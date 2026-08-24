import logging

import pytest
from app.log_buffer import SanitizedLogBuffer
from app.task_tracker import TaskTracker


@pytest.mark.asyncio
async def test_task_tracker_reports_safe_live_state_only():
    tracker = TaskTracker()
    async with tracker.track("historical_video", {"event_id": "event-1", "password": "secret"}) as task_id:
        tracker.update(task_id, phase="generating_mp4", details={"source_codec": "h264", "url": "rtsp://secret"})
        tasks = tracker.snapshot()
        assert len(tasks) == 1
        assert tasks[0]["operation"] == "historical_video"
        assert tasks[0]["phase"] == "generating_mp4"
        assert tasks[0]["details"] == {"event_id": "event-1", "source_codec": "h264"}
        assert tasks[0]["duration_seconds"] >= 0
        assert tasks[0]["started_at"].endswith("+00:00")
    assert tracker.snapshot() == []


def test_runtime_log_buffer_redacts_and_truncates():
    buffer = SanitizedLogBuffer(("fixture-user", "fixture-password"))
    logger = logging.getLogger("runtime-buffer-test")
    record = logger.makeRecord(logger.name, logging.ERROR, __file__, 1,
                               "failed rtsp://fixture-user:fixture-password@example/stream", (), None)
    buffer.emit(record)
    text = str(buffer.snapshot())
    assert "fixture-user" not in text
    assert "fixture-password" not in text
    assert "rtsp://***:***@example/stream" in text
    buffer.truncate()
    assert buffer.snapshot() == []


def test_runtime_log_buffer_ignores_its_own_polling_requests():
    buffer = SanitizedLogBuffer(())
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(logger.name, logging.INFO, __file__, 1,
                               '127.0.0.1 - "GET /api/diagnostics/runtime HTTP/1.1" 200', (), None)
    buffer.emit(record)
    assert buffer.snapshot() == []

    event_poll = logger.makeRecord(logger.name, logging.INFO, __file__, 1,
                                   '127.0.0.1 - "GET /api/events/698b66d5-a0f0-55bd-9ad9-37f217b14eca HTTP/1.1" 200', (), None)
    buffer.emit(event_poll)
    assert buffer.snapshot() == []
