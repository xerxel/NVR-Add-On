from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from urllib.parse import quote
from zoneinfo import ZoneInfo


class TimestampMode(StrEnum):
    UTC = "utc"
    NVR_LOCAL = "nvr_local"
    MANUAL_OFFSET = "manual_offset"


def default_tracks(channel: int) -> tuple[str, str]:
    if channel not in range(1, 9):
        raise ValueError("Channel must be between 1 and 8")
    return f"{channel}01", f"{channel}02"


def parse_time(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return dt.astimezone(timezone.utc)


def hik_time(dt: datetime, mode: TimestampMode, nvr_timezone: str, offset_minutes: int = 0) -> str:
    utc = parse_time(dt)
    if mode == TimestampMode.NVR_LOCAL:
        interpreted = utc.astimezone(ZoneInfo(nvr_timezone)).replace(tzinfo=timezone.utc)
    elif mode == TimestampMode.MANUAL_OFFSET:
        interpreted = utc + timedelta(minutes=offset_minutes)
    else:
        interpreted = utc
    return interpreted.strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class PlaybackRequest:
    url: str
    redacted_url: str
    start_utc: datetime
    end_utc: datetime
    playback_start: str
    playback_end: str


def playback_url(*, host: str, port: int, username: str, password: str, track: str,
                 start: str | datetime, end: str | datetime, mode: TimestampMode,
                 nvr_timezone: str, offset_minutes: int = 0, pre_roll: int = 0,
                 post_roll: int = 0, max_seconds: int = 180) -> PlaybackRequest:
    start_utc = parse_time(start) - timedelta(seconds=pre_roll)
    requested_end = parse_time(end) + timedelta(seconds=post_roll)
    end_utc = min(requested_end, start_utc + timedelta(seconds=max_seconds))
    if end_utc <= start_utc or not track.isdigit() or len(track) > 8:
        raise ValueError("Invalid playback range or track")
    if not 1 <= port <= 65535 or any(c in host for c in "/?#@"):
        raise ValueError("Invalid NVR host or port")
    ps = hik_time(start_utc, mode, nvr_timezone, offset_minutes)
    pe = hik_time(end_utc, mode, nvr_timezone, offset_minutes)
    path = f"/Streaming/tracks/{track}?starttime={ps}&endtime={pe}"
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username or password else ""
    return PlaybackRequest(
        f"rtsp://{credentials}{host}:{port}{path}",
        f"rtsp://***:***@{host}:{port}{path}", start_utc, end_utc, ps, pe,
    )

