from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Channel(BaseModel):
    id: int = Field(ge=1, le=8)
    enabled: bool = False
    name: str = Field(min_length=1, max_length=80)
    nvr_channel: int = Field(ge=1, le=64)
    motion_entity: str = ""
    camera_entity: str = ""
    main_track: str
    sub_track: str
    timestamp_offset_minutes: int | None = Field(default=None, ge=-1440, le=1440)
    thumbnail_stream: Literal["main", "sub"] = "main"

    @field_validator("motion_entity")
    @classmethod
    def motion_domain(cls, value: str) -> str:
        if value and not value.startswith("binary_sensor."):
            raise ValueError("Motion entity must be a binary_sensor")
        return value

    @field_validator("camera_entity")
    @classmethod
    def camera_domain(cls, value: str) -> str:
        if value and not value.startswith("camera."):
            raise ValueError("Camera entity must be a camera")
        return value

    @field_validator("main_track", "sub_track")
    @classmethod
    def numeric_track(cls, value: str) -> str:
        if not value.isdigit() or len(value) > 8:
            raise ValueError("Track must be numeric")
        return value


class ChannelList(BaseModel):
    channels: list[Channel]

    @field_validator("channels")
    @classmethod
    def eight_unique(cls, value: list[Channel]) -> list[Channel]:
        if len(value) != 8 or len({c.id for c in value}) != 8:
            raise ValueError("Exactly eight unique channel slots are required")
        return sorted(value, key=lambda c: c.id)


class PlaybackTest(BaseModel):
    channel_id: int = Field(ge=1, le=8)
    start: datetime
    duration_seconds: int = Field(default=15, ge=1, le=30)
    mode: Literal["utc", "nvr_local", "manual_offset"] | None = None
    offset_minutes: int | None = Field(default=None, ge=-1440, le=1440)
    stream: Literal["main", "sub"] = "main"


class HistoryTest(BaseModel):
    channel_id: int = Field(ge=1, le=8)
    hours: int = Field(default=1, ge=1, le=24)
    import_events: bool = False


class VlcCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username", "password")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("VLC credentials cannot contain control characters")
        return value
