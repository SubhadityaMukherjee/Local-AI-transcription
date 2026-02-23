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
        """Convert audio file to WAV format."""
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                "-f",
                "wav",
                str(dst),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-800:]}")

    def transcribe(
        self, wav: Path, job_id: str, progress_callback: Callable[[int], None] = None
    ):
        """
        Transcribe audio file using Whisper.

        Args:
            wav: Path to WAV file
            job_id: Job identifier for output naming
            progress_callback: Optional callback for progress updates (0-100)
                             Can also receive dict with detailed progress info

        Returns:
            Transcribed text
        """
        if not self.config.WHISPER_BIN:
            raise RuntimeError("whisper-cli binary not found — run ./setup.sh")
        if not self.config.WHISPER_MODEL:
            raise RuntimeError("No Whisper model found — run ./setup.sh")

        out_stem = str(self.config.OUTPUT_DIR / job_id)
        threads = str(max(4, os.cpu_count() or 4))

        cmd = [
            self.config.WHISPER_BIN,
            "--model",
            self.config.WHISPER_MODEL,
            "--file",
            str(wav),
            "--output-txt",
            "--output-file",
            out_stem,
            "--print-progress",
            "--threads",
            threads,
        ]

        stderr_lines = []
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        # Track detailed progress info
        progress_info = {
            "stage": "transcribing",
            "pct": 0,
            "prompt": "",
            "tokens": 0,
        }

        for line in proc.stderr:
            stderr_lines.append(line)
            print(f"[whisper] {line}", end="", flush=True)

            # Parse detailed progress from whisper output
            line_lower = line.lower()

            # Extract overall progress percentage
            if "progress" in line_lower and "%" in line:
                try:
                    # Try various formats: "progress: 50%", "50.5%"
                    pct_str = line.split("%")[0]
                    if "=" in pct_str:
                        pct = int(pct_str.split("=")[-1].strip())
                    else:
                        pct = int(float(pct_str.split()[-1]))
                    progress_info["pct"] = pct

                    if progress_callback:
                        # Send progress info
                        progress_callback(30 + int(pct * 0.65))
                except (ValueError, IndexError):
                    pass

            # Extract prompt/text info if available
            if "prompt:" in line_lower or "generation" in line_lower:
                # Whisper sometimes outputs partial text info
                if progress_callback:
                    progress_callback(int(30 + progress_info["pct"] * 0.65))

        proc.wait()
        full_stderr = "".join(stderr_lines)

        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli exited {proc.returncode}:\n{full_stderr}")

        txt = Path(out_stem + ".txt")
        if txt.exists():
            text = txt.read_text().strip()
            txt.unlink(missing_ok=True)
            # Join sentences that were split on newlines
            text = " ".join(line.strip() for line in text.split("\n") if line.strip())
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
