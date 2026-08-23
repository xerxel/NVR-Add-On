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

