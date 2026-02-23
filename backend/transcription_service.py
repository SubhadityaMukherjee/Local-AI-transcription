"""Audio transcription service using whisper.cpp."""

import os
import subprocess
from pathlib import Path
from typing import Callable, Optional


class TranscriptionService:
    """Service for converting audio and transcribing with Whisper."""

    def __init__(self, config):
        self.config = config

    def convert_to_wav(self, src: Path, dst: Path):
        """Convert audio file to WAV format and strip silences."""
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", str(src),
                "-af", "silenceremove=1:0:-50dB", # Skip silence at start and internal pauses
                "-ar", "16000",
                "-ac", "1",
                "-sample_fmt", "s16",
                "-f", "wav",
                str(dst),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-800:]}")

    def transcribe(
        self, wav: Path, job_id: str, progress_callback: Callable[[dict], None] = None
    ):
        """Transcribe audio file using Whisper with flicker-reduction logic."""
        if not self.config.WHISPER_BIN:
            raise RuntimeError("whisper-cli binary not found — run ./setup.sh")
        if not self.config.WHISPER_MODEL:
            raise RuntimeError("No Whisper model found — run ./setup.sh")

        # Define output path and threading (The missing lines!)
        out_stem = str(self.config.OUTPUT_DIR / job_id)
        threads = str(max(4, os.cpu_count() or 4))

        cmd = [
            self.config.WHISPER_BIN,
            "--model", self.config.WHISPER_MODEL,
            "--file", str(wav),
            "--output-txt",
            "--output-file", out_stem,
            "--print-progress",
            "--threads", threads,
        ]

        # Capture STDOUT/STDERR combined to catch both progress and text segments
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )

        last_pct = -1
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            print(f"[whisper] {line}") # Keep server logs active

            # 1. Parse Progress (flicker-free)
            if "progress =" in line:
                try:
                    pct_str = line.split("=")[-1].replace("%", "").strip()
                    pct = int(float(pct_str))
                    # Only trigger callback if percentage actually moved up
                    if pct > last_pct:
                        last_pct = pct
                        if progress_callback:
                            progress_callback({
                                "stage": "transcribing",
                                "pct": 30 + int(pct * 0.65),
                                "message": f"Transcribing... {pct}%"
                            })
                except (ValueError, IndexError):
                    pass

            # 2. Parse Live Text Segments (the "Smooth Printing" fix)
            elif "-->" in line and "]" in line:
                try:
                    text_part = line.split("]")[-1].strip()
                    if text_part and progress_callback:
                        progress_callback({
                            "stage": "transcribing",
                            "pct": 30 + int(max(0, last_pct) * 0.65),
                            "text_segment": text_part
                        })
                except Exception:
                    pass

        proc.wait()
        
        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli exited with code {proc.returncode}")

        # Cleanup and return final text
        txt_path = Path(out_stem + ".txt")
        if txt_path.exists():
            text = txt_path.read_text().strip()
            txt_path.unlink(missing_ok=True)
            # Clean up extra newlines for a clean paragraph
            text = " ".join(l.strip() for l in text.split("\n") if l.strip())
            return text

        raise RuntimeError("whisper-cli produced no output file")

    def process(
        self, src: Path, job_id: str, on_progress: Callable[[int], None] = None
    ):
        """
        Process a complete transcription job.

        Args:
            src: Source audio file path
            job_id: Job identifier
            on_progress: Progress callback

        Returns:
            Tuple of (status, transcript_or_error)
        """
        wav = self.config.UPLOAD_DIR / f"{job_id}.wav"

        try:
            print(
                f"[job:{job_id[:8]}] converting {src.name} ({src.stat().st_size} bytes)"
            )
            if on_progress:
                on_progress(5)
            self.convert_to_wav(src, wav)
            print(
                f"[job:{job_id[:8]}] converted → {wav.name} ({wav.stat().st_size} bytes)"
            )

            if on_progress:
                on_progress(30)
            print(f"[job:{job_id[:8]}] transcribing…")

            def update_progress(pct):
                if on_progress:
                    on_progress(pct)

            text = self.transcribe(wav, job_id, update_progress)
            print(f"[job:{job_id[:8]}] done — {len(text)} chars")

            if on_progress:
                on_progress(100)

            return "done", text

        except Exception as e:
            import traceback

            print(f"[job:{job_id[:8]}] ERROR: {e}")
            traceback.print_exc()
            return "error", str(e)

        finally:
            src.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)
