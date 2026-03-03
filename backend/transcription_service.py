"""Audio transcription service using whisper.cpp."""

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional


class JobCancelledError(Exception):
    """Raised when a transcription job is cancelled by the user."""


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
                "-i",
                str(src),
                "-af",
                "silenceremove=1:0:-50dB",  # Skip silence at start and internal pauses
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
        self,
        wav: Path,
        job_id: str,
        progress_callback: Callable[[dict], None] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        """Transcribe audio file using Whisper with flicker‑reduction and deduplication."""
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
            # Stability controls
            "--entropy-thold",
            "2.0",
            "--temperature",
            "0",
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )

        last_pct = -1
        last_segment = ""  # to avoid repeating the same text segment

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            if cancel_event and cancel_event.is_set():
                try:
                    proc.terminate()
                except Exception:
                    pass
                proc.wait()
                raise JobCancelledError("cancelled")

            print(f"[whisper] {line}")

            # 1. Parse progress
            if "progress =" in line:
                try:
                    pct_str = line.split("=")[-1].replace("%", "").strip()
                    pct = int(float(pct_str))
                    if pct > last_pct:
                        last_pct = pct
                        if progress_callback:
                            progress_callback(
                                {
                                    "stage": "transcribing",
                                    "pct": 30 + int(pct * 0.65),
                                    "message": f"Transcribing... {pct}%",
                                }
                            )
                except (ValueError, IndexError):
                    pass

            # 2. Parse live text segments, skip exact duplicates
            elif "-->" in line and "]" in line:
                try:
                    text_part = line.split("]")[-1].strip()
                    if text_part and progress_callback and text_part != last_segment:
                        last_segment = text_part
                        progress_callback(
                            {
                                "stage": "transcribing",
                                "pct": 30 + int(max(0, last_pct) * 0.65),
                                "text_segment": text_part,
                            }
                        )
                except Exception:
                    pass

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli exited with code {proc.returncode}")

        # 3. Read and deduplicate final transcript
        txt_path = Path(out_stem + ".txt")
        if not txt_path.exists():
            raise RuntimeError("whisper-cli produced no output file")

        text = txt_path.read_text().strip()
        txt_path.unlink(missing_ok=True)

        # Split, dedup by cleaned line, and join into one paragraph
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        seen = set()
        deduped = []
        for line in lines:
            sig = line.lower().replace(" ", "").replace(".", "").replace(",", "")
            if sig not in seen:
                seen.add(sig)
                deduped.append(line)

        text = " ".join(deduped)
        return text

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

            # on_progress is the callback; callers may pass a cancel_event in kwargs
            cancel_event = None
            # If caller provided a cancel_event through attribute on_progress (routes sets mapping),
            # it should pass it explicitly when calling process. We support an attribute on this
            # object (not ideal but keeps backward compatibility). Instead callers should pass
            # a keyword arg named 'cancel_event' when invoking process.
            # For now, inspect for an attribute set on self (not used normally) and default to None.
            # The routes layer will pass cancel_event via kwargs by updating this method signature
            # in-place; for now we accept passing via a reserved attribute set by caller.
            if hasattr(self, "_cancel_event"):
                cancel_event = getattr(self, "_cancel_event")

            text = self.transcribe(wav, job_id, update_progress, cancel_event)
            print(f"[job:{job_id[:8]}] done — {len(text)} chars")

            if on_progress:
                on_progress(100)

            return "done", text

        except JobCancelledError:
            return "cancelled", "cancelled by user"

        except Exception as e:
            import traceback

            print(f"[job:{job_id[:8]}] ERROR: {e}")
            traceback.print_exc()
            return "error", str(e)
