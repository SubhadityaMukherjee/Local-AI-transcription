"""SQLAlchemy-backed job store (drop-in replacement for the JSON-based JobStore).

Supports any SQLAlchemy-compatible database:
  - MySQL:      mysql+pymysql://user:pass@host/dbname
  - PostgreSQL: postgresql+psycopg2://user:pass@host/dbname
  - SQLite:     sqlite:///path/to/whisper.db
  - and more

Set the DATABASE_URL environment variable (or config.DATABASE_URL).
"""

import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import (JSON, Column, Float, Integer, String, Text,
                        create_engine, text)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger("whisper_cli.job_store")
RECORDINGS_DIR = Path(__file__).parent / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

# ── ORM model ──────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


class JobModel(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True)
    filename = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    transcript = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(Float, nullable=False)
    ai_results = Column(JSON, nullable=True, default=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "progress": self.progress,
            "transcript": self.transcript,
            "error": self.error,
            "created_at": self.created_at,
            "ai_results": self.ai_results or [],
        }


# ── Store ──────────────────────────────────────────────────────────────────────


class SQLJobStore:
    """Job store backed by SQLAlchemy (MySQL, PostgreSQL, SQLite, …)."""

    def __init__(self, config):
        self.config = config
        logger.debug("Initializing SQLJobStore")

        url = getattr(config, "DATABASE_URL", None)
        if not url:
            import os

            url = os.environ.get("DATABASE_URL", "sqlite:///whisper.db")

        logger.info("Connecting to database: %s", _redact(url))

        connect_args = {}
        if url.startswith("sqlite"):
            # SQLite needs check_same_thread=False for multi-threaded Flask/CLI use
            connect_args["check_same_thread"] = False
            logger.debug("SQLite mode: disabling same-thread check for multi-threading")

        self._engine = create_engine(
            url,
            pool_pre_ping=True,  # detect stale connections
            pool_recycle=1800,  # recycle connections every 30 min
            connect_args=connect_args,
            echo=False,  # set True to log all SQL (very verbose)
        )
        self._Session = sessionmaker(bind=self._engine, expire_on_commit=False)
        logger.debug("SQLAlchemy engine created")
        self._ensure_schema()
        logger.info("SQLJobStore ready")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_schema(self):
        logger.debug("Ensuring database schema...")
        Base.metadata.create_all(self._engine)
        logger.debug("Schema verified / created")

    def _session(self) -> Session:
        return self._Session()

    # ------------------------------------------------------------------
    # Public API — mirrors JobStore exactly
    # ------------------------------------------------------------------

    def load_from_disk(self):
        """No-op: the DB is always-on. Prunes expired jobs on startup."""
        ttl = getattr(self.config, "JOB_TTL_DAYS", 30)
        if ttl > 0:
            cutoff = time.time() - ttl * 86400
            with self._session() as s:
                deleted = (
                    s.query(JobModel)
                    .filter(JobModel.created_at < cutoff)
                    .delete(synchronize_session=False)
                )
                s.commit()
            if deleted:
                logger.info("Pruned %d expired jobs (TTL=%dd)", deleted, ttl)
        logger.info("SQLAlchemy store ready")

    def save_to_disk(self):
        """No-op: SQLAlchemy commits immediately after every mutation."""
        pass

    def create(self, filename: str) -> dict:
        # Convert input filename to Path object
        input_path = Path(filename)

        # Create safe filename for storage (strip path, sanitize)
        safe_filename = input_path.name
        stored_path = RECORDINGS_DIR / safe_filename

        # Copy original file to recordings/ directory
        if input_path.is_file():
            shutil.copy2(input_path, stored_path)
            stored_filename = str(stored_path)
            logger.info("Copied %s -> %s", input_path, stored_path)
        else:
            # If input doesn't exist, store what was passed (for compatibility)
            stored_filename = filename
            logger.warning(
                "Input file %s not found, storing original filename", filename
            )

        # Create job with stored path
        job = JobModel(
            id=str(uuid.uuid4()),
            filename=str(stored_filename),  # Now points to recordings/
            status="queued",
            progress=0,
            transcript=None,
            error=None,
            created_at=time.time(),
            ai_results=[],
        )
        with self._session() as s:
            s.add(job)
            s.commit()
            result = job.to_dict()
        logger.info("Created job %s for file '%s'", result["id"][:8], stored_filename)
        return result

    # Add helper method to get absolute stored path
    def get_recording_path(self, job_id: str) -> Optional[Path]:
        """Returns Path to actual recording file for a job."""
        job = self.get(job_id)
        if not job or not job["filename"]:
            logger.warning(
                "Cannot get recording path: job %s not found or has no filename",
                job_id[:8],
            )
            return None
        path = Path(job["filename"])
        logger.debug("Recording path for job %s: %s", job_id[:8], path)
        return path

    def get(self, job_id: str) -> Optional[dict]:
        logger.debug("Retrieving job: %s", job_id[:8])
        with self._session() as s:
            job = s.get(JobModel, job_id)
        if job is None:
            logger.debug("Job %s not found", job_id[:8])
            return None
        logger.debug(
            "Job %s retrieved (status=%s, progress=%d%%)",
            job_id[:8],
            job.status,
            job.progress,
        )
        return job.to_dict()

    def update(self, job_id: str, **kwargs):
        if not kwargs:
            return
        logger.debug("Updating job %s with: %s", job_id[:8], list(kwargs.keys()))
        with self._session() as s:
            job = s.get(JobModel, job_id)
            if job is None:
                logger.warning("update: job %s not found", job_id[:8])
                return
            for k, v in kwargs.items():
                setattr(job, k, v)
                logger.debug(
                    "  %s: %s",
                    k,
                    (
                        v
                        if k != "transcript"
                        else f"<{len(v)} chars>" if isinstance(v, str) else v
                    ),
                )
            s.commit()
        logger.debug("Updated job %s: %s", job_id[:8], list(kwargs.keys()))

    def delete(self, job_id: str) -> bool:
        with self._session() as s:
            job = s.get(JobModel, job_id)
            if job is None:
                return False
            s.delete(job)
            s.commit()
        logger.info("Deleted job %s", job_id[:8])
        return True

    def list_all(self, limit: int = 50) -> list:
        logger.debug("Listing all jobs (limit=%d)", limit)
        with self._session() as s:
            jobs = (
                s.query(JobModel)
                .order_by(JobModel.created_at.desc())
                .limit(limit)
                .all()
            )
            result = [j.to_dict() for j in jobs]
        logger.info("Retrieved %d jobs (from %d total)", len(result), len(result))
        return result

    def clear_completed(self) -> int:
        with self._session() as s:
            count = (
                s.query(JobModel)
                .filter(JobModel.status.in_(["done", "error"]))
                .delete(synchronize_session=False)
            )
            s.commit()
        logger.info("Cleared %d completed/errored jobs", count)
        return count

    def add_ai_result(self, job_id: str, mode: str, text: str):
        with self._session() as s:
            job = s.get(JobModel, job_id)
            if job is None:
                logger.warning("add_ai_result: job %s not found", job_id[:8])
                return
            results = list(job.ai_results or [])
            results.append({"mode": mode, "text": text, "created_at": time.time()})
            job.ai_results = results
            s.commit()
        logger.info("Added AI result (mode=%s) to job %s", mode, job_id[:8])

    def delete_ai_result(self, job_id: str, idx: int) -> bool:
        logger.debug("Deleting AI result #%d from job %s", idx, job_id[:8])
        with self._session() as s:
            job = s.get(JobModel, job_id)
            if job is None:
                logger.warning("delete_ai_result: job %s not found", job_id[:8])
                return False
            results = list(job.ai_results or [])
            if idx < 0 or idx >= len(results):
                logger.warning(
                    "delete_ai_result: invalid index %d (job has %d results)",
                    idx,
                    len(results),
                )
                return False
            deleted_mode = results[idx].get("mode", "unknown")
            results.pop(idx)
            job.ai_results = results
            s.commit()
        logger.info(
            "Deleted AI result #%d (mode=%s) from job %s", idx, deleted_mode, job_id[:8]
        )
        return True


# ── Utility ────────────────────────────────────────────────────────────────────


def _redact(url: str) -> str:
    """Hide password in a DB URL for safe logging."""
    try:
        from urllib.parse import urlparse, urlunparse

        p = urlparse(url)
        if p.password:
            netloc = p.netloc.replace(p.password, "***")
            return urlunparse(p._replace(netloc=netloc))
    except Exception:
        pass
    return url
