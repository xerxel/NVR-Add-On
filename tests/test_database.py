import sqlite3

from app.database import Database


def test_pair_duplicate_and_merge(tmp_path):
    db = Database(tmp_path / "events.db")
    first = db.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T10:00:00Z", {}, 5, 10, "utc", 0, 5)
    assert first
    assert db.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T10:00:00Z", {}, 5, 10, "utc", 0, 5) is None
    assert db.transition(1, "binary_sensor.motion", "on", "off", "2026-08-23T10:00:10Z", {}, 5, 10, "utc", 0, 5) == first
    merged = db.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T10:00:14Z", {}, 5, 10, "utc", 0, 5)
    assert merged == first
    db.transition(1, "binary_sensor.motion", "on", "off", "2026-08-23T10:00:20Z", {}, 5, 10, "utc", 0, 5)
    assert db.event(first)["ended_at"] == "2026-08-23T10:00:20+00:00"


def test_missing_clear_is_ignored(tmp_path):
    db = Database(tmp_path / "events.db")
    assert db.transition(1, "binary_sensor.motion", "on", "off", "2026-08-23T10:00:00Z", {}, 5, 10, "utc", 0, 5) is None


def test_session_closes_connection_on_success_and_error(tmp_path, monkeypatch):
    database = Database(tmp_path / "events.db")

    class SpyConnection:
        def __init__(self):
            self.connection = sqlite3.connect(database.path)
            self.closed = False

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def close(self):
            self.closed = True
            self.connection.close()

    successful = SpyConnection()
    monkeypatch.setattr(database, "connect", lambda: successful)
    with database.session():
        pass
    assert successful.closed

    failing = SpyConnection()
    monkeypatch.setattr(database, "connect", lambda: failing)
    try:
        with database.session():
            raise RuntimeError("test failure")
    except RuntimeError:
        pass
    assert failing.closed


def test_completed_duplicate_is_not_reopened(tmp_path):
    database = Database(tmp_path / "events.db")
    event_id = database.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T10:00:00Z", {}, 5, 10, "utc", 0, 5)
    database.transition(1, "binary_sensor.motion", "on", "off", "2026-08-23T10:00:10Z", {}, 5, 10, "utc", 0, 5)

    assert database.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T10:00:00Z", {}, 5, 10, "utc", 0, 5) is None
    assert database.transition(1, "binary_sensor.motion", "on", "off", "2026-08-23T10:00:10Z", {}, 5, 10, "utc", 0, 5) is None
    assert database.event(event_id)["ended_at"] == "2026-08-23T10:00:10+00:00"


def test_older_transition_is_not_merged_into_newer_event(tmp_path):
    database = Database(tmp_path / "events.db")
    newer = database.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T10:00:00Z", {}, 5, 10, "utc", 0, 5)
    database.transition(1, "binary_sensor.motion", "on", "off", "2026-08-23T10:00:10Z", {}, 5, 10, "utc", 0, 5)

    older = database.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T09:00:00Z", {}, 5, 10, "utc", 0, 5)
    assert older and older != newer


def test_clear_before_start_does_not_corrupt_event(tmp_path):
    database = Database(tmp_path / "events.db")
    event_id = database.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T10:00:00Z", {}, 5, 10, "utc", 0, 5)
    assert database.transition(1, "binary_sensor.motion", "on", "off", "2026-08-23T09:59:00Z", {}, 5, 10, "utc", 0, 5) is None
    assert database.event(event_id)["ended_at"] is None


def test_restart_recovers_interrupted_clip_and_pending_thumbnail(tmp_path):
    path = tmp_path / "events.db"
    database = Database(path)
    event_id = database.transition(1, "binary_sensor.motion", "off", "on", "2026-08-23T10:00:00Z", {}, 5, 10, "utc", 0, 5)
    database.transition(1, "binary_sensor.motion", "on", "off", "2026-08-23T10:00:10Z", {}, 5, 10, "utc", 0, 5)
    database.update(event_id, video_status="generating")

    restarted = Database(path)

    assert restarted.event(event_id)["video_status"] == "failed"
    assert "interrupted" in restarted.event(event_id)["last_error"]
    assert event_id in {row["id"] for row in restarted.pending_thumbnails()}


def test_pending_thumbnails_are_newest_first_and_limit_keeps_newest(tmp_path):
    database = Database(tmp_path / "events.db")
    event_ids = []
    for hour in (8, 10, 9):
        started = f"2026-08-23T{hour:02d}:00:00Z"
        ended = f"2026-08-23T{hour:02d}:00:10Z"
        event_id = database.transition(1, "binary_sensor.motion", "off", "on", started, {}, 5, 10, "utc", 0, 5)
        database.transition(1, "binary_sensor.motion", "on", "off", ended, {}, 5, 10, "utc", 0, 5)
        event_ids.append(event_id)

    pending = database.pending_thumbnails(limit=2)

    assert [row["id"] for row in pending] == [event_ids[1], event_ids[2]]
    assert [row["started_at"] for row in pending] == sorted(
        (row["started_at"] for row in pending), reverse=True,
    )
