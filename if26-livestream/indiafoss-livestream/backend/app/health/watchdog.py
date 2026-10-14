"""Health watchdog for MediaMTX origin, RTSP ingestion, and HLS playlist progression.

A running process does NOT mean the stream is live.
Actual health requires:
1. Streamlink process alive
2. FFmpeg process alive
3. MediaMTX RTSP publisher connected and receiving new bytes
4. HLS playlist accessible

The watchdog also acts: a pipeline whose video stops flowing, or that never
produces video after opening the stream, is handed to StreamSupervisor.recover().
It also decides when viewers of an "auto" stream fall back to the YouTube embed.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Awaitable, Callable, Dict, Optional, Sequence
import urllib.error
import urllib.request

from ..streaming import schedule
from ..streaming.schedule import RoomTracker, ScheduledSession
from ..streaming.supervisor import DesiredState, StreamStatus, StreamSupervisor
from ..streaming.url_sanitizer import InvalidYouTubeURLError, canonicalize_youtube_url
from . import youtube_probe

logger = logging.getLogger("health_watchdog")

MEDIA_SEQUENCE_REGEX = re.compile(r"#EXT-X-MEDIA-SEQUENCE:(\d+)")
RESOLUTION_REGEX = re.compile(r"RESOLUTION=(\d+x\d+)")
FRAME_RATE_REGEX = re.compile(r"FRAME-RATE=([\d.]+)")

# Bitrate is averaged over this window: YouTube delivers media in multi-second bursts
BITRATE_WINDOW_SECONDS = 30.0
BITRATE_MIN_SPAN_SECONDS = 5.0


@dataclass
class StreamHealthSnapshot:
    stream_id: str
    status: StreamStatus
    processes_alive: bool
    rtsp_publisher_ready: bool
    hls_accessible: bool
    hls_sequence: int | None
    hls_advancing: bool
    viewers: int  # MediaMTX reader count (not a viewer count)
    last_error: str | None
    streamlink_alive: bool = False
    ffmpeg_alive: bool = False
    bitrate_bps: float | None = None
    origin_reachable: bool = False
    resolution: str | None = None
    fps: str | None = None


@dataclass
class PathInfo:
    ready: bool
    readers: int
    bytes_received: int | None


class HealthWatchdog:
    """Monitors MediaMTX and HLS output for all managed streams and triggers recovery."""

    def __init__(
        self,
        supervisors: Dict[str, StreamSupervisor],
        mediamtx_api_url: str = "http://mediamtx:9997",
        mediamtx_hls_url: str = "http://mediamtx:8888",
        poll_interval: float = 3.0,
        stall_threshold: float = 20.0,
        startup_timeout: float = 45.0,
        fallback_after: float = 300.0,
        fallback_return_after: float = 60.0,
        on_event: Callable[[str, str, str, str], None] | None = None,
        youtube_proxy: str | None = None,
        playability_probe_interval: float = 60.0,
        proxy_check_interval: float = 30.0,
        # Room schedules: lookup(stream_id) -> (sessions, current slug, auto-advance on?) or None
        schedule_lookup: Callable[[str], tuple[Sequence[ScheduledSession], Optional[str], bool] | None] | None = None,
        # on_advance(stream_id, next session slug, reason): switches the room (API layer)
        on_advance: Callable[[str, str, str], Awaitable[None]] | None = None,
    ):
        self.supervisors = supervisors
        self.mediamtx_api_url = mediamtx_api_url.rstrip("/")
        self.mediamtx_hls_url = mediamtx_hls_url.rstrip("/")
        self.poll_interval = poll_interval
        self.stall_threshold = stall_threshold
        self.startup_timeout = startup_timeout
        self.fallback_after = fallback_after
        self.fallback_return_after = fallback_return_after
        # on_event(stream_id, level, message, detail) for the operator event log
        self.on_event = on_event
        # Proxy Streamlink uses for YouTube (e.g. Cloudflare WARP); None = direct
        self.youtube_proxy = youtube_proxy
        self.playability_probe_interval = playability_probe_interval
        self.proxy_check_interval = proxy_check_interval
        # None = no proxy configured / not checked yet
        self.proxy_ok: bool | None = None
        self.proxy_error: str | None = None
        self._last_proxy_check = 0.0
        self._proxy_task: Optional[asyncio.Task] = None
        self._last_probe: Dict[str, float] = {}
        self._probe_tasks: Dict[str, asyncio.Task] = {}
        self.schedule_lookup = schedule_lookup
        self.on_advance = on_advance
        self._room_trackers: Dict[str, RoomTracker] = {}
        self._advance_tasks: Dict[str, asyncio.Task] = {}

        self._last_sequences: Dict[str, int] = {}
        self._last_progress_time: Dict[str, float] = {}
        self._last_bytes: Dict[str, tuple[int, float]] = {}
        self._byte_samples: Dict[str, deque] = {}
        self._variant_info: Dict[str, tuple[str | None, str | None]] = {}
        self._last_segment: Dict[str, str] = {}
        self._output_bad_since: Dict[str, float] = {}
        self._snapshots: Dict[str, StreamHealthSnapshot] = {}
        self._recovery_tasks: Dict[str, asyncio.Task] = {}
        self.origin_reachable = False
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._watchdog_loop())

    async def stop(self):
        self._running = False
        for task in [self._proxy_task, *self._probe_tasks.values(), *self._advance_tasks.values()]:
            if task and not task.done():
                task.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_snapshot(self, stream_id: str) -> Optional[StreamHealthSnapshot]:
        return self._snapshots.get(stream_id)

    def get_all_snapshots(self) -> Dict[str, StreamHealthSnapshot]:
        return dict(self._snapshots)

    async def _watchdog_loop(self):
        while self._running:
            try:
                await self.check_all_streams()
            except Exception as e:
                logger.error(f"Error in watchdog loop: {e}", exc_info=True)
            await asyncio.sleep(self.poll_interval)

    def _emit(self, stream_id: str, level: str, message: str, detail: str = "") -> None:
        if self.on_event:
            try:
                self.on_event(stream_id, level, message, detail)
            except Exception as e:
                logger.error(f"[Stream {stream_id}] Event callback failed: {e}")

    def _request_recovery(self, sup: StreamSupervisor, reason: str) -> None:
        """Run sup.recover() in the background, at most once at a time per stream."""
        task = self._recovery_tasks.get(sup.stream_id)
        if task and not task.done():
            return
        logger.warning(f"[Stream {sup.stream_id}] Requesting recovery: {reason}")
        self._recovery_tasks[sup.stream_id] = asyncio.create_task(sup.recover(reason))

    def _forget(self, stream_id: str) -> None:
        for tracker in (
            self._last_sequences,
            self._last_progress_time,
            self._last_bytes,
            self._byte_samples,
            self._variant_info,
            self._last_segment,
            self._output_bad_since,
        ):
            tracker.pop(stream_id, None)

    async def check_all_streams(self):
        now = time.time()
        self._maybe_check_proxy(now)
        if not any(s.desired_state == DesiredState.RUNNING and not s.dummy_mode for s in self.supervisors.values()):
            # No running stream updates origin_reachable, so check MediaMTX's API directly
            await asyncio.to_thread(self._check_origin)
        for stream_id, sup in self.supervisors.items():
            if sup.desired_state == DesiredState.STOPPED:
                self._forget(stream_id)
                self._snapshots[stream_id] = StreamHealthSnapshot(
                    stream_id=stream_id,
                    status=StreamStatus.OFFLINE,
                    processes_alive=False,
                    rtsp_publisher_ready=False,
                    hls_accessible=False,
                    hls_sequence=None,
                    hls_advancing=False,
                    viewers=0,
                    last_error=None,
                    origin_reachable=self.origin_reachable,
                )
            elif sup.dummy_mode:
                self._check_dummy_stream(sup)
            else:
                await self._check_stream(sup, now)
                self._update_schedule(sup, now)
            self._update_fallback(sup, now)

    def _check_dummy_stream(self, sup: StreamSupervisor) -> None:
        """Dummy processes carry no media: treat live processes as a live stream."""
        is_alive = sup._is_running()
        if is_alive:
            sup._set_status(StreamStatus.LIVE, "Dummy processes running")
        self._snapshots[sup.stream_id] = StreamHealthSnapshot(
            stream_id=sup.stream_id,
            status=sup.status,
            processes_alive=is_alive,
            rtsp_publisher_ready=is_alive,
            hls_accessible=is_alive,
            hls_sequence=None,
            hls_advancing=is_alive,
            viewers=0,
            last_error=sup.metrics.last_error,
            streamlink_alive=is_alive,
            ffmpeg_alive=is_alive,
            origin_reachable=True,
        )

    async def _check_stream(self, sup: StreamSupervisor, now: float) -> None:
        stream_id = sup.stream_id
        sl_alive, ff_alive = sup.process_liveness()
        processes_alive = sl_alive and ff_alive

        path = await asyncio.to_thread(self._fetch_path_info, stream_id)
        hls_accessible, seq, last_segment = await asyncio.to_thread(self._check_hls_playlist, stream_id)

        # Progress = MediaMTX received new bytes, or the playlist gained a segment
        progressed = False
        if path and path.bytes_received is not None:
            prev = self._last_bytes.get(stream_id)
            if prev is not None and path.bytes_received > prev[0]:
                progressed = True
            self._last_bytes[stream_id] = (path.bytes_received, now)
        else:
            self._last_bytes.pop(stream_id, None)
        bitrate_bps = self._update_bitrate(stream_id, now, path.bytes_received if path else None)
        if last_segment and last_segment != self._last_segment.get(stream_id):
            if stream_id in self._last_segment:
                progressed = True
            self._last_segment[stream_id] = last_segment
        if seq is not None:
            self._last_sequences[stream_id] = seq
        if progressed:
            self._last_progress_time[stream_id] = now

        last_progress = self._last_progress_time.get(stream_id)
        video_flowing = last_progress is not None and now - last_progress < self.stall_threshold
        output_ok = bool(path and path.ready) and hls_accessible
        if output_ok:
            self._output_bad_since.pop(stream_id, None)
        else:
            self._output_bad_since.setdefault(stream_id, now)

        if processes_alive:
            self._decide_running_stream(sup, now, video_flowing, output_ok, last_progress)
        if sup.status != StreamStatus.LIVE:
            self._maybe_probe_playability(sup, now)

        self._snapshots[stream_id] = StreamHealthSnapshot(
            stream_id=stream_id,
            status=sup.status,
            processes_alive=processes_alive,
            rtsp_publisher_ready=bool(path and path.ready),
            hls_accessible=hls_accessible,
            hls_sequence=seq,
            hls_advancing=video_flowing,
            viewers=path.readers if path else 0,
            last_error=sup.metrics.last_error,
            streamlink_alive=sl_alive,
            ffmpeg_alive=ff_alive,
            bitrate_bps=bitrate_bps if video_flowing else None,
            origin_reachable=self.origin_reachable,
            resolution=self._variant_info.get(stream_id, (None, None))[0],
            fps=self._variant_info.get(stream_id, (None, None))[1],
        )

    def _update_bitrate(self, stream_id: str, now: float, bytes_received: int | None) -> float | None:
        """Average ingest bitrate over the last BITRATE_WINDOW_SECONDS."""
        samples = self._byte_samples.setdefault(stream_id, deque())
        if bytes_received is None or (samples and bytes_received < samples[-1][1]):
            samples.clear()  # no publisher, or MediaMTX restarted the counter
        if bytes_received is None:
            return None
        samples.append((now, bytes_received))
        while samples and now - samples[0][0] > BITRATE_WINDOW_SECONDS:
            samples.popleft()
        span = samples[-1][0] - samples[0][0]
        if span < BITRATE_MIN_SPAN_SECONDS:
            return None
        return (samples[-1][1] - samples[0][1]) * 8 / span

    def _decide_running_stream(
        self,
        sup: StreamSupervisor,
        now: float,
        video_flowing: bool,
        output_ok: bool,
        last_progress: float | None,
    ) -> None:
        """Both processes are alive: set status and recover stalled pipelines."""
        if video_flowing and output_ok:
            sup._set_status(StreamStatus.LIVE, "Video flowing")
            sup.metrics.last_error = None
            return

        if sup.waiting_for_source and not video_flowing:
            # Streamlink is waiting for YouTube. It hides YouTube's reason, so ask YouTube directly
            # whether the broadcast hasn't started or YouTube is blocking this server
            # (the probe itself is scheduled by _check_stream).
            detail = sup.source_error or "No live broadcast at this YouTube link yet: waiting for it to go live"
            if sup.source_blocked:
                detail = f"YouTube is blocking this server: {detail}"
            sup.metrics.last_error = detail
            if sup.status != StreamStatus.LIVE:
                sup._set_status(StreamStatus.STARTING, detail)
            return

        run_started = sup.stream_opened_at or sup.metrics.started_at or now
        flowed_this_run = last_progress is not None and last_progress >= run_started

        if sup.status == StreamStatus.LIVE or flowed_this_run:
            if not video_flowing:
                stalled_for = int(now - last_progress) if last_progress else int(now - run_started)
                sup._set_status(StreamStatus.RECONNECTING, "No new video")
                self._request_recovery(sup, f"No new video for {stalled_for}s")
            else:
                bad_since = self._output_bad_since.get(sup.stream_id, now)
                if now - bad_since > self.stall_threshold:
                    self._request_recovery(sup, "Video is arriving but the HLS output is unavailable")
        elif now - run_started > self.startup_timeout:
            self._request_recovery(sup, f"No video within {int(self.startup_timeout)}s of opening the stream")

    def _maybe_probe_playability(self, sup: StreamSupervisor, now: float) -> None:
        """Background playabilityStatus check, at most once per interval per stream."""
        stream_id = sup.stream_id
        task = self._probe_tasks.get(stream_id)
        if task and not task.done():
            return
        if now - self._last_probe.get(stream_id, 0.0) < self.playability_probe_interval:
            return
        try:
            url = canonicalize_youtube_url(sup.source_url)
        except InvalidYouTubeURLError:
            return
        self._last_probe[stream_id] = now
        probed_session = self._tracker(stream_id).session

        async def run() -> None:
            result = await asyncio.to_thread(youtube_probe.probe_playability, url, self.youtube_proxy)
            tracker = self._tracker(stream_id)
            if tracker.session == probed_session:
                schedule.record_playability(tracker, time.time(), result.status, result.is_live)
            if not sup.waiting_for_source:
                return  # Streamlink isn't waiting: nothing to explain to the operator
            was_blocked = sup.source_blocked
            sup.source_blocked = result.blocked
            sup.source_error = youtube_probe.describe(result)
            logger.info(f"[Stream {stream_id}] YouTube playability: {result.status} ({sup.source_error})")
            if result.blocked and not was_blocked:
                self._emit(stream_id, "error", "YouTube is blocking this server", sup.source_error)

        self._probe_tasks[stream_id] = asyncio.create_task(run())

    def _tracker(self, stream_id: str) -> RoomTracker:
        return self._room_trackers.setdefault(stream_id, RoomTracker())

    def _update_schedule(self, sup: StreamSupervisor, now: float) -> None:
        """Move a running room on to its next scheduled session when the rules say so."""
        if not self.schedule_lookup or sup.desired_state != DesiredState.RUNNING:
            return
        room = self.schedule_lookup(sup.stream_id)
        if not room:
            return
        sessions, current, auto_advance = room
        tracker = self._tracker(sup.stream_id)
        if tracker.session != current:
            tracker.reset(current)  # new session (automatic or manual switch)
        if sup.status == StreamStatus.LIVE:
            tracker.went_live = True
        if not auto_advance or not self.on_advance:
            return
        task = self._advance_tasks.get(sup.stream_id)
        if task and not task.done():
            return
        decision = schedule.decide_advance(sessions, current, tracker, now, sup.status == StreamStatus.LIVE)
        if decision:
            slug, reason = decision
            logger.info(f"[Stream {sup.stream_id}] Advancing from {current} to {slug}: {reason}")
            self._advance_tasks[sup.stream_id] = asyncio.create_task(self.on_advance(sup.stream_id, slug, reason))

    def _maybe_check_proxy(self, now: float) -> None:
        """End-to-end check of the YouTube proxy (WARP), in the background."""
        if not self.youtube_proxy:
            return
        if self._proxy_task and not self._proxy_task.done():
            return
        if now - self._last_proxy_check < self.proxy_check_interval:
            return
        self._last_proxy_check = now

        async def run() -> None:
            ok, error = await asyncio.to_thread(youtube_probe.check_proxy, self.youtube_proxy)
            if ok != self.proxy_ok:
                if ok:
                    logger.info("YouTube proxy is working")
                    if self.proxy_ok is False:
                        self._emit("System", "success", "YouTube proxy (WARP) recovered", "")
                else:
                    logger.error(f"YouTube proxy check failed: {error}")
                    self._emit("System", "error", "YouTube proxy (WARP) is not working", error or "")
            self.proxy_ok, self.proxy_error = ok, error

        self._proxy_task = asyncio.create_task(run())

    def _update_fallback(self, sup: StreamSupervisor, now: float) -> None:
        """Decide whether viewers of an "auto" stream should see the YouTube embed."""
        if sup.desired_state != DesiredState.RUNNING or sup.playback_mode != "auto":
            active = False
        elif sup.fallback_active:
            # Return to the restream once it has been live long enough
            active = not (
                sup.status == StreamStatus.LIVE
                and sup.live_since is not None
                and now - sup.live_since >= self.fallback_return_after
            )
        else:
            # The embed only helps when YouTube itself is live (or is blocking only us)
            source_offline = sup.waiting_for_source and not sup.source_blocked
            if source_offline and sup.down_since is not None:
                # Waiting for a scheduled broadcast is not an outage: restart the outage clock,
                # so the moment the broadcast starts doesn't look like hours of downtime.
                sup.down_since = now
            active = (
                sup.status != StreamStatus.LIVE
                and not source_offline
                and sup.down_since is not None
                and now - sup.down_since >= self.fallback_after
            )

        if active == sup.fallback_active:
            return
        sup.fallback_active = active
        if active:
            minutes = int((now - (sup.down_since or now)) // 60)
            logger.warning(f"[Stream {sup.stream_id}] Viewers switched to the YouTube embed")
            self._emit(sup.stream_id, "warning", "Viewers switched to YouTube", f"Restream down for {minutes} min")
        else:
            logger.info(f"[Stream {sup.stream_id}] Viewers back on the restream")
            self._emit(sup.stream_id, "success", "Viewers back on the restream", "")

    def _fetch_path_info(self, stream_id: str) -> Optional[PathInfo]:
        """MediaMTX API path state; None if the path does not exist or the API is unreachable."""
        path_url = f"{self.mediamtx_api_url}/v3/paths/get/live/stream{stream_id}"
        try:
            req = urllib.request.Request(path_url, headers={"User-Agent": "IndiaFOSS-Watchdog"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                self.origin_reachable = True
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    readers = data.get("readers", [])
                    bytes_received = data.get("bytesReceived")
                    return PathInfo(
                        ready=bool(data.get("ready", False)),
                        readers=len(readers) if isinstance(readers, list) else 0,
                        bytes_received=bytes_received if isinstance(bytes_received, int) else None,
                    )
        except urllib.error.HTTPError as he:
            # MediaMTX answered: the path just doesn't exist (no publisher yet)
            self.origin_reachable = True
            if he.code != 404:
                logger.debug(f"[Stream {stream_id}] MediaMTX path check HTTP error: {he.code}")
        except Exception as e:
            self.origin_reachable = False
            logger.debug(f"[Stream {stream_id}] MediaMTX path check error: {e}")
        return None

    def _check_origin(self) -> None:
        try:
            req = urllib.request.Request(f"{self.mediamtx_api_url}/v3/paths/list", headers={"User-Agent": "IndiaFOSS-Watchdog"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                self.origin_reachable = resp.status == 200
        except Exception:
            self.origin_reachable = False

    def _check_rtsp_publisher(self, stream_id: str) -> tuple[bool, int]:
        """Check MediaMTX API for path presence and reader count."""
        info = self._fetch_path_info(stream_id)
        if info is None:
            return False, 0
        return info.ready, info.readers

    def _check_hls_playlist(self, stream_id: str) -> tuple[bool, Optional[int], Optional[str]]:
        """Fetch index.m3u8 (following a multivariant playlist): (accessible, media sequence, last segment)."""
        hls_url = f"{self.mediamtx_hls_url}/live/stream{stream_id}/index.m3u8"
        try:
            content = self._fetch_text(hls_url)
            if content is None:
                return False, None, None
            if "#EXT-X-STREAM-INF" in content:
                resolution = RESOLUTION_REGEX.search(content)
                frame_rate = FRAME_RATE_REGEX.search(content)
                self._variant_info[stream_id] = (
                    resolution.group(1).replace("x", "×") if resolution else None,
                    f"{float(frame_rate.group(1)):g}" if frame_rate else None,
                )
                variant = next(
                    (line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")),
                    None,
                )
                if variant:
                    child = self._fetch_text(f"{self.mediamtx_hls_url}/live/stream{stream_id}/{variant}")
                    if child is not None:
                        content = child
            match = MEDIA_SEQUENCE_REGEX.search(content)
            segments = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
            return True, int(match.group(1)) if match else None, segments[-1] if segments else None
        except urllib.error.HTTPError as he:
            if he.code != 404:
                logger.debug(f"[Stream {stream_id}] HLS check HTTP error: {he.code}")
        except Exception as e:
            logger.debug(f"[Stream {stream_id}] HLS check error: {e}")
        return False, None, None

    @staticmethod
    def _fetch_text(url: str) -> Optional[str]:
        req = urllib.request.Request(url, headers={"User-Agent": "IndiaFOSS-Watchdog"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="replace")
