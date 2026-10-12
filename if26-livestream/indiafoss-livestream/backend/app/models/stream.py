"""Pydantic models for IndiaFOSS 2026 livestream backend.

Directly matches the frontend state model (AdminStreamState in adminState.ts)
to guarantee zero contract mismatches.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

try:
    from pydantic import BaseModel, Field
except ImportError:
    # Lightweight fallback for local environments without pydantic installed
    import json

    class BaseModel:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self, mode=None):
            data = {}
            for klass in reversed(type(self).__mro__):
                for k in getattr(klass, "__annotations__", {}):
                    if not k.startswith("_"):
                        data[k] = getattr(self, k, None)
            data.update({k: v for k, v in self.__dict__.items() if not k.startswith("_")})
            if mode == "json":
                data = {k: (v.value if isinstance(v, Enum) else v) for k, v in data.items()}
            return data

        def model_dump_json(self):
            return json.dumps(self.model_dump(mode="json"))

        def dict(self):
            return self.model_dump()

    def Field(*args, **kwargs):  # type: ignore
        return None


class StreamStatusEnum(str, Enum):
    LIVE = "live"
    STARTING = "starting"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"
    ERROR = "error"
    STOPPING = "stopping"


class PlaybackModeEnum(str, Enum):
    AUTO = "auto"  # restream; YouTube embed only after a sustained outage
    RESTREAM = "restream"  # never fall back
    YOUTUBE = "youtube"  # always show the YouTube embed


class ServiceHealthEnum(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class SessionConfig(BaseModel):
    """One scheduled YouTube broadcast played by a room (imported by scripts/import-schedule.py)."""
    slug: str
    title: str
    youtubeVideoId: str
    startsAt: str  # ISO 8601
    thumbnail: str = ""


class SessionStatus(SessionConfig):
    # "upcoming" | "current" | "ended", relative to the room's current session
    state: str = "upcoming"


class StreamConfig(BaseModel):
    id: str
    # URL-friendly room name, e.g. "audi-1" (room link /audi-1)
    slug: Optional[str] = None
    index: int = 0
    streamNumber: str
    title: str
    devroom: str = "DEVROOM"
    partner: Optional[str] = None
    time: str = ""
    thumbnail: str
    accentColor: str = "#3b82f6"
    route: str
    sourceUrl: str
    quality: str = "1080p60"
    order: int = 1
    enabled: bool = True
    # ISO 8601 start of the track's session, shown to viewers while offline
    startsAt: Optional[str] = None
    # Operator's intent, persisted so a controller restart resumes running streams
    desiredState: str = "STOPPED"
    playbackMode: PlaybackModeEnum = PlaybackModeEnum.AUTO
    # The room's scheduled sessions, the one it plays now, and whether it moves on by itself
    sessions: List[SessionConfig] = []
    currentSession: Optional[str] = None
    autoAdvance: bool = True


class StreamUpdateRequest(BaseModel):
    sourceUrl: Optional[str] = None
    quality: Optional[str] = None
    title: Optional[str] = None
    order: Optional[int] = None
    thumbnail: Optional[str] = None
    playbackMode: Optional[PlaybackModeEnum] = None
    autoAdvance: Optional[bool] = None


class SessionSwitchRequest(BaseModel):
    slug: str


class StreamStatusResponse(BaseModel):
    id: str
    slug: Optional[str] = None
    index: int
    streamNumber: str
    title: str
    devroom: str
    partner: Optional[str] = None
    time: str = ""
    thumbnail: str
    accentColor: str
    route: str

    status: StreamStatusEnum
    desiredState: str = "STOPPED"
    # True while Streamlink waits for the YouTube broadcast to go live
    waitingForSource: bool = False
    startsAt: Optional[str] = None
    quality: str
    # Not measured yet (MediaMTX reader counts are not viewer counts)
    viewers: Optional[int] = None
    sourceUrl: str
    youtubeVideoId: Optional[str] = None
    playbackMode: PlaybackModeEnum = PlaybackModeEnum.AUTO
    # What viewers are shown right now: "restream" or "youtube"
    playback: str = "restream"
    currentSession: Optional[str] = None
    autoAdvance: bool = True
    sessions: List[SessionStatus] = []
    order: int
    uptime: str = "0m"
    restartCount: int = 0
    lastError: Optional[str] = None
    lastRecovery: Optional[str] = None
    bitrate: str = "—"
    resolution: str = "—"
    fps: str = "—"

    sourceHealth: ServiceHealthEnum = ServiceHealthEnum.UNKNOWN
    streamlinkHealth: ServiceHealthEnum = ServiceHealthEnum.UNKNOWN
    ffmpegHealth: ServiceHealthEnum = ServiceHealthEnum.UNKNOWN
    mediaOriginHealth: ServiceHealthEnum = ServiceHealthEnum.UNKNOWN
    hlsHealth: ServiceHealthEnum = ServiceHealthEnum.UNKNOWN


class ServiceStatusItem(BaseModel):
    name: str
    key: str
    health: ServiceHealthEnum
    detail: Optional[str] = None


class SystemHealthResponse(BaseModel):
    overall: ServiceHealthEnum
    cpu: float
    memoryUsed: float
    memoryTotal: float
    networkBandwidth: str
    services: List[ServiceStatusItem]
    streamsHealthy: int
    streamsTotal: int
    totalViewers: Optional[int] = None


class LogEventItem(BaseModel):
    id: str
    timestamp: str
    time: str
    streamId: Optional[str] = None
    streamLabel: Optional[str] = None
    level: str = "info"
    message: str
    text: str
    detail: Optional[str] = None
    type: str = "info"
