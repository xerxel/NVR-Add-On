import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone


class TaskTracker:
    """In-memory, secret-free registry of currently running operations."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._lock = threading.RLock()

    @asynccontextmanager
    async def track(self, operation: str, details: dict | None = None):
        task_id = uuid.uuid4().hex[:12]
        record = {
            "id": task_id,
            "operation": operation,
            "phase": "starting",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "started_monotonic": time.monotonic(),
            "details": self._safe_details(details or {}),
        }
        with self._lock:
            self._tasks[task_id] = record
        try:
            yield task_id
        finally:
            with self._lock:
                self._tasks.pop(task_id, None)

    def update(self, task_id: str, *, phase: str | None = None, details: dict | None = None):
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return
            if phase:
                record["phase"] = phase[:80]
            if details:
                record["details"].update(self._safe_details(details))

    def snapshot(self) -> list[dict]:
        current = time.monotonic()
        with self._lock:
            records = list(self._tasks.values())
        return [{"id": record["id"], "operation": record["operation"], "phase": record["phase"],
                 "started_at": record["started_at"],
                 "duration_seconds": round(max(0, current - record["started_monotonic"]), 1),
                 "details": dict(record["details"])} for record in records]

    @staticmethod
    def _safe_details(details: dict) -> dict:
        allowed = {"event_id", "channel_id", "executable", "timeout_seconds", "requested_duration_seconds",
                   "source_codec", "pending_count"}
        return {key: value for key, value in details.items() if key in allowed and isinstance(value, (str, int, float, bool, type(None)))}
