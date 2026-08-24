from datetime import datetime, timezone

from app.main import app, db, diagnostic_request, event_playback
from app.models import PlaybackTest
from fastapi.testclient import TestClient


def test_health_and_secret_free_config():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        text = client.get("/api/config").text
        assert "fixture-password" not in text and "fixture-user" not in text


def test_api_validation_and_cross_origin():
    with TestClient(app) as client:
        assert client.get("/api/events", params={"date": "bad"}).status_code == 422
        assert client.put("/api/channels", json={}, headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_event_and_range_serving(tmp_path):
    event_id = db.transition(1, "binary_sensor.motion", "off", "on", datetime.now(timezone.utc).isoformat(), {}, 5, 10, "utc", 0, 5)
    db.transition(1, "binary_sensor.motion", "on", "off", datetime.now(timezone.utc).isoformat(), {}, 5, 10, "utc", 0, 5)
    media_dir = __import__("app.main", fromlist=["media"]).media.videos
    (media_dir / f"{event_id}.mp4").write_bytes(b"0123456789")
    db.update(event_id, video_status="ready", video_name=f"{event_id}.mp4", video_size=10)
    with TestClient(app) as client:
        r = client.get(f"/api/events/{event_id}/video", headers={"range": "bytes=2-5"})
        assert r.status_code == 206 and r.content == b"2345" and r.headers["accept-ranges"] == "bytes"


def test_ingress_relative_assets():
    with TestClient(app) as client:
        html = client.get("/diagnostics").text
        assert 'href="static/app.css"' in html and 'src="static/app.js"' in html


def test_timeline_date_uses_london_local_day():
    db.transition(8, "binary_sensor.local_day_test", "off", "on", "2026-08-23T23:30:00Z", {}, 5, 10, "utc", 0, 5)
    db.transition(8, "binary_sensor.local_day_test", "on", "off", "2026-08-23T23:31:00Z", {}, 5, 10, "utc", 0, 5)
    with TestClient(app) as client:
        result = client.get("/api/events", params={"date": "2026-08-24", "channel_id": 8}).json()
    assert "2026-08-23T23:30:00+00:00" in {item["started_at"] for item in result["items"]}


def test_historical_diagnostics_force_main_track_when_sub_requested():
    test = PlaybackTest(channel_id=1, start=datetime(2026, 8, 22, 7, 23, tzinfo=timezone.utc),
                        duration_seconds=15, stream="sub")
    request = diagnostic_request(test)
    assert "/Streaming/tracks/101?" in request.redacted_url
    assert "/Streaming/tracks/102?" not in request.redacted_url

    with TestClient(app) as client:
        result = client.post("/api/diagnostics/playback-url", json=test.model_dump(mode="json")).json()
    assert "/Streaming/tracks/101?" in result["redacted_url"]
    assert result["playback_stream"] == "main"


def test_corrupt_event_range_gets_bounded_playback_fallback():
    request = event_playback({"id": "missing-test-event", "channel_id": 1,
                              "started_at": "2026-08-23T10:00:00+00:00",
                              "ended_at": "2026-08-23T09:59:00+00:00", "timestamp_mode": "utc",
                              "applied_offset": 0, "pre_roll": 5, "post_roll": 10})
    assert (request.end_utc - request.start_utc).total_seconds() == 45
