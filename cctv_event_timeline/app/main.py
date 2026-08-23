import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .config import Settings
from .database import Database
from .hikvision import TimestampMode, playback_url
from .homeassistant import HomeAssistantClient
from .media import MediaManager
from .models import ChannelList, HistoryTest, PlaybackTest
from .security import redact, safe_name

settings = Settings.load()
settings.data_dir.mkdir(parents=True, exist_ok=True)
db = Database(settings.data_dir / "events.db")
ha = HomeAssistantClient()
media = MediaManager(settings, db)
log = logging.getLogger("timeline")
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
stop_event = asyncio.Event()


def channel(channel_id: int):
    return next((c for c in settings.channels() if c.id == channel_id), None)


def public_event(row: dict):
    keep = ("id", "channel_id", "started_at", "ended_at", "status", "thumbnail_status", "thumbnail_source",
            "video_status", "video_size", "video_duration", "video_codec", "last_error", "retry_count")
    result = {k: row.get(k) for k in keep}
    c = channel(row["channel_id"]); result["camera_name"] = c.name if c else f"Camera {row['channel_id']}"
    result["thumbnail_url"] = f"api/events/{row['id']}/thumbnail" if row.get("thumbnail_name") else None
    result["video_url"] = f"api/events/{row['id']}/video" if row.get("video_status") == "ready" else None
    return result


def event_playback(row: dict, stream="main", mode=None, offset=None, duration=None):
    c = channel(row["channel_id"])
    if not c: raise ValueError("Channel mapping no longer exists")
    end = row.get("ended_at") or (datetime.fromisoformat(row["started_at"]) + timedelta(seconds=30)).isoformat()
    if duration: end = (datetime.fromisoformat(row["started_at"]) + timedelta(seconds=duration)).isoformat()
    return playback_url(host=settings.nvr_host, port=settings.rtsp_port, username=settings.nvr_username,
                        password=settings.nvr_password, track=c.main_track if stream == "main" else c.sub_track,
                        start=row["started_at"], end=end, mode=TimestampMode(mode or row["timestamp_mode"]),
                        nvr_timezone=settings.nvr_timezone, offset_minutes=offset if offset is not None else row["applied_offset"],
                        pre_roll=row["pre_roll"], post_roll=row["post_roll"], max_seconds=settings.max_clip_seconds,
                        path_template=settings.rtsp_path_template, channel=c.nvr_channel, stream=stream)


async def handle_state(event: dict):
    data = event.get("data", {}); entity = data.get("entity_id")
    c = next((x for x in settings.channels() if x.enabled and x.motion_entity == entity), None)
    if not c: return
    old, new = data.get("old_state") or {}, data.get("new_state") or {}
    if not new: return
    event_id = db.transition(c.id, entity, old.get("state"), new.get("state"), new.get("last_changed") or event.get("time_fired"),
                             {"context_id": new.get("context", {}).get("id")}, settings.pre_roll_seconds,
                             settings.post_roll_seconds, settings.timestamp_mode,
                             c.timestamp_offset_minutes if c.timestamp_offset_minutes is not None else settings.manual_offset_minutes,
                             settings.merge_gap_seconds)
    if event_id and new.get("state") == "on" and c.camera_entity:
        asyncio.create_task(quick_snapshot(event_id, c.camera_entity))
    if event_id and new.get("state") == "off": asyncio.create_task(final_thumbnail(event_id))


async def quick_snapshot(event_id: str, entity: str):
    try:
        content, _ = await ha.snapshot(entity); name = safe_name(event_id) + ".jpg"
        temp = media.tmp / (name + ".tmp"); final = media.thumbs / name
        temp.write_bytes(content)
        with Image.open(temp) as image: image.verify()
        temp.replace(final); db.update(event_id, thumbnail_status="ready", thumbnail_name=name, thumbnail_source="ha_live")
    except Exception as exc:
        db.update(event_id, thumbnail_status="failed", last_error=redact(exc))


async def final_thumbnail(event_id: str):
    await asyncio.sleep(8)
    row = db.event(event_id)
    if not row: return
    try:
        request = event_playback(row, stream=channel(row["channel_id"]).thumbnail_stream)
        name = await media.thumbnail(event_id, request.url)
        db.update(event_id, thumbnail_status="ready", thumbnail_name=name, thumbnail_source="nvr_historical", status="ready")
    except Exception as exc:
        db.update(event_id, thumbnail_status="partial" if row.get("thumbnail_name") else "failed", status="partial",
                  last_error=redact(exc, (settings.nvr_username, settings.nvr_password)))


async def reconcile():
    for c in settings.channels():
        if not c.enabled or not c.motion_entity: continue
        try:
            states = await ha.history(c.motion_entity, settings.history_backfill_hours)
            previous = None
            for state in states:
                value = state.get("state")
                if value in {"on", "off"}:
                    db.transition(c.id, c.motion_entity, previous, value, state.get("last_changed") or state.get("last_updated"), {},
                                  settings.pre_roll_seconds, settings.post_roll_seconds, settings.timestamp_mode,
                                  c.timestamp_offset_minutes or settings.manual_offset_minutes, settings.merge_gap_seconds)
                    previous = value
        except Exception as exc: log.warning("History reconciliation failed for channel %s: %s", c.id, redact(exc))


@asynccontextmanager
async def lifespan(app):
    stop_event.clear(); media.cleanup()
    tasks = [asyncio.create_task(ha.subscribe(handle_state, stop_event)), asyncio.create_task(reconcile())]
    yield
    stop_event.set()
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for task in list(media.jobs.values()): task.cancel()


app = FastAPI(title="CCTV Event Timeline", version="0.1.2", lifespan=lifespan, docs_url=None, redoc_url=None)
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")


@app.middleware("http")
async def secure(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site == "cross-site": return JSONResponse({"error": {"code": "cross_origin", "message": "Cross-origin request rejected"}}, 403)
    response = await call_next(request)
    response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                             "Content-Security-Policy": "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'self'"})
    return response


@app.exception_handler(Exception)
async def errors(request, exc):
    log.error("Request failed: %s", redact(exc, (settings.nvr_username, settings.nvr_password)))
    return JSONResponse({"error": {"code": "internal_error", "message": redact(exc, (settings.nvr_username, settings.nvr_password))}}, 500)


@app.get("/", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
@app.get("/diagnostics", response_class=HTMLResponse)
async def index(): return (static / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
async def health():
    return {"status": "ok", "database": str(db.path.exists()), "home_assistant_last_connected": ha.last_connected,
            "media": media.health(), "enabled_channels": sum(c.enabled for c in settings.channels())}


@app.get("/api/config")
async def config(): return {"settings": settings.safe_summary(), "channels": [c.model_dump() for c in settings.channels()]}


@app.put("/api/channels")
async def save_channels(body: ChannelList): settings.save_channels(body); return {"ok": True, "channels": [c.model_dump() for c in body.channels]}


@app.get("/api/entities")
async def entities(): return await ha.entities()


@app.get("/api/events")
async def events(date: str, channel_id: int | None = Query(None, ge=1, le=8), status: str | None = None,
                 limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    try:
        day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(422, "Date must use YYYY-MM-DD") from None
    return {"items": [public_event(x) for x in db.list_events(day.isoformat(), (day + timedelta(days=1)).isoformat(), channel_id, status, limit, offset)]}


@app.get("/api/events/{event_id}")
async def event(event_id: str):
    row = db.event(event_id)
    if not row: raise HTTPException(404, "Event not found")
    return public_event(row)


@app.get("/api/events/{event_id}/thumbnail")
async def thumbnail(event_id: str):
    row = db.event(event_id)
    if not row or not row.get("thumbnail_name"): raise HTTPException(404, "Thumbnail unavailable")
    path = media.thumbs / (safe_name(Path(row["thumbnail_name"]).stem) + ".jpg")
    if not path.is_file(): raise HTTPException(404, "Thumbnail unavailable")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/events/{event_id}/generate")
async def generate(event_id: str):
    row = db.event(event_id)
    if not row: raise HTTPException(404, "Event not found")
    if not settings.nvr_username or not settings.nvr_password: raise HTTPException(409, "NVR credentials are not configured")
    request = event_playback(row); media.enqueue(event_id, request.url)
    return {"status": db.event(event_id)["video_status"] if event_id not in media.jobs else "generating"}


@app.delete("/api/events/{event_id}/video")
async def delete_video(event_id: str):
    row = db.event(event_id)
    if not row: raise HTTPException(404, "Event not found")
    media.delete_clip(row); return {"ok": True}


@app.get("/api/events/{event_id}/video")
async def video(event_id: str, request: Request):
    row = db.event(event_id)
    if not row or row.get("video_status") != "ready" or not row.get("video_name"): raise HTTPException(404, "Video unavailable")
    path = media.videos / (safe_name(Path(row["video_name"]).stem) + ".mp4")
    if not path.is_file(): raise HTTPException(404, "Video unavailable")
    size = path.stat().st_size; range_header = request.headers.get("range")
    if not range_header: return FileResponse(path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})
    try:
        spec = range_header.removeprefix("bytes=").split(",")[0]; first, last = spec.split("-")
        start = int(first) if first else max(0, size - int(last)); end = min(int(last) if last else size - 1, size - 1)
        if start > end: raise ValueError
    except ValueError: return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    def chunks():
        with path.open("rb") as fh:
            fh.seek(start); remaining = end - start + 1
            while remaining:
                chunk = fh.read(min(65536, remaining))
                if not chunk: break
                remaining -= len(chunk); yield chunk
    return StreamingResponse(chunks(), 206, media_type="video/mp4", headers={"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes", "Content-Length": str(end-start+1)})


@app.post("/api/diagnostics/health")
async def diag_health():
    started = time.monotonic(); test = settings.data_dir / ".write-test"; test.write_text("ok"); test.unlink()
    result = {"status": "pass", "duration_ms": round((time.monotonic()-started)*1000), "database_writable": True, **media.health()}
    db.store_diagnostic("health", result); return result


@app.post("/api/diagnostics/home-assistant")
async def diag_ha():
    started = time.monotonic(); info = await ha.info(); result = {"status": "pass", "duration_ms": round((time.monotonic()-started)*1000), **info}
    db.store_diagnostic("home_assistant", result); return result


@app.post("/api/diagnostics/entities")
async def diag_entities():
    found = await ha.entities(); ids = {x["entity_id"] for x in found["motion"] + found["cameras"]}
    mappings = [{"channel": c.id, "motion_exists": c.motion_entity in ids, "camera_exists": c.camera_entity in ids} for c in settings.channels()]
    result = {"status": "pass", "entities": found, "mappings": mappings}; db.store_diagnostic("entities", result); return result


@app.post("/api/diagnostics/network")
async def diag_network():
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(settings.nvr_host, settings.rtsp_port), 5); writer.close(); await writer.wait_closed(); status, detail = "pass", "TCP connection succeeded"
    except Exception as exc: status, detail = "fail", redact(exc)
    result = {"status": status, "detail": detail, "host": settings.nvr_host, "port": settings.rtsp_port, "duration_ms": round((time.monotonic()-started)*1000)}
    db.store_diagnostic("network", result); return result


@app.post("/api/diagnostics/playback-url")
async def diag_url(test: PlaybackTest):
    c = channel(test.channel_id)
    if not c: raise HTTPException(404, "Channel not found")
    end = test.start + timedelta(seconds=test.duration_seconds)
    request = playback_url(host=settings.nvr_host, port=settings.rtsp_port, username=settings.nvr_username, password=settings.nvr_password,
                           track=c.main_track if test.stream == "main" else c.sub_track, start=test.start, end=end,
                           mode=TimestampMode(test.mode or settings.timestamp_mode), nvr_timezone=settings.nvr_timezone,
                           offset_minutes=test.offset_minutes if test.offset_minutes is not None else settings.manual_offset_minutes,
                           max_seconds=30, path_template=settings.rtsp_path_template,
                           channel=c.nvr_channel, stream=test.stream)
    return {"status": "pass", "redacted_url": request.redacted_url, "start_utc": request.start_utc,
            "end_utc": request.end_utc, "playback_start": request.playback_start, "playback_end": request.playback_end}


@app.post("/api/diagnostics/live-probe")
async def diag_probe(test: PlaybackTest):
    c = channel(test.channel_id); now = datetime.now(timezone.utc)
    req = playback_url(host=settings.nvr_host, port=settings.rtsp_port, username=settings.nvr_username, password=settings.nvr_password,
                       track=c.main_track if test.stream == "main" else c.sub_track, start=now-timedelta(seconds=10), end=now,
                       mode=TimestampMode.UTC, nvr_timezone=settings.nvr_timezone, max_seconds=15,
                       path_template=settings.rtsp_path_template, channel=c.nvr_channel, stream=test.stream)
    result = {"status": "pass", "probe": await media.probe(req.url), "url": req.redacted_url}; db.store_diagnostic("probe", result); return result


@app.post("/api/diagnostics/motion-history")
async def diag_history(test: HistoryTest):
    c = channel(test.channel_id)
    if not c or not c.motion_entity:
        raise HTTPException(409, "The selected channel has no motion entity mapping")
    states = await ha.history(c.motion_entity, test.hours)
    transitions = [{"state": x.get("state"), "time": x.get("last_changed") or x.get("last_updated")}
                   for x in states if x.get("state") in {"on", "off"}]
    pairs, opened = [], None
    for item in transitions:
        if item["state"] == "on" and opened is None:
            opened = item["time"]
        elif item["state"] == "off" and opened:
            pairs.append({"start": opened, "end": item["time"]})
            if test.import_events:
                db.transition(c.id, c.motion_entity, "off", "on", opened, {}, settings.pre_roll_seconds,
                              settings.post_roll_seconds, settings.timestamp_mode,
                              c.timestamp_offset_minutes or settings.manual_offset_minutes, settings.merge_gap_seconds)
                db.transition(c.id, c.motion_entity, "on", "off", item["time"], {}, settings.pre_roll_seconds,
                              settings.post_roll_seconds, settings.timestamp_mode,
                              c.timestamp_offset_minutes or settings.manual_offset_minutes, settings.merge_gap_seconds)
            opened = None
    result = {"status": "pass", "dry_run": not test.import_events, "transitions": transitions, "pairs": pairs}
    db.store_diagnostic("motion_history", result)
    return result


@app.post("/api/diagnostics/ha-thumbnail")
async def diag_ha_thumbnail(test: PlaybackTest):
    c = channel(test.channel_id)
    if not c or not c.camera_entity:
        raise HTTPException(409, "The selected channel has no camera entity mapping")
    started = time.monotonic(); content, mime = await ha.snapshot(c.camera_entity)
    name = f"diag_ha_{c.id}.jpg"; temp = media.tmp / f"{name}.tmp"; final = media.thumbs / name
    temp.write_bytes(content)
    with Image.open(temp) as image:
        width, height = image.size
        image.verify()
    temp.replace(final)
    return {"status": "pass", "mime_type": mime, "bytes": len(content), "width": width, "height": height,
            "duration_ms": round((time.monotonic()-started)*1000), "image_url": f"api/diagnostics/media/{name}"}


def diagnostic_request(test: PlaybackTest, mode: str | None = None):
    c = channel(test.channel_id)
    if not c:
        raise HTTPException(404, "Channel not found")
    return playback_url(host=settings.nvr_host, port=settings.rtsp_port, username=settings.nvr_username,
                        password=settings.nvr_password, track=c.main_track if test.stream == "main" else c.sub_track,
                        start=test.start, end=test.start + timedelta(seconds=test.duration_seconds),
                        mode=TimestampMode(mode or test.mode or settings.timestamp_mode), nvr_timezone=settings.nvr_timezone,
                        offset_minutes=test.offset_minutes if test.offset_minutes is not None else settings.manual_offset_minutes,
                        max_seconds=30, path_template=settings.rtsp_path_template,
                        channel=c.nvr_channel, stream=test.stream)


@app.post("/api/diagnostics/historical-thumbnail")
async def diag_historical_thumbnail(test: PlaybackTest):
    req = diagnostic_request(test)
    name = await media.thumbnail(f"diag_nvr_{test.channel_id}", req.url)
    return {"status": "pass", "redacted_url": req.redacted_url, "image_url": f"api/diagnostics/media/{name}"}


@app.post("/api/diagnostics/historical-video")
async def diag_historical_video(test: PlaybackTest):
    req = diagnostic_request(test); diagnostic_id = f"diag_video_{test.channel_id}"
    await media.clip(diagnostic_id, req.url)
    path = media.videos / f"{diagnostic_id}.mp4"
    if not path.exists():
        raise HTTPException(502, "Historical video generation failed; inspect sanitised add-on logs")
    return {"status": "pass", "redacted_url": req.redacted_url, "bytes": path.stat().st_size,
            "video_url": f"api/diagnostics/media/{path.name}"}


@app.post("/api/diagnostics/calibration")
async def diag_calibration(test: PlaybackTest):
    attempts = []
    for mode in ("utc", "nvr_local", "manual_offset"):
        req = diagnostic_request(test, mode)
        try:
            probe = await media.probe(req.url)
            attempts.append({"mode": mode, "status": "pass", "redacted_url": req.redacted_url,
                             "playback_start": req.playback_start, "playback_end": req.playback_end, "probe": probe})
        except Exception as exc:
            attempts.append({"mode": mode, "status": "fail", "redacted_url": req.redacted_url,
                             "playback_start": req.playback_start, "playback_end": req.playback_end,
                             "detail": redact(exc, (settings.nvr_username, settings.nvr_password))})
    result = {"status": "pass" if any(x["status"] == "pass" for x in attempts) else "fail", "attempts": attempts}
    db.store_diagnostic("calibration", result)
    return result


@app.get("/api/diagnostics/media/{name}")
async def diag_media(name: str):
    safe_name(Path(name).stem)
    suffix = Path(name).suffix.lower()
    if suffix == ".jpg":
        path, mime = media.thumbs / name, "image/jpeg"
    elif suffix == ".mp4":
        path, mime = media.videos / name, "video/mp4"
    else:
        raise HTTPException(404, "Diagnostic media not found")
    if not path.is_file() or not path.name.startswith("diag_"):
        raise HTTPException(404, "Diagnostic media not found")
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store", "Accept-Ranges": "bytes"})


@app.get("/api/diagnostics/report")
async def report(): return {"version": "0.1.2", "generated_at": datetime.now(timezone.utc), "configuration": settings.safe_summary(),
                            "channels": [{**c.model_dump(), "motion_entity": c.motion_entity, "camera_entity": c.camera_entity} for c in settings.channels()],
                            "health": media.health(), "home_assistant_last_connected": ha.last_connected, "test_results": db.diagnostics()}
