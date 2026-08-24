import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, channel_id INTEGER NOT NULL, motion_entity TEXT NOT NULL,
 started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL DEFAULT 'open',
 context_json TEXT NOT NULL DEFAULT '{}', thumbnail_status TEXT NOT NULL DEFAULT 'pending',
 thumbnail_name TEXT, thumbnail_source TEXT, video_status TEXT NOT NULL DEFAULT 'uncached',
 video_name TEXT, video_size INTEGER, video_duration REAL, video_codec TEXT,
 pre_roll INTEGER NOT NULL, post_roll INTEGER NOT NULL, timestamp_mode TEXT NOT NULL,
 applied_offset INTEGER NOT NULL DEFAULT 0, last_error TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(channel_id, started_at)
);
CREATE INDEX IF NOT EXISTS ix_events_date ON events(started_at DESC);
CREATE INDEX IF NOT EXISTS ix_events_channel_date ON events(channel_id, started_at DESC);
CREATE TABLE IF NOT EXISTS open_motion (
 motion_entity TEXT PRIMARY KEY, channel_id INTEGER NOT NULL, started_at TEXT NOT NULL, context_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnostics (
 name TEXT PRIMARY KEY, result_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        with self.session() as db:
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def session(self):
        """Provide a transactional connection and always release its descriptor."""
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def transition(self, channel_id: int, entity: str, old: str | None, new: str,
                   timestamp: str, context: dict, pre: int, post: int, mode: str,
                   offset: int, merge_gap: int) -> str | None:
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        with self._lock, self.session() as db:
            if new == "on" and old != "on":
                existing = db.execute("SELECT started_at FROM open_motion WHERE motion_entity=?", (entity,)).fetchone()
                if existing:
                    return None
                event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cctv:{channel_id}:{timestamp}"))
                if db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
                    return None
                recent = db.execute("SELECT id,ended_at FROM events WHERE channel_id=? AND ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1", (channel_id,)).fetchone()
                gap = datetime.fromisoformat(timestamp) - datetime.fromisoformat(recent["ended_at"]) if recent else None
                if recent and timedelta(0) <= gap <= timedelta(seconds=merge_gap):
                    db.execute("UPDATE events SET ended_at=NULL,status='open',updated_at=? WHERE id=?", (now(), recent["id"]))
                    db.execute("INSERT OR REPLACE INTO open_motion VALUES(?,?,?,?)", (entity, channel_id, timestamp, json.dumps(context)))
                    return recent["id"]
                stamp = now()
                db.execute("INSERT OR IGNORE INTO events(id,channel_id,motion_entity,started_at,context_json,pre_roll,post_roll,timestamp_mode,applied_offset,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (event_id, channel_id, entity, timestamp, json.dumps(context), pre, post, mode, offset, stamp, stamp))
                db.execute("INSERT OR REPLACE INTO open_motion VALUES(?,?,?,?)", (entity, channel_id, timestamp, json.dumps(context)))
                return event_id
            if new == "off" and old == "on":
                opened = db.execute("SELECT * FROM open_motion WHERE motion_entity=?", (entity,)).fetchone()
                if not opened:
                    return None
                event = db.execute("SELECT id,started_at FROM events WHERE channel_id=? AND motion_entity=? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                                   (channel_id, entity)).fetchone()
                db.execute("DELETE FROM open_motion WHERE motion_entity=?", (entity,))
                if event:
                    if datetime.fromisoformat(timestamp) <= datetime.fromisoformat(event["started_at"]):
                        return None
                    db.execute("UPDATE events SET ended_at=?,status='finalising',updated_at=? WHERE id=?", (timestamp, now(), event["id"]))
                    return event["id"]
        return None

    def list_events(self, start: str, end: str, channel: int | None, status: str | None, limit: int, offset: int):
        query = "SELECT * FROM events WHERE started_at>=? AND started_at<?"
        args: list = [start, end]
        if channel:
            query += " AND channel_id=?"; args.append(channel)
        if status:
            query += " AND status=?"; args.append(status)
        query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"; args.extend([limit, offset])
        with self.session() as db:
            return [dict(x) for x in db.execute(query, args)]

    def event(self, event_id: str):
        with self.session() as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            return dict(row) if row else None

    def update(self, event_id: str, **values):
        allowed = {"status", "thumbnail_status", "thumbnail_name", "thumbnail_source", "video_status",
                   "video_name", "video_size", "video_duration", "video_codec", "last_error", "retry_count"}
        values = {k: v for k, v in values.items() if k in allowed}
        values["updated_at"] = now()
        with self.session() as db:
            db.execute(f"UPDATE events SET {','.join(f'{k}=?' for k in values)} WHERE id=?", [*values.values(), event_id])

    def store_diagnostic(self, name: str, result: dict):
        with self.session() as db:
            db.execute("INSERT OR REPLACE INTO diagnostics VALUES(?,?,?)", (name, json.dumps(result), now()))

    def diagnostics(self):
        with self.session() as db:
            return [{"name": r["name"], "result": json.loads(r["result_json"]), "updated_at": r["updated_at"]}
                    for r in db.execute("SELECT * FROM diagnostics ORDER BY updated_at DESC")]
