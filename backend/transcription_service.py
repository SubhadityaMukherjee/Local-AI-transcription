"""Audio transcription service using whisper.cpp."""

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional
import tempfile
import shutil
import wave


class JobCancelledError(Exception):
    """Raised when a transcription job is cancelled by the user."""


class TranscriptionService:
    """Service for converting audio and transcribing with Whisper."""

    def __init__(self, config):
        self.config = config

    def _get_duration(self, wav: Path) -> float:
        """Return duration of a WAV file in seconds."""
        with wave.open(str(wav), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
        return frames / float(rate)

    def _split_wav(self, wav: Path, max_secs: int = 300) -> list[Path]:
        """Split a WAV file into chunks no longer than *max_secs*.

        Returns a list of temporary chunk paths.  Caller is responsible for
        cleaning up the directory.
        """
        tmpdir = Path(tempfile.mkdtemp(prefix="wavchunks-"))
        pattern = str(tmpdir / "chunk%04d.wav")
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(wav),
                "-f",
                "segment",
                "-segment_time",
                str(max_secs),
                "-c",
                "copy",
                pattern,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return sorted(tmpdir.glob("chunk*.wav"))

    def _run_whisper(
        self,
        wav: Path,
        out_stem: str,
        progress_callback: Callable[[dict], None] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        """Run whisper-cli on *wav* and return deduped text."""
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
            # "--print-progress",
            "--threads",
            threads,
            "--entropy-thold",
            "2.0",
            "--temperature",
            "0",
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )

        last_pct = -1
        last_segment = ""

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

            if "progress =" in line:
                try:
                    pct_str = line.split("=")[-1].replace("%", "").strip()
                    pct = int(float(pct_str))
                    if pct > last_pct and progress_callback:
                        last_pct = pct
                        progress_callback(
                            {
                                "stage": "transcribing",
                                "pct": 30 + int(pct * 0.65),
                                "message": f"Transcribing... {pct}%",
                            }
                        )
                except (ValueError, IndexError):
                    pass
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

        txt_path = Path(out_stem + ".txt")
        if not txt_path.exists():
            raise RuntimeError("whisper-cli produced no output file")

        text = txt_path.read_text().strip()
        txt_path.unlink(missing_ok=True)

        # dedupe
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        seen = set()
        deduped = []
        for line in lines:
            sig = line.lower().replace(" ", "").replace(".", "").replace(",", "")
            if sig not in seen:
                seen.add(sig)
                deduped.append(line)

        return " ".join(deduped)

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
        """Transcribe audio file with optional chunking and deduplication."""
        if not self.config.WHISPER_BIN:
            raise RuntimeError("whisper-cli binary not found — run ./setup.sh")
        if not self.config.WHISPER_MODEL:
            raise RuntimeError("No Whisper model found — run ./setup.sh")

        # split long audio
        duration = self._get_duration(wav)
        if duration > getattr(self.config, "CHUNK_THRESHOLD", 300):
            chunks = self._split_wav(wav, max_secs=getattr(self.config, "CHUNK_THRESHOLD", 300))
        else:
            chunks = [wav]

        all_texts = []
        n = len(chunks)
        try:
            for idx, chunk in enumerate(chunks):
                stem = f"{job_id}-{idx}"

                # wrap the incoming callback so that each chunk's percent is
                # scaled into the overall 30‑95 range.  the first chunk starts
                # at 30, the last chunk ends at 95, and intermediate chunks are
                # evenly spaced.  we also propagate other fields (text_segment,
                # message) transparently.
                def make_cb(i):
                    def cb(p):
                        if not progress_callback:
                            return
                        p2 = p.copy()
                        if "pct" in p2:
                            base = 30 + int(65 * i / n)
                            span = int(65 / n)
                            p2["pct"] = base + int(p2["pct"] * span / 100)
                        progress_callback(p2)
                    return cb

                wrapped_cb = make_cb(idx)

                text = self._run_whisper(chunk, str(self.config.OUTPUT_DIR / stem), wrapped_cb, cancel_event)
                all_texts.append(text)
                if cancel_event and cancel_event.is_set():
                    raise JobCancelledError("cancelled")
        finally:
            if len(chunks) > 1:
                shutil.rmtree(chunks[0].parent, ignore_errors=True)

        lines = [l.strip() for t in all_texts for l in t.splitlines() if l.strip()]
        seen = set()
        deduped = []
        for line in lines:
            sig = line.lower().replace(" ", "").replace(".", "").replace(",", "")
            if sig not in seen:
                seen.add(sig)
                deduped.append(line)

        return " ".join(deduped)

    def process(
        self, src: Path, job_id: str, on_progress: Callable[[int], None] = None
    ):
        """Process a complete transcription job.

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
