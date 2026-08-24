from datetime import datetime, timezone

import app.main as main
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
        assert 'id="live-updates"' in html and 'id="truncate-logs"' in html
        assert 'id="video-failure"' in html and 'id="failure-details"' in html
        assert 'id="resource-usage"' in html and 'id="storage-bar"' in html
        assert 'id="addon-cpu"' in html and 'id="system-cpu"' in html
        assert 'id="vlc-settings"' in html and 'id="open-vlc"' in html and 'id="rtsp-url"' in html
        assert 'id="proxy-url"' in html and 'id="copy-proxy"' in html
        assert 'id="pause-thumbnail-refresh"' in html
        script = client.get("/static/app.js").text
        style = client.get("/static/app.css").text
        assert "codec-pill" in script and "stream_url" in script and "generation_details" in script
        assert "api/diagnostics/storage" in script and "api/diagnostics/cpu" in script
        assert "api/vlc-credentials" in script and "credential_free_rtsp_url" in script
        assert "Direct VLC handoff requested" in script and "codec-compare" in html
        assert "'H.265':'h265'" in script and ".codec-pill.h265{background:#7b1f2b}" in style
        assert "grid-template-columns:repeat(auto-fill,minmax(250px,1fr))" in style
        assert "aspect-ratio:32/9" in style and "object-fit:cover" in style


def test_runtime_diagnostics_and_log_truncation_are_secret_free():
    with TestClient(app) as client:
        runtime = client.get("/api/diagnostics/runtime")
        assert runtime.status_code == 200
        assert set(runtime.json()) == {"generated_at", "tasks", "logs"}
        assert "fixture-password" not in runtime.text and "fixture-user" not in runtime.text
        truncated = client.delete("/api/diagnostics/runtime/logs")
        assert truncated.status_code == 200
        assert truncated.json() == {"ok": True, "scope": "sanitised_in_memory_log_view"}


def test_thumbnail_refresh_can_be_paused_and_resumed():
    with TestClient(app) as client:
        paused = client.put("/api/diagnostics/thumbnail-refresh?paused=true")
        status = client.get("/api/diagnostics/thumbnail-refresh")
        resumed = client.put("/api/diagnostics/thumbnail-refresh?paused=false")

    assert paused.status_code == 200 and paused.json()["paused"] is True
    assert status.json()["paused"] is True
    assert resumed.status_code == 200 and resumed.json()["paused"] is False


def test_codec_compare_bypasses_cache_and_compares_live_with_historical(monkeypatch):
    async def fake_probe(url):
        codec = "h264" if "/Streaming/channels/" in url else "hevc"
        return {"streams": [{"codec_type": "video", "codec_name": codec, "profile": "Main",
                              "width": 1920, "height": 1080, "r_frame_rate": "25/1"}]}

    monkeypatch.setattr(main.media, "probe", fake_probe)
    body = {"channel_id": 7, "start": "2026-08-24T12:34:00Z", "duration_seconds": 15,
            "mode": "utc", "stream": "main"}
    with TestClient(app) as client:
        response = client.post("/api/diagnostics/codec-compare", json=body)

    result = response.json()
    assert response.status_code == 200
    assert result["live_main"]["codec_label"] == "H.264"
    assert result["historical_main"]["codec_label"] == "H.265"
    assert result["same_codec"] is False


def test_storage_and_cpu_diagnostics_are_bounded_and_path_free():
    with TestClient(app) as client:
        storage = client.get("/api/diagnostics/storage")
        cpu = client.get("/api/diagnostics/cpu")

    assert storage.status_code == 200
    assert set(storage.json()) == {"generated_at", "total_bytes", "cache_limit_bytes", "categories", "filesystem"}
    assert {item["key"] for item in storage.json()["categories"]} == {
        "system", "thumbnails", "videos", "database", "logs", "temporary", "other",
    }
    assert "path" not in storage.text.lower()
    assert cpu.status_code == 200
    assert 0 <= cpu.json()["addon_percent"] <= 100
    if cpu.json()["system_percent"] is not None:
        assert 0 <= cpu.json()["system_percent"] <= 100


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


async def no_background_work():
    return None


def test_save_mappings_queues_codec_validation(monkeypatch):
    monkeypatch.setattr(main, "reconcile", no_background_work)
    monkeypatch.setattr(main, "refresh_camera_codecs", no_background_work)
    channels = [item.model_dump(mode="json") for item in main.settings.channels()]

    with TestClient(app) as client:
        response = client.put("/api/channels", json={"channels": channels})

    assert response.status_code == 200
    assert response.json()["codec_validation_queued"] is True
