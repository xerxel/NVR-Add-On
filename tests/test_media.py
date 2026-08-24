import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.media import MediaManager
from PIL import Image, ImageDraw


class FakeDatabase:
    def __init__(self):
        self.updates = []

    def update(self, event_id, **values):
        self.updates.append((event_id, values))


def settings(tmp_path):
    return SimpleNamespace(
        data_dir=tmp_path,
        max_concurrent_jobs=1,
        ffmpeg_timeout_seconds=10,
        rtsp_transport="tcp",
        nvr_username="user",
        nvr_password="password",
        max_clip_seconds=180,
        media_retention_days=14,
        max_cache_mb=100,
    )


@pytest.mark.asyncio
async def test_thumbnail_retries_uniform_first_frame(tmp_path):
    manager = MediaManager(settings(tmp_path), FakeDatabase())
    calls = []

    async def fake_run(args, timeout=None):
        calls.append(args)
        target = Path(args[-1])
        if len(calls) == 1:
            Image.new("RGB", (320, 180), "gray").save(target, format="JPEG")
        else:
            image = Image.new("RGB", (320, 180), "black")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 160, 180), fill="white")
            image.save(target, format="JPEG")
        return b"", b""

    manager._run = fake_run
    name = await manager.thumbnail("event_1", "rtsp://hidden")

    assert name == "event_1.jpg"
    assert len(calls) == 2
    assert calls[0][calls[0].index("-ss") + 1] == "2"
    assert calls[1][calls[1].index("-ss") + 1] == "4"


@pytest.mark.asyncio
async def test_clip_has_explicit_duration_and_reports_ready(tmp_path):
    database = FakeDatabase()
    manager = MediaManager(settings(tmp_path), database)
    ffmpeg_args = []

    async def fake_run(args, timeout=None):
        if args[0] == "ffmpeg":
            ffmpeg_args.extend(args)
            Path(args[-1]).write_bytes(b"mock-mp4")
            return b"", b""
        return json.dumps({"format": {"duration": "15.0"},
                           "streams": [{"codec_type": "video", "codec_name": "h264"}]}).encode(), b""

    manager._run = fake_run
    await manager.clip("event_2", "rtsp://hidden", 15)

    assert ffmpeg_args[ffmpeg_args.index("-t") + 1] == "15.000"
    assert any(values.get("video_status") == "ready" for _, values in database.updates)


@pytest.mark.asyncio
async def test_diagnostic_thumbnail_does_not_wait_for_background_queue(tmp_path):
    manager = MediaManager(settings(tmp_path), FakeDatabase())

    async def fake_run(args, timeout=None):
        image = Image.new("RGB", (320, 180), "black")
        ImageDraw.Draw(image).rectangle((0, 0, 160, 180), fill="white")
        image.save(Path(args[-1]), format="JPEG")
        return b"", b""

    manager._run = fake_run
    async with manager.thumbnail_semaphore:
        name = await asyncio.wait_for(
            manager.thumbnail("diagnostic_1", "rtsp://hidden", diagnostic=True), timeout=1,
        )
    assert name == "diagnostic_1.jpg"
