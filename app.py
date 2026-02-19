"""
Whisper Studio — Flask backend
Transcription via whisper.cpp (submodule at vendor/whisper.cpp),
built with CoreML + Metal for macOS acceleration.
"""

from config import Config
from job_store import JobStore
from transcription_service import TranscriptionService
from ai_service import AIService
from routes import register_routes

# Initialize core components
config = Config()
job_store = JobStore(config)
transcription_service = TranscriptionService(config)
ai_service = AIService(config)

# Create Flask app
app = __import__('flask').Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

# Register routes
register_routes(app, config, job_store, transcription_service, ai_service)

if __name__ == "__main__":
    job_store.load_from_disk()
    config.print_status()
    app.run(debug=True, host="0.0.0.0", port=5343, threaded=True)
