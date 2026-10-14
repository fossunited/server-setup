"""Process supervisor for individual livestream pipelines in IndiaFOSS 2026.

CRITICAL INVARIANTS:
1. Zero shell pipelines. Uses subprocess.Popen with explicit argument arrays.
2. POSIX Process Groups: Each process uses start_new_session=True.
3. Clean Termination: SIGTERM -> grace period -> SIGKILL -> .wait() reap -> clear references.
4. Sibling Cleanup: If Streamlink or FFmpeg dies unexpectedly, the other is terminated and reaped.
5. Intentional Stop vs Auto-Restart: STOPPED state prevents automatic restart loops.
6. Capped Exponential Backoff, reset after 60s of continuous LIVE.
7. Concurrency serialization: Per-stream asyncio.Lock prevents duplicate processes.
8. Child stderr is drained continuously so a full pipe buffer can never block a child.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import logging
import os
import signal
import subprocess
import threading
import time
from typing import IO, Callable

from .pipeline import build_ffmpeg_command, build_streamlink_command
from .url_sanitizer import canonicalize_youtube_url

logger = logging.getLogger("stream_supervisor")

STDERR_TAIL_LINES = 50
BACKOFF_RESET_AFTER_LIVE = 60.0

# Streamlink log fragments that drive supervisor state
_SL_WAITING = "Waiting for streams"
_SL_OPENING = "Opening stream"
_SL_VIDEO_INFO_ERROR = "Could not get video info"
_SL_BLOCKED_MARKERS = ("LOGIN_REQUIRED", "Sign in to confirm", "not a bot")


class DesiredState(str, Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


class StreamStatus(str, Enum):
    LIVE = "live"
    STARTING = "starting"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"
    ERROR = "error"
    STOPPING = "stopping"


@dataclass
class SupervisorMetrics:
    restarts: int = 0
    last_error: str | None = None
    last_recovery: str | None = None
    started_at: float | None = None
    last_hls_progress: float | None = None


class StreamSupervisor:
    """Supervises a single Streamlink -> FFmpeg -> MediaMTX RTSP pipeline."""

    BACKOFF_STEPS = [2, 4, 8, 16, 30, 60]

    def __init__(
        self,
        stream_id: str,
        title: str,
        source_url: str,
        mediamtx_rtsp_base: str = "rtsp://mediamtx:8554",
        quality: str = "1080p60,1080p,720p60,720p,best",
        order: int = 1,
        thumbnail: str = "",
        shutdown_timeout: float = 5.0,
        dummy_mode: bool = False,
        on_status_change: Callable[[str, StreamStatus, str], None] | None = None,
        cookies_file: str | None = None,
        http_proxy: str | None = None,
    ):
        self.stream_id = stream_id
        self.title = title
        self.source_url = source_url
        self.mediamtx_rtsp_base = mediamtx_rtsp_base.rstrip("/")
        self.quality = quality
        self.order = order
        self.thumbnail = thumbnail
        self.shutdown_timeout = shutdown_timeout
        self.dummy_mode = dummy_mode
        self.on_status_change = on_status_change
        self.cookies_file = cookies_file
        self.http_proxy = http_proxy

        self.desired_state = DesiredState.STOPPED
        self.status = StreamStatus.OFFLINE
        self.metrics = SupervisorMetrics()

        # Viewer playback: "auto" | "restream" | "youtube" (fallback decided by the watchdog)
        self.playback_mode = "auto"
        self.fallback_active = False

        # When the current LIVE period began / when the stream stopped being LIVE while RUNNING
        self.live_since: float | None = None
        self.down_since: float | None = None

        # Source state parsed from Streamlink's output (written by the stderr reader threads)
        self.waiting_for_source = False
        self.source_blocked = False
        self.source_error: str | None = None
        self.stream_opened_at: float | None = None

        self._lock = asyncio.Lock()
        self._monitor_task: asyncio.Task | None = None
        self._backoff_index = 0
        self._recovery_reason: str | None = None

        self._pipeline_gen = 0
        self._stderr_tail: deque[tuple[int, str, str]] = deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_threads: list[threading.Thread] = []

        self.streamlink_proc: subprocess.Popen | None = None
        self.ffmpeg_proc: subprocess.Popen | None = None

    @property
    def rtsp_url(self) -> str:
        return f"{self.mediamtx_rtsp_base}/live/stream{self.stream_id}"

    def _set_status(self, new_status: StreamStatus, reason: str = ""):
        if self.status == new_status:
            return
        old_status = self.status
        self.status = new_status

        now = time.time()
        if new_status == StreamStatus.LIVE:
            self.live_since = now
            self.down_since = None
        else:
            self.live_since = None
            if self.desired_state == DesiredState.RUNNING and self.down_since is None:
                self.down_since = now

        logger.info(
            f"[Stream {self.stream_id}] Status changed: {old_status.value} -> {new_status.value} ({reason})"
        )
        if self.on_status_change:
            try:
                self.on_status_change(self.stream_id, new_status, reason)
            except Exception as e:
                logger.error(f"[Stream {self.stream_id}] Status change callback failed: {e}")

    async def start(self) -> None:
        """Idempotently start the stream pipeline."""
        async with self._lock:
            self.desired_state = DesiredState.RUNNING
            if self._is_running():
                logger.info(f"[Stream {self.stream_id}] Already running, ignoring start request.")
                return

            self.metrics.last_error = None
            self._backoff_index = 0
            self._set_status(StreamStatus.STARTING, "Operator started stream")
            await self._launch_pipeline()

            if not self._monitor_task or self._monitor_task.done():
                self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self, reason: str = "Operator stopped stream") -> None:
        """Intentionally stop the stream pipeline and reap all processes."""
        async with self._lock:
            self.desired_state = DesiredState.STOPPED
            self._set_status(StreamStatus.STOPPING, reason)

            await self._cancel_monitor()
            await self._terminate_pipeline()
            self.metrics.last_error = None
            self.metrics.started_at = None
            self._backoff_index = 0
            self._recovery_reason = None
            self.fallback_active = False
            self._reset_source_state()
            self._set_status(StreamStatus.OFFLINE, "Pipeline cleanly terminated")
            self.live_since = None
            self.down_since = None

    async def restart(self) -> None:
        """Cleanly terminate and restart the stream pipeline."""
        async with self._lock:
            self.desired_state = DesiredState.RUNNING
            self._set_status(StreamStatus.RECONNECTING, "Restart requested")
            self.metrics.restarts += 1
            self.metrics.last_error = None
            self._backoff_index = 0  # Manual restart resets backoff

            await self._cancel_monitor()
            await self._terminate_pipeline()
            await self._launch_pipeline()

            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def recover(self, reason: str) -> None:
        """Kill a running but unhealthy pipeline; the monitor loop relaunches it with backoff."""
        async with self._lock:
            if self.desired_state != DesiredState.RUNNING or not self._is_running():
                return
            logger.warning(f"[Stream {self.stream_id}] Recovering pipeline: {reason}")
            self._recovery_reason = reason
            self.metrics.last_error = reason
            self._set_status(StreamStatus.RECONNECTING, reason)
            await self._terminate_pipeline()

    async def update_source(self, new_source_url: str, new_quality: str | None = None) -> None:
        """Update source URL and reload pipeline if running."""
        canonical_url = canonicalize_youtube_url(new_source_url)
        self.source_url = canonical_url
        if new_quality:
            self.quality = new_quality

        if self.desired_state == DesiredState.RUNNING:
            await self.restart()

    def _is_running(self) -> bool:
        """Check if both Streamlink and FFmpeg are alive."""
        sl_alive, ff_alive = self.process_liveness()
        return sl_alive and ff_alive

    def process_liveness(self) -> tuple[bool, bool]:
        sl_alive = self.streamlink_proc is not None and self.streamlink_proc.poll() is None
        ff_alive = self.ffmpeg_proc is not None and self.ffmpeg_proc.poll() is None
        return sl_alive, ff_alive

    async def _cancel_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._monitor_task = None

    def _reset_source_state(self) -> None:
        self.waiting_for_source = False
        self.source_blocked = False
        self.source_error = None
        self.stream_opened_at = None

    async def _launch_pipeline(self) -> None:
        """Launch the pipeline with OS pipes and POSIX process groups."""
        # Ensure any leftover processes are cleared
        await self._terminate_pipeline()

        self._pipeline_gen += 1
        self._reset_source_state()

        if self.dummy_mode:
            await self._launch_dummy_pipeline()
            return

        if not self.source_url or not self.source_url.strip():
            logger.warning(f"[Stream {self.stream_id}] Cannot launch pipeline: source URL is not set.")
            self._set_status(StreamStatus.OFFLINE, "Source URL not configured")
            return

        cookies = self.cookies_file if self.cookies_file and os.path.isfile(self.cookies_file) else None
        canonical_url = canonicalize_youtube_url(self.source_url)
        streamlink_cmd = build_streamlink_command(
            canonical_url, self.quality, cookies_file=cookies, proxy=self.http_proxy
        )
        ffmpeg_cmd = build_ffmpeg_command(self.rtsp_url)

        logger.info(f"[Stream {self.stream_id}] Launching Streamlink: {' '.join(streamlink_cmd)}")
        logger.info(f"[Stream {self.stream_id}] Launching FFmpeg: {' '.join(ffmpeg_cmd)}")

        # Launch Streamlink in a new process group with stdout piped
        self.streamlink_proc = subprocess.Popen(
            streamlink_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            shell=False,
        )

        try:
            # Launch FFmpeg taking stdin from Streamlink's stdout
            self.ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=self.streamlink_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                shell=False,
            )
        except Exception as e:
            logger.error(f"[Stream {self.stream_id}] Failed to launch FFmpeg: {e}")
            self._kill_proc_group(self.streamlink_proc)
            self._reap(self.streamlink_proc)
            self.streamlink_proc = None
            self._set_status(StreamStatus.ERROR, f"FFmpeg launch failure: {e}")
            raise

        # Parent closes its reference to Streamlink stdout pipe so EOF flows naturally
        if self.streamlink_proc.stdout:
            self.streamlink_proc.stdout.close()

        self._start_stderr_reader("streamlink", self.streamlink_proc)
        self._start_stderr_reader("ffmpeg", self.ffmpeg_proc)

        self.metrics.started_at = time.time()
        self._set_status(StreamStatus.STARTING, "Processes launched")

    async def _launch_dummy_pipeline(self) -> None:
        """Launch dummy dummy-processes for testing without real YouTube/FFmpeg."""
        import sys

        cmd1 = [sys.executable, "-c", "import time; time.sleep(300)"]
        cmd2 = [sys.executable, "-c", "import time; time.sleep(300)"]

        self.streamlink_proc = subprocess.Popen(
            cmd1, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, shell=False
        )
        self.ffmpeg_proc = subprocess.Popen(
            cmd2, stdin=self.streamlink_proc.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, shell=False
        )
        if self.streamlink_proc.stdout:
            self.streamlink_proc.stdout.close()

        self.metrics.started_at = time.time()
        self._set_status(StreamStatus.STARTING, "Dummy processes launched")

    # ------------------------------------------------------------------
    # Child stderr handling
    # ------------------------------------------------------------------

    def _start_stderr_reader(self, name: str, proc: subprocess.Popen) -> None:
        if proc.stderr is None:
            return
        thread = threading.Thread(
            target=self._read_stderr,
            args=(name, proc.stderr, self._pipeline_gen),
            name=f"stderr-{self.stream_id}-{name}",
            daemon=True,
        )
        self._stderr_threads.append(thread)
        thread.start()

    def _read_stderr(self, name: str, pipe: IO[bytes], gen: int) -> None:
        """Drain a child's stderr until EOF, logging it and keeping a short tail."""
        last_line: str | None = None
        repeats = 0
        try:
            for raw in iter(pipe.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                self._note_stderr_line(name, line, gen)
                if line == last_line:
                    repeats += 1
                    continue
                if repeats:
                    logger.info(f"[Stream {self.stream_id}] {name}: (previous line repeated {repeats}x)")
                last_line, repeats = line, 0
                logger.info(f"[Stream {self.stream_id}] {name}: {line}")
        except (OSError, ValueError):
            pass  # pipe closed during teardown
        finally:
            if repeats:
                logger.info(f"[Stream {self.stream_id}] {name}: (previous line repeated {repeats}x)")
            try:
                pipe.close()
            except OSError:
                pass

    def _note_stderr_line(self, name: str, line: str, gen: int) -> None:
        self._stderr_tail.append((gen, name, line))
        if name != "streamlink" or gen != self._pipeline_gen:
            return
        if _SL_VIDEO_INFO_ERROR in line:
            self.source_error = line.split(_SL_VIDEO_INFO_ERROR, 1)[1].lstrip(" -")
            self.source_blocked = any(marker in line for marker in _SL_BLOCKED_MARKERS)
        elif _SL_WAITING in line:
            self.waiting_for_source = True
        elif _SL_OPENING in line:
            self.waiting_for_source = False
            self.source_blocked = False
            self.source_error = None
            self.stream_opened_at = time.time()

    async def _join_stderr_readers(self, timeout: float = 1.0) -> None:
        threads, self._stderr_threads = self._stderr_threads, []
        deadline = time.time() + timeout
        for thread in threads:
            remaining = max(0.0, deadline - time.time())
            await asyncio.to_thread(thread.join, remaining)

    def recent_output(self, limit: int = 10) -> list[str]:
        """Last stderr lines from the current pipeline, oldest first."""
        lines = [f"{name}: {line}" for gen, name, line in self._stderr_tail if gen == self._pipeline_gen]
        return lines[-limit:]

    def _failure_summary(self) -> str | None:
        """Latest error line from the current pipeline's output, if any."""
        lines = [(name, line) for gen, name, line in self._stderr_tail if gen == self._pipeline_gen]
        for name, line in reversed(lines):
            if "error" in line.lower():
                return f"{name}: {line}"
        return None

    # ------------------------------------------------------------------
    # Termination
    # ------------------------------------------------------------------

    async def _terminate_pipeline(self) -> None:
        """Gracefully terminate Streamlink & FFmpeg process groups with SIGKILL fallback and reaping."""
        sl_proc, ff_proc = self.streamlink_proc, self.ffmpeg_proc
        procs = [p for p in (sl_proc, ff_proc) if p is not None]
        if not procs:
            return

        # 1. Send SIGTERM to process groups
        for proc in procs:
            self._send_signal_to_group(proc, signal.SIGTERM)

        # 2. Wait up to shutdown_timeout for graceful exit
        deadline = time.time() + self.shutdown_timeout
        for proc in procs:
            remaining = max(0.1, deadline - time.time())
            try:
                await asyncio.to_thread(proc.wait, timeout=remaining)
            except (subprocess.TimeoutExpired, Exception):
                pass

        # 3. If any process remains alive, send SIGKILL
        for proc in procs:
            if proc.poll() is None:
                logger.warning(
                    f"[Stream {self.stream_id}] Process {proc.pid} still alive after SIGTERM, sending SIGKILL."
                )
                self._send_signal_to_group(proc, signal.SIGKILL)
                try:
                    await asyncio.to_thread(proc.wait, timeout=1.0)
                except Exception as e:
                    logger.error(f"[Stream {self.stream_id}] Error reaping {proc.pid}: {e}")

        # Clear references, unless a concurrent launch already replaced them
        if self.streamlink_proc is sl_proc:
            self.streamlink_proc = None
        if self.ffmpeg_proc is ff_proc:
            self.ffmpeg_proc = None

    def _send_signal_to_group(self, proc: subprocess.Popen, sig: signal.Signals) -> None:
        """Send signal to the process group."""
        if proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.debug(f"[Stream {self.stream_id}] Signal {sig.name} to pid {proc.pid} failed: {e}")

    def _kill_proc_group(self, proc: subprocess.Popen) -> None:
        self._send_signal_to_group(proc, signal.SIGKILL)

    def _reap(self, proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=0.5)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Monitor loop
    # ------------------------------------------------------------------

    def _next_backoff_delay(self) -> int:
        delay = self.BACKOFF_STEPS[min(self._backoff_index, len(self.BACKOFF_STEPS) - 1)]
        self._backoff_index = min(self._backoff_index + 1, len(self.BACKOFF_STEPS) - 1)
        return delay

    def _maybe_reset_backoff(self) -> None:
        """Reset backoff once the stream has been LIVE continuously for a while."""
        if (
            self._backoff_index > 0
            and self.status == StreamStatus.LIVE
            and self.live_since is not None
            and time.time() - self.live_since > BACKOFF_RESET_AFTER_LIVE
        ):
            logger.info(f"[Stream {self.stream_id}] Live for {int(BACKOFF_RESET_AFTER_LIVE)}s, resetting backoff.")
            self._backoff_index = 0

    async def _monitor_loop(self) -> None:
        """Background loop monitoring process health and unexpected child exits."""
        try:
            while self.desired_state == DesiredState.RUNNING:
                await asyncio.sleep(1.0)
                try:
                    if self._is_running():
                        self._maybe_reset_backoff()
                        continue
                    await self._handle_pipeline_exit()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # Keep supervising: a crashed monitor would leave the stream dead for good
                    logger.error(f"[Stream {self.stream_id}] Error in monitor loop: {e}", exc_info=True)
                    self.metrics.last_error = f"Supervisor error: {e}"
                    self._set_status(StreamStatus.ERROR, str(e))
                    await asyncio.sleep(self._next_backoff_delay())
        except asyncio.CancelledError:
            pass

    async def _handle_pipeline_exit(self) -> None:
        """One or both children died (or recover() killed them): clean up, back off, relaunch."""
        sl_code = self.streamlink_proc.poll() if self.streamlink_proc else None
        ff_code = self.ffmpeg_proc.poll() if self.ffmpeg_proc else None
        recovery_reason, self._recovery_reason = self._recovery_reason, None

        # Let the readers drain the dead processes' final lines before summarising
        await self._join_stderr_readers(timeout=1.0)

        if recovery_reason:
            detail = recovery_reason
        else:
            detail = self._failure_summary() or f"Streamlink exited ({sl_code}), FFmpeg exited ({ff_code})"
            if sl_code == 0 and ff_code in (0, None) and not self._failure_summary():
                detail = "YouTube stream ended (Streamlink exited normally)"
            logger.warning(
                f"[Stream {self.stream_id}] Unexpected process exit! Streamlink: {sl_code}, FFmpeg: {ff_code}"
            )

        self.metrics.last_error = detail
        self._set_status(StreamStatus.RECONNECTING, detail)

        # Clean up any surviving sibling
        await self._terminate_pipeline()

        if self.desired_state != DesiredState.RUNNING:
            return

        delay = self._next_backoff_delay()
        logger.info(f"[Stream {self.stream_id}] Backoff waiting {delay}s before restart...")
        await asyncio.sleep(delay)

        async with self._lock:
            if self.desired_state != DesiredState.RUNNING or self._is_running():
                return
            self.metrics.restarts += 1
            self.metrics.last_recovery = f"{datetime.now().strftime('%H:%M:%S')} ({detail})"
            await self._launch_pipeline()
