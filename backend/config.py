"""Configuration management for Whisper Studio."""

import os
import platform
from pathlib import Path


class Config:
    """Application configuration."""

    def __init__(self):
        # Backend folder is inside project root, so go up one level
        self.BASE_DIR = Path(__file__).parent.parent
        self.VENDOR_DIR = self.BASE_DIR / "vendor" / "whisper.cpp"
        self.UPLOAD_DIR = self.BASE_DIR / "uploads"
        self.OUTPUT_DIR = self.BASE_DIR / "outputs"
        self.STORE_DIR = self.BASE_DIR / "store"
        self.RECORDINGS_DIR = self.BASE_DIR / "recordings"
        self.JOBS_FILE = self.STORE_DIR / "jobs.json"

        # Create directories
        for dir_path in [self.UPLOAD_DIR, self.OUTPUT_DIR, self.STORE_DIR, self.RECORDINGS_DIR]:
            dir_path.mkdir(exist_ok=True)

        # Max upload size: 500 MB
        self.MAX_CONTENT_LENGTH = 500 * 1024 * 1024

        # Job TTL (days) - 0 means keep forever
        self.JOB_TTL_DAYS = int(os.environ.get("JOB_TTL_DAYS", "30"))

        # Platform detection
        self.IS_MACOS = platform.system() == "Darwin"

        # Whisper binary and model
        self.WHISPER_BIN = self._find_whisper_bin()
        self.WHISPER_MODEL = self._find_model()

        # AI configuration
        self.AI_BASE_URL = os.environ.get("AI_BASE_URL", "")
        self.AI_API_KEY = os.environ.get("AI_API_KEY", "ollama")
        self.AI_MODEL = os.environ.get("AI_MODEL", "mistral")

        # Allowed file extensions
        self.ALLOWED_EXTENSIONS = {
            "mp3", "wav", "ogg", "flac", "m4a", "aac", "opus", "webm",
            "caf", "mp4", "mkv", "mov", "avi", "ts", "wmv", "m4v",
        }

    def _find_whisper_bin(self) -> str:
        """Locate whisper-cli binary."""
        candidates = [
            os.environ.get("WHISPER_BIN", ""),
            str(self.VENDOR_DIR / "build" / "bin" / "whisper-cli"),
            str(self.VENDOR_DIR / "build" / "bin" / "main"),
            str(self.VENDOR_DIR / "main"),
        ]
        for c in candidates:
            if c and Path(c).is_file() and os.access(c, os.X_OK):
                return c
        return ""

    def _find_model(self) -> str:
        """Locate Whisper model file."""
        if os.environ.get("WHISPER_MODEL"):
            return os.environ["WHISPER_MODEL"]

        model_names = [
            "ggml-base.en.bin", "ggml-base.bin",
            "ggml-small.en.bin", "ggml-small.bin",
            "ggml-medium.en.bin", "ggml-large-v3.bin",
        ]

        for name in model_names:
            p = self.VENDOR_DIR / "models" / name
            if p.exists():
                return str(p)
        return ""

    def allowed_file(self, filename: str) -> bool:
        """Check if file extension is allowed."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return ext in self.ALLOWED_EXTENSIONS

    def file_extension(self, filename: str) -> str:
        """Get file extension."""
        return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    def print_status(self):
        """Print configuration status."""
        print(f"  Binary : {self.WHISPER_BIN or '⚠ not found — run ./setup.sh'}")
        print(f"  Model  : {self.WHISPER_MODEL or '⚠ not found — run ./setup.sh'}")
        print(f"  AI     : {self.AI_BASE_URL or 'disabled (set AI_BASE_URL in .env)'}")
        print()
