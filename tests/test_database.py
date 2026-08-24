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
