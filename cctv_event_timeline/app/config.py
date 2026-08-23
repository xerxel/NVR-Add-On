import json
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from .hikvision import default_tracks, render_path
from .models import Channel, ChannelList


@dataclass
class Settings:
    data_dir: Path
    nvr_host: str
    rtsp_port: int
    rtsp_path_template: str
    nvr_username: str
    nvr_password: str
    nvr_timezone: str
    timestamp_mode: str
    manual_offset_minutes: int
    rtsp_transport: str
    pre_roll_seconds: int
    post_roll_seconds: int
    max_clip_seconds: int
    merge_gap_seconds: int
    media_retention_days: int
    max_cache_mb: int
    ffmpeg_timeout_seconds: int
    max_concurrent_jobs: int
    history_backfill_hours: int
    log_level: str

    @classmethod
    def load(cls) -> "Settings":
        options_file = Path(os.getenv("OPTIONS_FILE", "/data/options.json"))
        raw = json.loads(options_file.read_text()) if options_file.exists() else {}
        data_dir = Path(os.getenv("TIMELINE_DATA", "/data/timeline"))
        tz = raw.get("nvr_timezone", "Europe/London")
        ZoneInfo(tz)
        path_template = raw.get("rtsp_path_template", "/Streaming/tracks/{track}")
        render_path(path_template, track="101", channel=1, stream="main")
        return cls(data_dir, raw.get("nvr_host", "192.168.0.100"), int(raw.get("rtsp_port", 554)), path_template,
                   raw.get("nvr_username", ""), raw.get("nvr_password", ""), tz,
                   raw.get("timestamp_mode", "utc"), int(raw.get("manual_offset_minutes", 0)),
                   raw.get("rtsp_transport", "tcp"), int(raw.get("pre_roll_seconds", 5)),
                   int(raw.get("post_roll_seconds", 10)), int(raw.get("max_clip_seconds", 180)),
                   int(raw.get("merge_gap_seconds", 5)), int(raw.get("media_retention_days", 14)),
                   int(raw.get("max_cache_mb", 2048)), int(raw.get("ffmpeg_timeout_seconds", 120)),
                   int(raw.get("max_concurrent_jobs", 1)), int(raw.get("history_backfill_hours", 24)),
                   raw.get("log_level", "info"))

    def channel_file(self) -> Path:
        return self.data_dir / "channels.json"

    def channels(self) -> list[Channel]:
        path = self.channel_file()
        if path.exists():
            return ChannelList.model_validate_json(path.read_text()).channels
        result = []
        for i in range(1, 9):
            main, sub = default_tracks(i)
            result.append(Channel(id=i, enabled=False, name=f"Camera {i}", nvr_channel=i,
                                  motion_entity=f"binary_sensor.network_video_recorder_channel_{i}_motion",
                                  camera_entity=f"camera.network_video_recorder_channel_{i}",
                                  main_track=main, sub_track=sub))
        return result

    def save_channels(self, channels: ChannelList) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        target = self.channel_file()
        temp = target.with_suffix(".tmp")
        temp.write_text(channels.model_dump_json(indent=2))
        temp.replace(target)

    def safe_summary(self) -> dict:
        return {"nvr_host": self.nvr_host, "rtsp_port": self.rtsp_port, "rtsp_path_template": self.rtsp_path_template,
                "credentials_configured": bool(self.nvr_username and self.nvr_password),
                "nvr_timezone": self.nvr_timezone, "timestamp_mode": self.timestamp_mode,
                "manual_offset_minutes": self.manual_offset_minutes, "rtsp_transport": self.rtsp_transport,
                "pre_roll_seconds": self.pre_roll_seconds, "post_roll_seconds": self.post_roll_seconds,
                "max_clip_seconds": self.max_clip_seconds, "media_retention_days": self.media_retention_days,
                "max_cache_mb": self.max_cache_mb, "max_concurrent_jobs": self.max_concurrent_jobs}
