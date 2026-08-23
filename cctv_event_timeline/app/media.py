import asyncio
import json
import os
import shutil
from pathlib import Path

from PIL import Image

from .security import redact, safe_name


class MediaError(RuntimeError): pass


class MediaManager:
    def __init__(self, settings, database):
        self.settings, self.db = settings, database
        self.root = settings.data_dir / "cache"
        self.thumbs, self.videos, self.tmp = self.root / "thumbs", self.root / "videos", settings.data_dir / "tmp"
        for p in (self.thumbs, self.videos, self.tmp): p.mkdir(parents=True, exist_ok=True)
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self.jobs: dict[str, asyncio.Task] = {}
        for abandoned in self.tmp.glob("*.tmp"): abandoned.unlink(missing_ok=True)

    async def _run(self, args: list[str], timeout: int | None = None) -> tuple[bytes, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                           start_new_session=True)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout or self.settings.ffmpeg_timeout_seconds)
        except asyncio.TimeoutError:
            try: os.killpg(process.pid, 15)
            except (ProcessLookupError, AttributeError): process.terminate()
            await process.wait()
            raise MediaError("Media operation timed out") from None
        if process.returncode:
            raise MediaError(redact(stderr.decode(errors="replace")[-1500:], (self.settings.nvr_username, self.settings.nvr_password)))
        return stdout, stderr

    async def probe(self, url: str) -> dict:
        out, _ = await self._run(["ffprobe", "-v", "error", "-rtsp_transport", self.settings.rtsp_transport,
                                  "-show_streams", "-show_format", "-of", "json", url], min(30, self.settings.ffmpeg_timeout_seconds))
        data = json.loads(out)
        return {"streams": [{k: s.get(k) for k in ("codec_type", "codec_name", "width", "height", "r_frame_rate")}
                            for s in data.get("streams", [])], "duration": data.get("format", {}).get("duration")}

    async def thumbnail(self, event_id: str, url: str) -> str:
        name = safe_name(event_id) + ".jpg"; final = self.thumbs / name; temp = self.tmp / (name + ".tmp")
        async with self.semaphore:
            await self._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", self.settings.rtsp_transport,
                             "-i", url, "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-f", "image2", "-y", str(temp)])
        try:
            with Image.open(temp) as image:
                image.verify()
                if image.width < 1 or image.height < 1 or temp.stat().st_size > 10_000_000: raise MediaError("Invalid thumbnail")
            temp.replace(final); return name
        finally: temp.unlink(missing_ok=True)

    async def clip(self, event_id: str, url: str) -> None:
        name = safe_name(event_id) + ".mp4"; final = self.videos / name; temp = self.tmp / (name + ".tmp")
        self.db.update(event_id, video_status="generating", last_error=None)
        try:
            async with self.semaphore:
                await self._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", self.settings.rtsp_transport,
                                 "-i", url, "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-f", "mp4", "-y", str(temp)])
                out, _ = await self._run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type",
                                          "-of", "json", str(temp)], 20)
            info = json.loads(out); duration = float(info.get("format", {}).get("duration", 0))
            if duration <= 0 or temp.stat().st_size <= 0: raise MediaError("Generated clip did not validate")
            temp.replace(final)
            codec = next((s.get("codec_name") for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            self.db.update(event_id, video_status="ready", video_name=name, video_size=final.stat().st_size,
                           video_duration=duration, video_codec=codec, status="ready")
        except Exception as exc:
            self.db.update(event_id, video_status="failed", last_error=redact(exc, (self.settings.nvr_username, self.settings.nvr_password)))
        finally:
            temp.unlink(missing_ok=True); self.jobs.pop(event_id, None)

    def enqueue(self, event_id: str, url: str):
        if event_id not in self.jobs:
            self.jobs[event_id] = asyncio.create_task(self.clip(event_id, url))

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
        return {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"), "active_jobs": len(self.jobs)}
