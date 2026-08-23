from datetime import datetime, timezone

from app.main import app, db
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
