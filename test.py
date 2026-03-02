"""
testing_app.py

Example script that:
  1. Takes a recorded audio file as input
  2. Converts & transcribes it via TranscriptionService
  3. Runs AI post-processing via AIProcessing
  4. Saves both the raw transcript and the AI result to the SQLite DB via jobs.py

Usage:
    python testing_app.py <path_to_audio_file> [--mode journal]
"""

import argparse
import sys
import uuid
from pathlib import Path
from dataclasses import dataclass

# ── Local imports (assumes this script lives alongside the module files) ──────
from backend.transcription import TranscriptionService
from backend.ai_service import AIProcessing
from backend.jobs import create_tables, create_record
from dotenv import load_dotenv
import os

load_dotenv()


# ── Minimal config object expected by TranscriptionService ────────────────────
@dataclass
class Config:
    WHISPER_BIN: str = os.getenv("WHISPER_BIN", "")  # path to whisper-cli binary
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "")  # path to .bin model file
    UPLOAD_DIR: Path = Path("uploads")  # temp dir for WAV conversion
    OUTPUT_DIR: Path = Path("outputs")  # temp dir for whisper txt output


def progress_callback(info: dict | int):
    """Simple progress printer — handles both int and dict style callbacks."""
    if isinstance(info, int):
        print(f"  [progress] {info}%")
    elif isinstance(info, dict):
        stage = info.get("stage", "")
        pct = info.get("pct", "")
        msg = info.get("message", "")
        seg = info.get("text_segment", "")
        if seg:
            print(f"  [live] {seg}")
        else:
            print(f"  [{stage}] {pct}% — {msg}")


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio and run AI post-processing."
    )
    parser.add_argument(
        "audio_file", help="Path to the recorded audio file (mp3, webm, wav, …)"
    )
    parser.add_argument(
        "--mode",
        default="journal",
        help="AI processing mode defined in prompts.toml (default: journal)",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio_file)
    if not audio_path.exists():
        print(f"ERROR: Audio file not found: {audio_path}")
        sys.exit(1)

    # ── Setup ─────────────────────────────────────────────────────────────────
    cfg = Config(
        UPLOAD_DIR=Path("tmp_uploads"),
        OUTPUT_DIR=Path("tmp_outputs"),
    )
    cfg.UPLOAD_DIR.mkdir(exist_ok=True)
    cfg.OUTPUT_DIR.mkdir(exist_ok=True)

    create_tables()  # ensure DB schema exists

    job_id = uuid.uuid4().hex
    print(f"\n── Job ID: {job_id[:8]} ──────────────────────────────────────────")

    # ── Step 1: Transcribe ────────────────────────────────────────────────────
    print("\n[1/3] Transcribing audio…")
    service = TranscriptionService(config=cfg)

    status, result = service.process(
        src=audio_path,
        job_id=job_id,
        on_progress=progress_callback,
    )

    if status != "done":
        print(f"\nTranscription failed or was cancelled: {result}")
        sys.exit(1)

    raw_transcript = result
    print(f"\n── Raw transcript ({len(raw_transcript)} chars) ──")
    print(raw_transcript)

    # ── Step 2: AI Post-processing ────────────────────────────────────────────
    print(f"\n[2/3] Running AI post-processing (mode='{args.mode}')…")
    ai = AIProcessing()

    try:
        ai_result = ai.process_prompts(mode=args.mode, text=raw_transcript)
    except Exception as e:
        print(f"\nAI processing failed: {e}")
        print("Saving raw transcript only.")
        ai_result = None

    if ai_result:
        print(f"\n── AI result ({len(ai_result)} chars) ──")
        print(ai_result)

    # ── Step 3: Save to DB ────────────────────────────────────────────────────
    print("\n[3/3] Saving to database…")
    record = create_record(transcription=raw_transcript, llm_result=ai_result)
    print(record.transcription, record.llm_result)
    print(f"Saved — record ID: {record.id}  timestamp: {record.timestamp}")
    print("\nDone! ✓")


if __name__ == "__main__":
    main()
