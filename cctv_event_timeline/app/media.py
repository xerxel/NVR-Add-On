import asyncio
import json
import logging
import math
import os
import shutil
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageStat

from .security import redact, safe_name

_TERMINATE_SIGNAL = getattr(signal, "SIGTERM", 15)
_KILL_SIGNAL = getattr(signal, "SIGKILL", 9)


class MediaError(RuntimeError): pass


class MediaManager:
    _PROCESS_STOP_GRACE_SECONDS = 2

    def __init__(self, settings, database, tracker=None):
        self.settings, self.db = settings, database
        self.tracker = tracker
        self.root = settings.data_dir / "cache"
        self.thumbs, self.videos, self.tmp = self.root / "thumbs", self.root / "videos", settings.data_dir / "tmp"
        for p in (self.thumbs, self.videos, self.tmp): p.mkdir(parents=True, exist_ok=True)
        self.clip_semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        # Apply the configured limit to every ffmpeg/ffprobe child, including
        # thumbnails, rather than allowing each kind of media job its own pool.
        self.process_semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self.thumbnail_semaphore = asyncio.Semaphore(1)
        self.diagnostic_thumbnail_semaphore = asyncio.Semaphore(1)
        self.jobs: dict[str, asyncio.Task] = {}
        self._codec_locks: dict[int, asyncio.Lock] = {}
        self.background_thumbnails_allowed = asyncio.Event()
        self.background_thumbnails_allowed.set()
        self._background_thumbnail_process: asyncio.Task | None = None
        self.log = logging.getLogger("timeline.media")
        for abandoned in self.tmp.glob("*.tmp"): abandoned.unlink(missing_ok=True)

    @asynccontextmanager
    async def _activity(self, operation: str, details: dict | None = None):
        if self.tracker:
            async with self.tracker.track(operation, details) as task_id:
                yield task_id
        else:
            yield None

    async def _run(self, args: list[str], timeout: int | None = None) -> tuple[bytes, bytes]:
        operation_timeout = timeout or self.settings.ffmpeg_timeout_seconds
        async with self._activity("media_process", {"executable": Path(args[0]).name,
                                                     "timeout_seconds": operation_timeout}) as task_id:
            if self.tracker and task_id:
                self.tracker.update(task_id, phase="waiting_for_worker")
            async with self.process_semaphore:
                if self.tracker and task_id:
                    self.tracker.update(task_id, phase="running")
                process = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
                )
                communicate = asyncio.create_task(process.communicate())
                try:
                    stdout, stderr = await asyncio.wait_for(asyncio.shield(communicate), operation_timeout)
                except asyncio.TimeoutError:
                    await self._stop_process(process, communicate)
                    raise MediaError(f"Media operation timed out after {operation_timeout} seconds") from None
                except asyncio.CancelledError:
                    await self._stop_process(process, communicate)
                    raise
            if process.returncode:
                raise MediaError(redact(stderr.decode(errors="replace")[-1500:], (self.settings.nvr_username, self.settings.nvr_password)))
            return stdout, stderr

    async def _stop_process(self, process, communicate: asyncio.Task) -> None:
        """Stop a child process without allowing an unresponsive child to hang the caller."""
        self._signal_process(process, _TERMINATE_SIGNAL)
        try:
            await asyncio.wait_for(asyncio.shield(communicate), self._PROCESS_STOP_GRACE_SECONDS)
            return
        except asyncio.TimeoutError:
            self._signal_process(process, _KILL_SIGNAL)
        try:
            await asyncio.wait_for(asyncio.shield(communicate), self._PROCESS_STOP_GRACE_SECONDS)
        except asyncio.TimeoutError:
            communicate.cancel()
            await asyncio.gather(communicate, return_exceptions=True)

    @staticmethod
    def _signal_process(process, sig: int) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, AttributeError, OSError):
            try:
                process.kill() if sig == _KILL_SIGNAL else process.terminate()
            except ProcessLookupError:
                pass

    async def probe(self, url: str) -> dict:
        out, _ = await self._run(["ffprobe", "-v", "error", "-rtsp_transport", self.settings.rtsp_transport,
                                  "-show_streams", "-show_format", "-of", "json", url], min(30, self.settings.ffmpeg_timeout_seconds))
        data = json.loads(out)
        return {"streams": [{k: s.get(k) for k in ("codec_type", "codec_name", "codec_long_name", "profile", "level",
                                                               "width", "height", "r_frame_rate", "tags", "side_data_list")}
                            for s in data.get("streams", [])], "duration": data.get("format", {}).get("duration")}

    @staticmethod
    def _codec_label(stream: dict) -> str:
        codec = (stream.get("codec_name") or "unknown").lower()
        metadata = json.dumps({"tags": stream.get("tags"), "side_data": stream.get("side_data_list")}).lower()
        smart = any(marker in metadata for marker in ("smartcodec", "smart_codec", "h.264+", "h264+", "h.265+", "h265+"))
        if codec == "h264":
            return "H.264+" if smart else "H.264"
        if codec in {"hevc", "h265"}:
            return "H.265+" if smart else "H.265"
        return codec.upper().replace("HEVC", "H.265") if codec != "unknown" else "Unknown"

    async def codec_for(self, channel_id: int, track: str, url: str, *, background: bool = False,
                        task_id: str | None = None) -> dict:
        cached = self.db.camera_codec(channel_id, track)
        if cached:
            return cached
        lock = self._codec_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            cached = self.db.camera_codec(channel_id, track)
            if cached:
                return cached
            args = ["ffprobe", "-v", "error", "-rtsp_transport", self.settings.rtsp_transport,
                    "-show_streams", "-show_format", "-of", "json", url]
            if background:
                out, _ = await self._run_background_operation(
                    args, min(30, self.settings.ffmpeg_timeout_seconds), task_id, "probing_codec",
                )
                source = json.loads(out)
                source = {"streams": [{k: s.get(k) for k in (
                    "codec_type", "codec_name", "codec_long_name", "profile", "level", "width", "height",
                    "r_frame_rate", "tags", "side_data_list",
                )} for s in source.get("streams", [])], "duration": source.get("format", {}).get("duration")}
            else:
                source = await self.probe(url)
            stream = next((item for item in source["streams"] if item.get("codec_type") == "video"), {})
            codec = (stream.get("codec_name") or "unknown").lower()
            label = self._codec_label(stream)
            self.db.store_camera_codec(channel_id, track, codec, label, stream)
            return self.db.camera_codec(channel_id, track) or {
                "channel_id": channel_id, "track": track, "codec": codec, "codec_label": label,
            }

    async def thumbnail(self, event_id: str, url: str, *, diagnostic: bool = False,
                        channel_id: int | None = None, track: str | None = None) -> str:
        name = safe_name(event_id) + ".jpg"; final = self.thumbs / name; temp = self.tmp / (name + ".tmp")
        operation = "diagnostic_thumbnail" if diagnostic else "background_thumbnail"
        async with self._activity(operation, {"event_id": event_id}) as task_id:
            try:
                semaphore = self.diagnostic_thumbnail_semaphore if diagnostic else self.thumbnail_semaphore
                if self.tracker and task_id:
                    self.tracker.update(task_id, phase="waiting_for_worker")
                async with semaphore:
                    if channel_id is not None and track:
                        await self.codec_for(channel_id, track, url, background=not diagnostic, task_id=task_id)
                    for offset in (2, 4):
                        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", self.settings.rtsp_transport,
                                "-i", url, "-an", "-ss", str(offset), "-frames:v", "1",
                                "-vf", "scale='min(960,iw)':-2", "-f", "image2", "-y", str(temp)]
                        if diagnostic:
                            if self.tracker and task_id:
                                self.tracker.update(task_id, phase="extracting_frame")
                            await self._run(args, min(30, self.settings.ffmpeg_timeout_seconds))
                        else:
                            await self._run_background_operation(
                                args, min(30, self.settings.ffmpeg_timeout_seconds), task_id, "extracting_frame",
                            )
                        with Image.open(temp) as image:
                            image.load()
                            variation = ImageStat.Stat(image.convert("L")).stddev[0]
                            valid = image.width > 0 and image.height > 0 and temp.stat().st_size <= 10_000_000
                        if valid and variation >= 3:
                            temp.replace(final)
                            return name
                raise MediaError("NVR returned only blank or uniform thumbnail frames")
            finally: temp.unlink(missing_ok=True)

    async def _run_background_operation(self, args: list[str], timeout: int, task_id: str | None,
                                        phase: str) -> tuple[bytes, bytes]:
        """Run a retryable background media process that yields immediately to a user clip."""
        while True:
            if self.tracker and task_id:
                current_phase = phase if self.background_thumbnails_allowed.is_set() else "paused_for_historical_video"
                self.tracker.update(task_id, phase=current_phase)
            await self.background_thumbnails_allowed.wait()
            if self.tracker and task_id:
                self.tracker.update(task_id, phase=phase)
            operation = asyncio.create_task(self._run(args, timeout))
            self._background_thumbnail_process = operation
            try:
                return await operation
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current and current.cancelling():
                    raise
                if self.background_thumbnails_allowed.is_set():
                    raise
                if self.tracker and task_id:
                    self.tracker.update(task_id, phase="paused_for_historical_video")
            finally:
                if self._background_thumbnail_process is operation:
                    self._background_thumbnail_process = None

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _generation_update(self, event_id: str, details: dict, **changes) -> None:
        details.update(changes)
        self.db.update(event_id, generation_json=json.dumps(details, separators=(",", ":")))

    async def clip(self, event_id: str, url: str, duration_seconds: float, *, channel_id: int | None = None,
                   track: str | None = None, request_details: dict | None = None,
                   raise_errors: bool = False) -> None:
        name = safe_name(event_id) + ".mp4"; final = self.videos / name; temp = self.tmp / (name + ".tmp")
        bounded_duration = max(1, min(float(duration_seconds), self.settings.max_clip_seconds))
        details = dict(request_details or {})
        details.setdefault("requested_at", self._utc_now())
        details.update({"requested_duration_seconds": round(bounded_duration, 3), "phase": "queued"})
        self.db.update(event_id, video_status="generating", video_error=None, last_error=None,
                       generation_json=json.dumps(details, separators=(",", ":")))
        self.log.info("Historical clip started event=%s channel=%s duration=%.3fs",
                      event_id, channel_id, bounded_duration)
        try:
            async with self._activity("historical_video", {"event_id": event_id,
                                                            "requested_duration_seconds": bounded_duration}) as task_id:
                if self.tracker and task_id:
                    self.tracker.update(task_id, phase="waiting_for_worker")
                async with self.clip_semaphore:
                    if self.tracker and task_id:
                        self.tracker.update(task_id, phase="probing_source")
                    self._generation_update(event_id, details, phase="probing_source")
                    if channel_id is not None and track:
                        cached = await self.codec_for(channel_id, track, url)
                        video_codec = cached.get("codec")
                        codec_label = cached.get("codec_label")
                    else:
                        source = await self.probe(url)
                        stream = next((item for item in source["streams"] if item.get("codec_type") == "video"), {})
                        video_codec = stream.get("codec_name")
                        codec_label = self._codec_label(stream)
                    if self.tracker and task_id:
                        self.tracker.update(task_id, phase="generating_mp4", details={"source_codec": video_codec})
                    generation_mode = "remux" if video_codec == "h264" else "transcode"
                    operation_timeout = max(self.settings.ffmpeg_timeout_seconds, math.ceil(bounded_duration) + 30)
                    self._generation_update(event_id, details, phase="generating_mp4", source_codec=video_codec,
                                            source_codec_label=codec_label, generation_mode=generation_mode,
                                            timeout_seconds=operation_timeout)
                    self.log.info("Historical clip encoding event=%s codec=%s mode=%s timeout=%ss",
                                  event_id, codec_label or video_codec, generation_mode, operation_timeout)
                    video_args = ["-c:v", "copy"] if video_codec == "h264" else [
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    ]
                    await self._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", self.settings.rtsp_transport,
                                     "-fflags", "+genpts+discardcorrupt", "-i", url, "-t", f"{bounded_duration:.3f}",
                                     "-map", "0:v:0", "-map", "0:a?", *video_args, "-c:a", "aac",
                                     "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                                     "-frag_duration", "1000000", "-flush_packets", "1",
                                     "-f", "mp4", "-y", str(temp)],
                                    operation_timeout)
                    if self.tracker and task_id:
                        self.tracker.update(task_id, phase="validating_mp4")
                    self._generation_update(event_id, details, phase="validating_mp4")
                    out, _ = await self._run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type",
                                              "-of", "json", str(temp)], 20)
            info = json.loads(out); duration = float(info.get("format", {}).get("duration", 0))
            if duration <= 0 or temp.stat().st_size <= 0: raise MediaError("Generated clip did not validate")
            temp.replace(final)
            codec = next((s.get("codec_name") for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            self.db.update(event_id, video_status="ready", video_name=name, video_size=final.stat().st_size,
                           video_duration=duration, video_codec=codec, video_error=None, status="ready")
            self._generation_update(event_id, details, phase="ready", completed_at=self._utc_now(),
                                    output_duration_seconds=duration, output_size_bytes=final.stat().st_size,
                                    output_codec=codec)
            self.log.info("Historical clip ready event=%s duration=%.3fs bytes=%s codec=%s",
                          event_id, duration, final.stat().st_size, codec)
        except asyncio.CancelledError:
            detail = "Clip generation was cancelled before completion; retry the clip"
            self._generation_update(event_id, details, phase="cancelled", failed_at=self._utc_now(), error=detail)
            self.db.update(event_id, video_status="failed", video_error=detail, last_error=detail)
            self.log.warning("Historical clip cancelled event=%s phase=%s", event_id, details.get("phase"))
            raise
        except Exception as exc:
            detail = redact(exc, (self.settings.nvr_username, self.settings.nvr_password))
            self._generation_update(event_id, details, phase="failed", failed_at=self._utc_now(), error=detail)
            self.db.update(event_id, video_status="failed", video_error=detail, last_error=detail)
            self.log.error("Historical clip failed event=%s phase=%s error=%s",
                           event_id, details.get("phase"), detail)
            if raise_errors:
                raise MediaError(detail) from None
        finally:
            temp.unlink(missing_ok=True); self.jobs.pop(event_id, None)
            if not self.jobs:
                self.background_thumbnails_allowed.set()

    def enqueue(self, event_id: str, url: str, duration_seconds: float, *, channel_id: int | None = None,
                track: str | None = None, request_details: dict | None = None):
        if event_id not in self.jobs:
            self.background_thumbnails_allowed.clear()
            if self._background_thumbnail_process and not self._background_thumbnail_process.done():
                self._background_thumbnail_process.cancel()
            task = asyncio.create_task(self.clip(
                event_id, url, duration_seconds, channel_id=channel_id, track=track, request_details=request_details,
            ), name=f"historical-clip-{event_id}")
            self.jobs[event_id] = task

            def completed(job: asyncio.Task) -> None:
                if job.cancelled():
                    self.log.warning("Historical clip task ended cancelled event=%s", event_id)
                    return
                error = job.exception()
                if error:
                    self.log.error("Historical clip task ended with exception event=%s error=%s",
                                   event_id, redact(error, (self.settings.nvr_username, self.settings.nvr_password)))

            task.add_done_callback(completed)

    async def progressive_clip(self, event_id: str):
        """Yield a growing fragmented MP4 while its independent cache job runs."""
        name = safe_name(event_id) + ".mp4"
        final, temp = self.videos / name, self.tmp / (name + ".tmp")
        offset = 0
        self.log.info("Progressive clip client connected event=%s", event_id)
        try:
            while True:
                path = final if final.is_file() else temp
                chunk = b""
                if path.is_file():
                    with path.open("rb") as stream:
                        stream.seek(offset)
                        chunk = stream.read(256 * 1024)
                    if chunk:
                        if offset == 0:
                            self.log.info("Progressive clip first bytes event=%s bytes=%s", event_id, len(chunk))
                        offset += len(chunk)
                        yield chunk
                        continue
                row = self.db.event(event_id)
                if not row or row.get("video_status") in {"ready", "failed", "uncached"}:
                    break
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self.log.info("Progressive clip client disconnected event=%s bytes_sent=%s", event_id, offset)
            raise
        finally:
            self.log.info("Progressive clip ended event=%s bytes_sent=%s", event_id, offset)

    def delete_clip(self, event: dict):
        if event.get("video_name"): (self.videos / safe_name(Path(event["video_name"]).stem)).with_suffix(".mp4").unlink(missing_ok=True)
        self.db.update(event["id"], video_status="uncached", video_name=None, video_size=None, video_duration=None,
                       video_codec=None, video_error=None, generation_json=None)

    def cleanup(self):
        files = [p for p in self.root.rglob("*") if p.is_file() and ".tmp" not in p.name]
        cutoff = __import__("time").time() - self.settings.media_retention_days * 86400
        for p in files:
            if p.stat().st_mtime < cutoff: p.unlink(missing_ok=True)
        files = sorted((p for p in self.root.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
        total, limit = sum(p.stat().st_size for p in files), self.settings.max_cache_mb * 1024 * 1024
        for p in files:
            if total <= limit: break
            size = p.stat().st_size; p.unlink(missing_ok=True); total -= size

    def health(self):
        return {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
                "active_jobs": len(self.jobs), "diagnostic_thumbnail_busy": self.diagnostic_thumbnail_semaphore.locked(),
                "background_thumbnail_busy": self.thumbnail_semaphore.locked(),
                "background_thumbnail_paused": not self.background_thumbnails_allowed.is_set()}
