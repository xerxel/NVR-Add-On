import asyncio
import json
import os
import shutil
import signal
from contextlib import asynccontextmanager
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
        return {"streams": [{k: s.get(k) for k in ("codec_type", "codec_name", "width", "height", "r_frame_rate")}
                            for s in data.get("streams", [])], "duration": data.get("format", {}).get("duration")}

    async def thumbnail(self, event_id: str, url: str, *, diagnostic: bool = False) -> str:
        name = safe_name(event_id) + ".jpg"; final = self.thumbs / name; temp = self.tmp / (name + ".tmp")
        operation = "diagnostic_thumbnail" if diagnostic else "background_thumbnail"
        async with self._activity(operation, {"event_id": event_id}) as task_id:
            try:
                semaphore = self.diagnostic_thumbnail_semaphore if diagnostic else self.thumbnail_semaphore
                if self.tracker and task_id:
                    self.tracker.update(task_id, phase="waiting_for_worker")
                async with semaphore:
                    if self.tracker and task_id:
                        self.tracker.update(task_id, phase="extracting_frame")
                    for offset in (2, 4):
                        await self._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", self.settings.rtsp_transport,
                                         "-i", url, "-an", "-ss", str(offset), "-frames:v", "1",
                                         "-vf", "scale='min(960,iw)':-2", "-f", "image2", "-y", str(temp)],
                                        min(30, self.settings.ffmpeg_timeout_seconds))
                        with Image.open(temp) as image:
                            image.load()
                            variation = ImageStat.Stat(image.convert("L")).stddev[0]
                            valid = image.width > 0 and image.height > 0 and temp.stat().st_size <= 10_000_000
                        if valid and variation >= 3:
                            temp.replace(final)
                            return name
                raise MediaError("NVR returned only blank or uniform thumbnail frames")
            finally: temp.unlink(missing_ok=True)

    async def clip(self, event_id: str, url: str, duration_seconds: float, *, raise_errors: bool = False) -> None:
        name = safe_name(event_id) + ".mp4"; final = self.videos / name; temp = self.tmp / (name + ".tmp")
        bounded_duration = max(1, min(float(duration_seconds), self.settings.max_clip_seconds))
        self.db.update(event_id, video_status="generating", last_error=None)
        try:
            async with self._activity("historical_video", {"event_id": event_id,
                                                            "requested_duration_seconds": bounded_duration}) as task_id:
                if self.tracker and task_id:
                    self.tracker.update(task_id, phase="waiting_for_worker")
                async with self.clip_semaphore:
                    if self.tracker and task_id:
                        self.tracker.update(task_id, phase="probing_source")
                    source = await self.probe(url)
                    video_codec = next((stream.get("codec_name") for stream in source["streams"]
                                        if stream.get("codec_type") == "video"), None)
                    if self.tracker and task_id:
                        self.tracker.update(task_id, phase="generating_mp4", details={"source_codec": video_codec})
                    video_args = ["-c:v", "copy"] if video_codec == "h264" else [
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    ]
                    await self._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", self.settings.rtsp_transport,
                                     "-fflags", "+genpts+discardcorrupt", "-i", url, "-t", f"{bounded_duration:.3f}",
                                     "-map", "0:v:0", "-map", "0:a?", *video_args, "-c:a", "aac",
                                     "-movflags", "+faststart", "-f", "mp4", "-y", str(temp)])
                    if self.tracker and task_id:
                        self.tracker.update(task_id, phase="validating_mp4")
                    out, _ = await self._run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type",
                                              "-of", "json", str(temp)], 20)
            info = json.loads(out); duration = float(info.get("format", {}).get("duration", 0))
            if duration <= 0 or temp.stat().st_size <= 0: raise MediaError("Generated clip did not validate")
            temp.replace(final)
            codec = next((s.get("codec_name") for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            self.db.update(event_id, video_status="ready", video_name=name, video_size=final.stat().st_size,
                           video_duration=duration, video_codec=codec, status="ready")
        except Exception as exc:
            detail = redact(exc, (self.settings.nvr_username, self.settings.nvr_password))
            self.db.update(event_id, video_status="failed", last_error=detail)
            if raise_errors:
                raise MediaError(detail) from None
        finally:
            temp.unlink(missing_ok=True); self.jobs.pop(event_id, None)

    def enqueue(self, event_id: str, url: str, duration_seconds: float):
        if event_id not in self.jobs:
            self.jobs[event_id] = asyncio.create_task(self.clip(event_id, url, duration_seconds))

    def delete_clip(self, event: dict):
        if event.get("video_name"): (self.videos / safe_name(Path(event["video_name"]).stem)).with_suffix(".mp4").unlink(missing_ok=True)
        self.db.update(event["id"], video_status="uncached", video_name=None, video_size=None, video_duration=None, video_codec=None)

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
                "background_thumbnail_busy": self.thumbnail_semaphore.locked()}
