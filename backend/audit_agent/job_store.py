from __future__ import annotations

import threading
from datetime import datetime
from uuid import uuid4


class JobStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def create(self, **kwargs) -> dict:
        with self._lock:
            job_id = uuid4().hex[:12]
            job = {
                "id": job_id,
                "status": "queued",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "logs": [],
                "items": [],
                **kwargs,
            }
            self._jobs[job_id] = job
            return job

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)

    def update(self, job_id: str, **kwargs) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(kwargs)
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["logs"].append({
                "time": datetime.now().isoformat(timespec="seconds"),
                "message": message,
            })
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")


job_store = JobStore()
