import json

from app.config import Settings


def test_default_entity_mappings_and_path_template(tmp_path, monkeypatch):
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"nvr_timezone": "Europe/London"}), encoding="utf-8")
    monkeypatch.setenv("OPTIONS_FILE", str(options))
    monkeypatch.setenv("TIMELINE_DATA", str(tmp_path / "data"))

    settings = Settings.load()
    channels = settings.channels()

    assert settings.live_rtsp_path_template == "/Streaming/channels/{track}"
    assert settings.playback_rtsp_path_template == "/Streaming/tracks/{track}"
    assert all(c.enabled for c in channels)
    assert [c.motion_entity for c in channels] == [
        f"binary_sensor.network_video_recorder_channel_{i}_motion" for i in range(1, 9)
    ]
    assert [c.camera_entity for c in channels] == [
        f"camera.network_video_recorder_channel_{i}" for i in range(1, 9)
    ]


def test_legacy_path_migrates_to_live_only(tmp_path, monkeypatch):
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"rtsp_path_template": "/legacy/live/{track}"}), encoding="utf-8")
    monkeypatch.setenv("OPTIONS_FILE", str(options))
    monkeypatch.setenv("TIMELINE_DATA", str(tmp_path / "data"))

    settings = Settings.load()

    assert settings.live_rtsp_path_template == "/legacy/live/{track}"
    assert settings.playback_rtsp_path_template == "/Streaming/tracks/{track}"
