"""IndiaFOSS 2026 Livestream Platform — Production Controller & API Server.

Integrates:
- StreamSupervisor for 4 concurrent stream pipelines
- HealthWatchdog for MediaMTX / HLS media sequence verification
- ConfigManager for atomic YAML persistence
- Explicit GET and HEAD request support across all routes
- Structured JSON error handling
- Zero transcoding enforcement
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

# Ensure project root and backend dir are in sys.path
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.shim import (
    FastAPI,
    FileResponse,
    HTTPException,
    JSONResponse,
    Request,
    Response,
    StaticFiles,
    status,
)
from app.api import routes
from app.config.manager import ConfigManager
from app.health.watchdog import HealthWatchdog
from app.streaming.schedule import ScheduledSession, initial_session, parse_start
from app.streaming.supervisor import StreamStatus, StreamSupervisor

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("controller_server")


class _QuietPollingFilter(logging.Filter):
    """Drop uvicorn access-log lines for routine reads: every viewer polls /api/streams
    every few seconds, which would otherwise rotate real errors out of the logs."""

    QUIET_PREFIXES = ("/api/streams", "/api/system/health", "/healthz", "/readyz")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 5:
            method, path, status_code = args[1], str(args[2]), args[4]
            if (
                method in ("GET", "HEAD")
                and isinstance(status_code, int)
                and status_code < 400
                and path.startswith(self.QUIET_PREFIXES)
            ):
                return False
        return True


logging.getLogger("uvicorn.access").addFilter(_QuietPollingFilter())

# Keeps fire-and-forget event tasks alive until they finish
_background_tasks: set = set()

_STATUS_EVENTS = {
    StreamStatus.LIVE: ("success", "Stream live", "success"),
    StreamStatus.STARTING: ("info", "Stream starting", "info"),
    StreamStatus.RECONNECTING: ("warning", "Stream reconnecting", "warning"),
    StreamStatus.ERROR: ("error", "Stream error", "error"),
    StreamStatus.OFFLINE: ("info", "Stream offline", "info"),
    StreamStatus.STOPPING: ("info", "Stream stopping", "info"),
}


def _schedule_event(stream_id: str, level: str, message: str, detail: str, event_type: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(routes.broadcast_event(stream_id, level, message, detail or None, event_type))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _on_status_change(stream_id: str, new_status: StreamStatus, reason: str) -> None:
    """Mirror automatic supervisor/watchdog status changes into the operator event log."""
    level, message, event_type = _STATUS_EVENTS.get(new_status, ("info", f"Status {new_status.value}", "info"))
    _schedule_event(stream_id, level, message, reason, event_type)


def _on_watchdog_event(stream_id: str, level: str, message: str, detail: str) -> None:
    _schedule_event(stream_id, level, message, detail, level)


def resolve_config_path() -> Path:
    """Resolve location of streams.yaml configuration."""
    env_path = os.getenv("CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    candidates = [
        BASE_DIR / "config" / "streams.yaml",
        PROJECT_ROOT / "config" / "streams.yaml",
        Path("/app/config/streams.yaml"),
    ]
    for c in candidates:
        if c.exists():
            return c

    # Fallback to default in backend/config
    default_p = BASE_DIR / "config" / "streams.yaml"
    default_p.parent.mkdir(parents=True, exist_ok=True)
    return default_p


def resolve_static_dir() -> Path:
    """Resolve path to built Preact frontend (dist directory)."""
    env_dir = os.getenv("STATIC_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.exists():
            return p

    candidates = [
        PROJECT_ROOT / "dist",
        BASE_DIR / "dist",
        Path("/app/dist"),
    ]
    for c in candidates:
        if c.exists() and (c / "index.html").exists():
            return c

    return PROJECT_ROOT / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager: starts supervisors and health watchdog on boot, terminates cleanly on shutdown."""
    logger.info("Initializing IndiaFOSS 2026 Livestream Controller...")

    # 1. Config Manager & Streams Loading
    config_file = resolve_config_path()
    logger.info(f"Using streams configuration at: {config_file}")
    cfg_mgr = ConfigManager(config_file)
    routes.config_manager = cfg_mgr

    stream_configs = []
    try:
        stream_configs = cfg_mgr.load_streams()
        logger.info(f"Loaded {len(stream_configs)} stream configurations.")
    except Exception as e:
        logger.error(f"Failed to load stream configs ({e}), initializing fallback streams 1-4.")
        from app.models.stream import StreamConfig

        stream_configs = [
            StreamConfig(
                id=str(i),
                index=i - 1,
                streamNumber=f"STREAM {i:02d}",
                title=f"IndiaFOSS 2026 — Track {i}",
                route=f"/stream/{i}",
                sourceUrl="",
                quality="1080p60",
                thumbnail=f"/assets/streams/stream{i}.jpg",
                order=i,
                enabled=True,
            )
            for i in range(1, 5)
        ]
        try:
            cfg_mgr.save_streams(stream_configs)
        except Exception as save_err:
            logger.warning(f"Could not persist fallback stream config: {save_err}")

    # 2. Check environment for dummy/mock mode
    dummy_mode = os.getenv("DUMMY_MODE", "0").lower() in ("1", "true", "yes")

    # 3. Instantiate Stream Supervisors
    rtsp_base = os.getenv("MEDIAMTX_RTSP_BASE", "rtsp://mediamtx:8554")
    cookies_file = os.getenv("STREAMLINK_COOKIES_FILE", "").strip() or None
    streamlink_proxy = os.getenv("STREAMLINK_PROXY", "").strip() or None
    if streamlink_proxy:
        logger.info(f"YouTube traffic goes through proxy {streamlink_proxy}")
    routes.supervisors.clear()
    routes.configs.clear()
    schedule_changed = False
    for sc in sorted(stream_configs, key=lambda s: s.order):
        # Scheduled rooms play their current session (kept across restarts, else picked by time)
        if sc.sessions:
            slugs = [s.slug for s in sc.sessions]
            if sc.currentSession not in slugs:
                sc.currentSession = initial_session(
                    [ScheduledSession(s.slug, parse_start(s.startsAt)) for s in sc.sessions], time.time()
                )
                schedule_changed = True
            current = next(s for s in sc.sessions if s.slug == sc.currentSession)
            sc.sourceUrl = f"https://www.youtube.com/watch?v={current.youtubeVideoId}"
            logger.info(f"[Stream {sc.id}] Room {sc.slug or sc.id}: current session {sc.currentSession}")
        sup = StreamSupervisor(
            stream_id=sc.id,
            source_url=sc.sourceUrl,
            quality=sc.quality,
            title=sc.title,
            thumbnail=sc.thumbnail,
            order=sc.order,
            mediamtx_rtsp_base=rtsp_base,
            dummy_mode=dummy_mode,
            on_status_change=_on_status_change,
            cookies_file=cookies_file,
            http_proxy=streamlink_proxy,
        )
        sup.playback_mode = getattr(sc.playbackMode, "value", sc.playbackMode) or "auto"
        routes.supervisors[sc.id] = sup
        routes.configs[sc.id] = sc

    # 4. Start Health Watchdog
    mediamtx_api = os.getenv("MEDIAMTX_API_URL", "http://mediamtx:9997")
    mediamtx_hls = os.getenv("MEDIAMTX_HLS_URL", "http://mediamtx:8888")
    watchdog = HealthWatchdog(
        supervisors=routes.supervisors,
        mediamtx_api_url=mediamtx_api,
        mediamtx_hls_url=mediamtx_hls,
        fallback_after=float(os.getenv("FALLBACK_AFTER_SECONDS", "300")),
        fallback_return_after=float(os.getenv("FALLBACK_RETURN_SECONDS", "60")),
        on_event=_on_watchdog_event,
        youtube_proxy=streamlink_proxy,
        schedule_lookup=routes.room_schedule,
        on_advance=routes.auto_advance_room,
    )
    routes.watchdog = watchdog
    if schedule_changed:
        routes.persist_configs()
    watchdog.start()
    logger.info("Health watchdog background monitor started.")

    # 5. Resume streams the operator left running (or all streams with AUTO_START_STREAMS=1)
    auto_start = os.getenv("AUTO_START_STREAMS", "0").lower() in ("1", "true", "yes")
    for sc in stream_configs:
        sup = routes.supervisors[sc.id]
        if not (sup.source_url and sup.source_url.strip()):
            continue
        if auto_start or sc.desiredState == "RUNNING":
            logger.info(f"[Stream {sc.id}] Resuming (desired state {sc.desiredState}).")
            asyncio.create_task(sup.start())

    telemetry_task = asyncio.create_task(routes.telemetry_loop())
    logger.info("IndiaFOSS Controller startup sequence complete. Accepting requests.")

    yield

    telemetry_task.cancel()
    try:
        await telemetry_task
    except (asyncio.CancelledError, Exception):
        pass
    from app import telemetry
    await asyncio.to_thread(telemetry.writer.flush)

    # Shutdown sequence
    logger.info("Controller shutdown initiated. Cleaning up supervisors and processes...")
    if routes.watchdog:
        await routes.watchdog.stop()

    # Gracefully stop all supervisors concurrently
    stop_tasks = [sup.stop("Controller shutting down") for sup in routes.supervisors.values()]
    if stop_tasks:
        await asyncio.gather(*stop_tasks, return_exceptions=True)

    logger.info("All stream pipelines reaped. Controller shutdown complete.")


# Initialize FastAPI application
app = FastAPI(
    title="IndiaFOSS 2026 Livestream Operator API",
    version="1.0.0",
    lifespan=lifespan,
)

# No CORS middleware: the SPA and API share one origin behind the Caddy edge.


# Exception handler for structured errors
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        error_payload = detail
    else:
        error_payload = {
            "code": f"HTTP_{exc.status_code}",
            "message": str(detail) if detail else "An error occurred.",
        }

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error_payload},
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected internal server error occurred.",
            }
        },
    )


# Mount API Routes
app.include_router(routes.router)

# Mount Static Frontend
static_dir = resolve_static_dir()
if static_dir.exists() and (static_dir / "index.html").exists():
    logger.info(f"Serving static frontend from: {static_dir}")
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    async def serve_index(request: Request):
        return FileResponse(str(static_dir / "index.html"))

    app.api_route("/", methods=["GET", "HEAD"])(serve_index)
    app.api_route("/admin", methods=["GET", "HEAD"])(serve_index)
    app.api_route("/stream/{stream_id}", methods=["GET", "HEAD"])(serve_index)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = os.getenv("RELOAD", "false").lower() in ("1", "true", "yes")

    logger.info(f"Starting server on {host}:{port} (reload={reload_enabled})")
    uvicorn.run("backend.server:app", host=host, port=port, reload=reload_enabled)
