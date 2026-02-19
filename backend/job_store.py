"""Job management with in-memory storage and disk persistence."""

import json
import threading
import time
import uuid
from typing import Optional


class JobStore:
    """Thread-safe job storage with persistence."""

    def __init__(self, config):
        self.config = config
        self.jobs: dict = {}
        self._lock = threading.Lock()

    def load_from_disk(self):
        """Load persisted jobs from disk on startup."""
        jobs_file = self.config.JOBS_FILE
        if not jobs_file.exists():
            return

        try:
            data = json.loads(jobs_file.read_text())
            cutoff = time.time() - self.config.JOB_TTL_DAYS * 86400 if self.config.JOB_TTL_DAYS > 0 else 0
            kept = 0

            for jid, j in data.items():
                if cutoff and j.get("created_at", 0) < cutoff:
                    continue
                self.jobs[jid] = j
                kept += 1

            print(f"  Loaded {kept} jobs from store (TTL={self.config.JOB_TTL_DAYS}d)")
        except Exception as e:
            print(f"  Warning: could not load jobs store: {e}")

    def save_to_disk(self):
        """Persist completed/errored jobs to disk."""
        with self._lock:
            saveable = {
                jid: {k: v for k, v in j.items() if k != "debug_log"}
                for jid, j in self.jobs.items()
                if j["status"] in ("done", "error")
            }

        try:
            tmp = self.config.JOBS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(saveable, indent=2))
            tmp.replace(self.config.JOBS_FILE)
        except Exception as e:
            print(f"  Warning: could not persist jobs: {e}")

    def create(self, filename: str) -> dict:
        """Create a new job."""
        job = {
            "id": str(uuid.uuid4()),
            "filename": filename,
            "status": "queued",
            "progress": 0,
            "transcript": None,
            "error": None,
            "created_at": time.time(),
        }
        with self._lock:
            self.jobs[job["id"]] = job
        return job

    def get(self, job_id: str) -> Optional[dict]:
        """Get job by ID."""
        return self.jobs.get(job_id)

    def update(self, job_id: str, **kwargs):
        """Update job fields."""
        should_persist = False
        with self._lock:
            j = self.jobs.get(job_id)
            if j:
                j.update(kwargs)
                if kwargs.get("status") in ("done", "error"):
                    should_persist = True

        if should_persist:
            self.save_to_disk()

    def delete(self, job_id: str) -> bool:
        """Delete a job."""
        with self._lock:
            if job_id not in self.jobs:
                return False
            del self.jobs[job_id]
        self.save_to_disk()
        return True

    def list_all(self, limit: int = 50) -> list:
        """List jobs sorted by creation date (most recent first)."""
        with self._lock:
            sorted_jobs = sorted(
                self.jobs.values(),
                key=lambda j: j["created_at"],
                reverse=True
            )
            return sorted_jobs[:limit]

    def clear_completed(self) -> int:
        """Delete all completed/errored jobs."""
        with self._lock:
            to_remove = [jid for jid, j in self.jobs.items() if j["status"] in ("done", "error")]
            for jid in to_remove:
                del self.jobs[jid]
        self.save_to_disk()
        return len(to_remove)

    def add_ai_result(self, job_id: str, mode: str, text: str):
        """Add AI result to job."""
        with self._lock:
            j = self.jobs.get(job_id)
            if j:
                if "ai_results" not in j:
                    j["ai_results"] = []
                j["ai_results"].append({
                    "mode": mode,
                    "text": text,
                    "created_at": time.time(),
                })
        self.save_to_disk()

    def delete_ai_result(self, job_id: str, idx: int) -> bool:
        """Delete AI result at index."""
        with self._lock:
            j = self.jobs.get(job_id)
            if not j:
                return False
            ai_results = j.get("ai_results", [])
            if idx < 0 or idx >= len(ai_results):
                return False
            ai_results.pop(idx)
            j["ai_results"] = ai_results
        self.save_to_disk()
        return True
