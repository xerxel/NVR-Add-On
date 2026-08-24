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
    assert ffmpeg_args[ffmpeg_args.index("-c:v") + 1] == "copy"
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


@pytest.mark.asyncio
async def test_run_escalates_from_terminate_to_kill_when_child_hangs(tmp_path, monkeypatch):
    manager = MediaManager(settings(tmp_path), FakeDatabase())
    manager._PROCESS_STOP_GRACE_SECONDS = 0.01
    killed = asyncio.Event()
    signals = []

    class HungProcess:
        pid = 1234
        returncode = None

        async def communicate(self):
            await killed.wait()
            self.returncode = -9
            return b"", b""

        def terminate(self):
            signals.append("terminate")

        def kill(self):
            signals.append("kill")
            killed.set()

    async def fake_subprocess(*args, **kwargs):
        return HungProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    with pytest.raises(Exception, match="timed out after 0.01 seconds"):
        await manager._run(["ffmpeg", "-version"], timeout=0.01)

    assert signals == ["terminate", "kill"]


@pytest.mark.asyncio
async def test_process_limit_applies_across_all_media_commands(tmp_path, monkeypatch):
    manager = MediaManager(settings(tmp_path), FakeDatabase())
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    starts = 0

    class Process:
        returncode = 0
        pid = 1234

        async def communicate(self):
            nonlocal starts
            starts += 1
            if starts == 1:
                first_started.set()
                await release_first.wait()
            return b"", b""

    async def fake_subprocess(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    first = asyncio.create_task(manager._run(["ffmpeg", "first"]))
    await first_started.wait()
    second = asyncio.create_task(manager._run(["ffprobe", "second"]))
    await asyncio.sleep(0)
    assert starts == 1
    release_first.set()
    await asyncio.gather(first, second)
    assert starts == 2


@pytest.mark.asyncio
async def test_user_clip_preempts_thumbnail_and_thumbnail_resumes_after_clip(tmp_path):
    database = FakeDatabase()
    manager = MediaManager(settings(tmp_path), database)
    thumbnail_started = asyncio.Event()
    thumbnail_cancelled = asyncio.Event()
    clip_started = asyncio.Event()
    release_clip = asyncio.Event()
    thumbnail_attempts = 0

    async def fake_run(args, timeout=None):
        nonlocal thumbnail_attempts
        target = Path(args[-1])
        if args[0] == "ffmpeg" and "-frames:v" in args:
            thumbnail_attempts += 1
            if thumbnail_attempts == 1:
                thumbnail_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    thumbnail_cancelled.set()
                    raise
            image = Image.new("RGB", (320, 180), "black")
            ImageDraw.Draw(image).rectangle((0, 0, 160, 180), fill="white")
            image.save(target, format="JPEG")
            return b"", b""
        if args[0] == "ffmpeg":
            clip_started.set()
            await release_clip.wait()
            target.write_bytes(b"mock-mp4")
            return b"", b""
        if str(target).startswith("rtsp://"):
            return json.dumps({"format": {}, "streams": [
                {"codec_type": "video", "codec_name": "h264"},
            ]}).encode(), b""
        return json.dumps({"format": {"duration": "15.0"}, "streams": [
            {"codec_type": "video", "codec_name": "h264"},
        ]}).encode(), b""

    manager._run = fake_run
    thumbnail = asyncio.create_task(manager.thumbnail("thumbnail_event", "rtsp://thumbnail"))
    await thumbnail_started.wait()

    manager.enqueue("clip_event", "rtsp://clip", 15)
    clip = manager.jobs["clip_event"]
    await asyncio.wait_for(thumbnail_cancelled.wait(), timeout=1)
    await asyncio.wait_for(clip_started.wait(), timeout=1)

    assert not manager.background_thumbnails_allowed.is_set()
    release_clip.set()
    await asyncio.wait_for(clip, timeout=1)
    assert manager.background_thumbnails_allowed.is_set()
    assert await asyncio.wait_for(thumbnail, timeout=1) == "thumbnail_event.jpg"
    assert thumbnail_attempts == 2
