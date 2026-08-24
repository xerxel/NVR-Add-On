import app.main as main
import pytest


@pytest.mark.asyncio
async def test_mapping_codec_validation_probes_every_enabled_main_track(monkeypatch):
    first, second = main.settings.channels()[:2]
    second = second.model_copy(update={"enabled": False})
    calls = []

    async def capture_codec(channel_id, track, url):
        calls.append((channel_id, track, url))
        return {"codec": "h264", "codec_label": "H.264"}

    monkeypatch.setattr(main.settings, "channels", lambda: [first, second])
    monkeypatch.setattr(main.media, "codec_for", capture_codec)

    await main.refresh_camera_codecs()

    assert [(channel_id, track) for channel_id, track, _ in calls] == [(first.id, first.main_track)]
    assert calls[0][2].startswith("rtsp://") and f"/Streaming/channels/{first.main_track}" in calls[0][2]
