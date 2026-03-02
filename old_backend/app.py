"""
Whisper Studio — Gradio frontend
Transcription via whisper.cpp (submodule at vendor/whisper.cpp),
built with CoreML + Metal for macOS acceleration.
"""

import platform
import multiprocessing

# Must be set before any other multiprocessing imports on macOS
if platform.system() == "Darwin":
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

from backend.config import Config
from backend.job_store import JobStore
from backend.transcription_service import TranscriptionService
from backend.ai_service import AIService
from backend.gradio_app import build_app

config = Config()
job_store = JobStore(config)
transcription_service = TranscriptionService(config)
ai_service = AIService(config)

app = build_app(config, job_store, transcription_service, ai_service)

if __name__ == "__main__":
    job_store.load_from_disk()
    config.print_status()
    app.launch(server_name="0.0.0.0", server_port=5343)
