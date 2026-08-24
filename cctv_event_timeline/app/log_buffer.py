import logging
import threading
from collections import deque
from datetime import datetime, timezone

from .security import redact


class SanitizedLogBuffer(logging.Handler):
    def __init__(self, secrets: tuple[str, ...], capacity: int = 200):
        super().__init__()
        self.secrets = secrets
        self.records = deque(maxlen=capacity)
        self.lock = threading.RLock()

    def emit(self, record: logging.LogRecord):
        try:
            message = record.getMessage()
            if record.name == "uvicorn.access" and "/api/diagnostics/runtime" in message:
                return
            entry = {"timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                     "level": record.levelname, "logger": record.name[:80],
                     "message": redact(message, self.secrets)[:2000]}
            with self.lock:
                self.records.append(entry)
        except Exception:
            self.handleError(record)

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def truncate(self):
        with self.lock:
            self.records.clear()
