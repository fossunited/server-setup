"""Viewer counting and event telemetry, written as JSON lines for analysis after the event.

Nothing here may affect streaming: every write is buffered, size-capped and
best-effort, and a failing disk disables telemetry instead of raising.
No IP addresses are recorded; viewers are identified by a random per-tab ID.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("telemetry")

VIEWER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
WATCH_KEY_RE = re.compile(r"^[a-z0-9-]{1,40}$")
# A viewer counts while their page has checked in within this window (pages poll every 5s)
VIEWER_WINDOW_SECONDS = 15.0
MAX_TRACKED_VIEWERS = 20_000


class ViewerTracker:
    """Counts distinct viewers per page from the regular /api/streams check-ins."""

    def __init__(self, window: float = VIEWER_WINDOW_SECONDS) -> None:
        self.window = window
        self._seen: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.Lock()

    def touch(self, viewer_id: str, watching: str, now: Optional[float] = None) -> bool:
        if not VIEWER_ID_RE.match(viewer_id or "") or not WATCH_KEY_RE.match(watching or ""):
            return False
        now = time.monotonic() if now is None else now
        with self._lock:
            if viewer_id not in self._seen and len(self._seen) >= MAX_TRACKED_VIEWERS:
                self._prune(now)
                if len(self._seen) >= MAX_TRACKED_VIEWERS:
                    return False
            self._seen[viewer_id] = (watching, now)
        return True

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        for vid in [v for v, (_, seen) in self._seen.items() if seen < cutoff]:
            del self._seen[vid]

    def counts(self, now: Optional[float] = None) -> Dict[str, int]:
        """Viewers per watch key (a room id such as "1", "home", or "session")."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            result: Dict[str, int] = {}
            for watching, _ in self._seen.values():
                result[watching] = result.get(watching, 0) + 1
            return result


# ------------------------------------------------------------------
# Player reports (POST /api/telemetry)
# ------------------------------------------------------------------

_STR_FIELDS = {
    "vid": 40, "kind": 20, "page": 20, "room": 10, "session": 60, "playback": 20, "phase": 20,
    "device": 20, "os": 20, "browser": 20, "net": 10, "lang": 20, "reason": 40,
}
_NUM_FIELDS = {
    "seq", "watchSec", "stalls", "stallSec", "rebuilds", "ttffMs", "height", "bitrateKbps",
    "droppedFrames", "totalFrames", "latencySec", "bufferSec", "screenW", "screenH", "dpr",
    "downlinkMbps", "hiddenSec", "tzOffsetMin",
}
_BOOL_FIELDS = {"muted", "fullscreen", "hidden", "autoplayBlocked"}
_KINDS = {"start", "beat", "end", "error"}
MAX_ERRORS_PER_REPORT = 10


def sanitize_player_report(raw: Any) -> Optional[Dict[str, Any]]:
    """Keep only known fields with bounded sizes; None if the report is unusable."""
    if not isinstance(raw, dict):
        return None
    vid = raw.get("vid")
    if not isinstance(vid, str) or not VIEWER_ID_RE.match(vid) or raw.get("kind") not in _KINDS:
        return None
    out: Dict[str, Any] = {}
    for key, limit in _STR_FIELDS.items():
        value = raw.get(key)
        if isinstance(value, str) and value:
            out[key] = value[:limit]
    for key in _NUM_FIELDS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) < 1e9:
            out[key] = round(float(value), 2) if isinstance(value, float) else value
    for key in _BOOL_FIELDS:
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    errors = raw.get("errors")
    if isinstance(errors, list):
        out["errors"] = [str(e)[:80] for e in errors[:MAX_ERRORS_PER_REPORT]]
    return out


class RateLimiter:
    """Per-viewer and global caps so a misbehaving or hostile client can't flood the disk."""

    def __init__(self, per_viewer_per_minute: int = 12, global_per_second: int = 60) -> None:
        self.per_viewer = per_viewer_per_minute
        self.global_per_second = global_per_second
        self._viewer_hits: Dict[str, Deque[float]] = {}
        self._global_hits: Deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self, viewer_id: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            while self._global_hits and now - self._global_hits[0] > 1.0:
                self._global_hits.popleft()
            if len(self._global_hits) >= self.global_per_second:
                return False
            hits = self._viewer_hits.setdefault(viewer_id, deque())
            while hits and now - hits[0] > 60.0:
                hits.popleft()
            if len(hits) >= self.per_viewer:
                return False
            hits.append(now)
            self._global_hits.append(now)
            if len(self._viewer_hits) > MAX_TRACKED_VIEWERS:
                for vid in [v for v, h in self._viewer_hits.items() if not h or now - h[-1] > 60.0]:
                    del self._viewer_hits[vid]
            return True


# ------------------------------------------------------------------
# JSON-lines writer
# ------------------------------------------------------------------

class TelemetryWriter:
    """Buffers records in memory and appends them to <dir>/<stream>-<UTC date>.jsonl on flush()."""

    def __init__(self, directory: Optional[str], max_total_bytes: int = 1_000_000_000) -> None:
        self.directory = Path(directory) if directory else None
        self.max_total_bytes = max_total_bytes
        self.enabled = self.directory is not None
        self.bytes_written = 0
        self.dropped = 0
        self._buffer: List[Tuple[str, str]] = []
        self._lock = threading.Lock()
        if self.directory is not None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.bytes_written = sum(p.stat().st_size for p in self.directory.glob("*.jsonl"))
            except OSError as e:
                logger.warning("Telemetry disabled: %s is not usable (%s)", self.directory, e)
                self.enabled = False

    def record(self, stream: str, data: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **data}
        line = json.dumps(entry, separators=(",", ":"), default=str)
        with self._lock:
            if len(self._buffer) >= 50_000:  # flushes are failing or stalled; don't grow memory
                self.dropped += 1
                return
            self._buffer.append((stream, line))

    def flush(self) -> None:
        """Write buffered lines (call from a thread; never raises)."""
        if not self.enabled or self.directory is None:
            return
        with self._lock:
            pending, self._buffer = self._buffer, []
        if not pending:
            return
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        by_stream: Dict[str, List[str]] = {}
        for stream, line in pending:
            by_stream.setdefault(stream, []).append(line)
        for stream, lines in by_stream.items():
            payload = "\n".join(lines) + "\n"
            size = len(payload.encode())
            if self.bytes_written + size > self.max_total_bytes:
                self.dropped += len(lines)
                continue
            try:
                with open(self.directory / f"{stream}-{day}.jsonl", "a", encoding="utf-8") as f:
                    f.write(payload)
                self.bytes_written += size
            except OSError as e:
                logger.warning("Telemetry write failed, disabling: %s", e)
                self.enabled = False
                return


# ------------------------------------------------------------------
# Host network counters (bind-mounted from the host's /sys/class/net/<if>/statistics)
# ------------------------------------------------------------------

class NetworkMeter:
    def __init__(self, stats_dir: Optional[str]) -> None:
        self.stats_dir = Path(stats_dir) if stats_dir else None
        self._last: Optional[Tuple[float, int, int]] = None
        self.tx_mbps: Optional[float] = None
        self.rx_mbps: Optional[float] = None
        self.tx_total: Optional[int] = None
        self.rx_total: Optional[int] = None

    def _read(self, name: str) -> Optional[int]:
        if self.stats_dir is None:
            return None
        try:
            return int((self.stats_dir / name).read_text().strip())
        except (OSError, ValueError):
            return None

    def sample(self, now: Optional[float] = None) -> None:
        tx, rx = self._read("tx_bytes"), self._read("rx_bytes")
        if tx is None or rx is None:
            return
        now = time.monotonic() if now is None else now
        if self._last is not None:
            elapsed = now - self._last[0]
            if elapsed > 0 and tx >= self._last[1] and rx >= self._last[2]:
                self.tx_mbps = round((tx - self._last[1]) * 8 / elapsed / 1_000_000, 1)
                self.rx_mbps = round((rx - self._last[2]) * 8 / elapsed / 1_000_000, 1)
        self._last = (now, tx, rx)
        self.tx_total, self.rx_total = tx, rx


def format_bytes(n: Optional[int]) -> str:
    if n is None:
        return "—"
    for unit, size in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6)):
        if n >= size:
            return f"{n / size:.2f} {unit}"
    return f"{n / 1e3:.0f} KB"


viewers = ViewerTracker()
limiter = RateLimiter()
writer = TelemetryWriter(os.getenv("TELEMETRY_DIR") or None)
network = NetworkMeter(os.getenv("HOST_NET_STATS_DIR") or None)
