"""FastAPI router for IndiaFOSS 2026 operator control plane.

IMPORTANT REQUIREMENTS:
1. Explicit GET and HEAD support on all read routes to satisfy health/smoke-check contracts.
2. Structured JSON error formatting: {"error": {"code": "...", "message": "..."}}.
3. Bounded in-memory event log; every SSE subscriber gets its own bounded queue.
4. Idempotent lifecycle calls and per-stream serialization.
5. Report only measured telemetry; unknown values are "—" / null, never invented.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import itertools
import logging
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
import urllib.error
import urllib.request

from .shim import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    StreamingResponse,
    status,
)

from ..models.stream import (
    LogEventItem,
    PlaybackModeEnum,
    ServiceHealthEnum,
    ServiceStatusItem,
    SessionStatus,
    SessionSwitchRequest,
    StreamConfig,
    StreamStatusEnum,
    StreamStatusResponse,
    StreamUpdateRequest,
    SystemHealthResponse,
)
from .. import auth, telemetry
from ..streaming.schedule import ScheduledSession, parse_start
from ..streaming.supervisor import DesiredState, StreamStatus, StreamSupervisor
from ..streaming.url_sanitizer import (
    InvalidYouTubeURLError,
    canonicalize_youtube_url,
    extract_youtube_video_id,
)

logger = logging.getLogger("api_router")

router = APIRouter()

# Global state references (injected by server.py during lifespan)
supervisors: Dict[str, StreamSupervisor] = {}
configs: Dict[str, StreamConfig] = {}
watchdog: Optional[Any] = None
config_manager: Optional[Any] = None
event_ring_buffer: List[LogEventItem] = []
MAX_EVENT_BUFFER = 200
# Event log times are shown in the venue's timezone (the server clock is UTC); IST by default
DISPLAY_TZ = timezone(timedelta(minutes=int(os.getenv("DISPLAY_UTC_OFFSET_MINUTES", "330"))))
SSE_BACKLOG = 50

# One bounded queue per connected SSE client
_event_subscribers: Set[asyncio.Queue] = set()
_event_ids = itertools.count(1)


def get_operator_token() -> Optional[str]:
    """Retrieve the optional configured operator secret token."""
    return os.getenv("OPERATOR_TOKEN", "").strip() or None


def verify_operator_auth(authorization: Optional[str] = Header(None)) -> None:
    """Validate Bearer token if an OPERATOR_TOKEN environment variable is set."""
    expected_token = get_operator_token()
    if not expected_token:
        return  # No auth required in development/open mode

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Missing Authorization header."},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Invalid operator token."},
        )


async def broadcast_event(
    stream_id: Optional[str],
    level: str,
    message: str,
    detail: Optional[str] = None,
    event_type: str = "info",
) -> None:
    """Record an operational event into the bounded ring buffer and push it to every SSE client."""
    now = datetime.now(DISPLAY_TZ)
    sup = supervisors.get(stream_id) if stream_id else None
    if sup is not None and sup.title:
        stream_label = sup.title
    else:
        stream_label = f"Stream {int(stream_id):02d}" if stream_id and stream_id.isdigit() else (stream_id or "System")

    item = LogEventItem(
        id=f"{int(time.time() * 1000)}-{next(_event_ids)}",
        timestamp=now.strftime("%H:%M:%S"),
        time=now.strftime("%H:%M"),
        streamId=stream_id,
        streamLabel=stream_label,
        level=level,
        message=message,
        text=f"{stream_label}: {message}",
        detail=detail,
        type=event_type,
    )

    telemetry.writer.record("events", {
        "room": stream_id, "level": level, "type": event_type, "message": message, "detail": detail,
    })

    event_ring_buffer.append(item)
    if len(event_ring_buffer) > MAX_EVENT_BUFFER:
        event_ring_buffer.pop(0)

    for queue in list(_event_subscribers):
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass  # a stalled client misses events rather than growing memory


def _not_found(stream_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "STREAM_NOT_FOUND", "message": f"Stream '{stream_id}' not found."},
    )


def _get_supervisor(stream_id: str) -> StreamSupervisor:
    sup = supervisors.get(stream_id)
    if not sup:
        raise _not_found(stream_id)
    return sup


def _stream_number(stream_id: str) -> str:
    return f"STREAM {int(stream_id):02d}" if stream_id.isdigit() else f"STREAM {stream_id}"


def persist_configs() -> None:
    """Atomically save every stream's config, including the operator's desired state."""
    if not config_manager:
        return
    updated: List[StreamConfig] = []
    for sup in sorted(supervisors.values(), key=lambda s: s.order):
        base = configs.get(sup.stream_id)
        data = base.model_dump() if base else {
            "index": int(sup.stream_id) - 1 if sup.stream_id.isdigit() else 0,
            "streamNumber": _stream_number(sup.stream_id),
            "route": f"/stream/{sup.stream_id}",
        }
        data.update(
            id=sup.stream_id,
            title=sup.title,
            thumbnail=sup.thumbnail,
            sourceUrl=sup.source_url,
            quality=sup.quality,
            order=sup.order,
            desiredState=sup.desired_state.value,
            playbackMode=sup.playback_mode,
        )
        cfg = StreamConfig(**data)
        configs[sup.stream_id] = cfg
        updated.append(cfg)
    try:
        config_manager.save_streams(updated)
    except Exception as e:
        logger.error(f"Failed to persist stream configuration: {e}")
        # Surface it: an unsaved "running" state means streams won't resume after a restart
        try:
            asyncio.get_running_loop().create_task(
                broadcast_event(None, "error", "Could not save stream settings", str(e), "error")
            )
        except RuntimeError:
            pass


def _format_uptime(since: Optional[float]) -> str:
    if not since:
        return "0m"
    elapsed_sec = int(time.time() - since)
    hours, mins = elapsed_sec // 3600, (elapsed_sec % 3600) // 60
    return f"{hours}h {mins}m" if hours > 0 else f"{mins}m"


def _youtube_video_id(source_url: str) -> Optional[str]:
    if not source_url:
        return None
    try:
        return extract_youtube_video_id(source_url)
    except InvalidYouTubeURLError:
        return None  # channel /live URLs have no fixed video ID


def _alive_health(running: bool, alive: bool) -> ServiceHealthEnum:
    if not running:
        return ServiceHealthEnum.UNKNOWN
    return ServiceHealthEnum.HEALTHY if alive else ServiceHealthEnum.DOWN


def _session_statuses(cfg: Optional[StreamConfig]) -> List[SessionStatus]:
    sessions = list(cfg.sessions or []) if cfg else []
    slugs = [s.slug for s in sessions]
    current_index = slugs.index(cfg.currentSession) if cfg and cfg.currentSession in slugs else -1
    statuses = []
    for i, session in enumerate(sessions):
        state = "current" if i == current_index else ("ended" if i < current_index else "upcoming")
        statuses.append(SessionStatus(**session.model_dump(), state=state))
    return statuses


def _current_session(cfg: Optional[StreamConfig]):
    if not cfg or not cfg.currentSession:
        return None
    return next((s for s in cfg.sessions or [] if s.slug == cfg.currentSession), None)


def room_schedule(stream_id: str):
    """Watchdog lookup: (sessions, current slug, auto-advance) for a room, or None without a schedule."""
    cfg = configs.get(stream_id)
    if not cfg or not cfg.sessions:
        return None
    sessions = [ScheduledSession(s.slug, parse_start(s.startsAt)) for s in cfg.sessions]
    return sessions, cfg.currentSession, cfg.autoAdvance


async def advance_room(stream_id: str, slug: str, reason: str) -> StreamStatusResponse:
    """Switch a room to one of its scheduled sessions (automatic advance or operator override)."""
    sup = _get_supervisor(stream_id)
    cfg = configs.get(stream_id)
    session = next((s for s in (cfg.sessions or [] if cfg else []) if s.slug == slug), None)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": f"Room '{stream_id}' has no session '{slug}'."},
        )
    if cfg.currentSession != slug:
        cfg.currentSession = slug
        sup.fallback_active = False  # a new broadcast: the fallback decision starts over
        # Restarts the pipeline if the room is running; Streamlink then waits for the broadcast
        await sup.update_source(f"https://www.youtube.com/watch?v={session.youtubeVideoId}")
        persist_configs()
        await broadcast_event(stream_id, "action", f"Now showing: {session.title}", reason, "action")
    return format_supervisor_to_response(sup)


async def auto_advance_room(stream_id: str, slug: str, reason: str) -> None:
    """Watchdog callback: never raises."""
    try:
        await advance_room(stream_id, slug, reason)
    except Exception as e:
        logger.error(f"[Stream {stream_id}] Automatic session switch to {slug} failed: {e}")


def format_supervisor_to_response(sup: StreamSupervisor) -> StreamStatusResponse:
    """Format supervisor state into frontend-compatible StreamStatusResponse."""
    cfg = configs.get(sup.stream_id)
    current_session = _current_session(cfg)
    is_live = sup.status == StreamStatus.LIVE
    running = sup.desired_state == DesiredState.RUNNING
    snap = watchdog.get_snapshot(sup.stream_id) if watchdog else None

    if not running:
        source_health = hls_health = origin_health = ServiceHealthEnum.UNKNOWN
    elif snap is None:
        source_health = hls_health = origin_health = ServiceHealthEnum.UNKNOWN
    else:
        if snap.hls_advancing:
            source_health = ServiceHealthEnum.HEALTHY
        elif sup.source_blocked:
            source_health = ServiceHealthEnum.DOWN
        else:
            source_health = ServiceHealthEnum.DEGRADED
        if not snap.origin_reachable:
            origin_health = ServiceHealthEnum.DOWN
        else:
            origin_health = ServiceHealthEnum.HEALTHY if snap.rtsp_publisher_ready else ServiceHealthEnum.DEGRADED
        if snap.hls_accessible and snap.hls_advancing:
            hls_health = ServiceHealthEnum.HEALTHY
        elif snap.hls_accessible:
            hls_health = ServiceHealthEnum.DEGRADED
        else:
            hls_health = ServiceHealthEnum.DOWN

    sl_alive, ff_alive = sup.process_liveness()
    bitrate_bps = snap.bitrate_bps if snap and is_live else None
    playback_mode = sup.playback_mode
    playback = "youtube" if playback_mode == "youtube" or sup.fallback_active else "restream"

    return StreamStatusResponse(
        id=str(sup.stream_id),
        slug=cfg.slug if cfg else None,
        index=cfg.index if cfg else (int(sup.stream_id) - 1 if str(sup.stream_id).isdigit() else 0),
        streamNumber=cfg.streamNumber if cfg else _stream_number(str(sup.stream_id)),
        title=sup.title,
        devroom=cfg.devroom if cfg else "",
        partner=cfg.partner if cfg else None,
        time=cfg.time if cfg else "",
        thumbnail=(current_session.thumbnail if current_session and current_session.thumbnail else sup.thumbnail),
        accentColor=cfg.accentColor if cfg else "#3b82f6",
        route=f"/{cfg.slug}" if cfg and cfg.slug else f"/stream/{sup.stream_id}",
        status=StreamStatusEnum(sup.status.value),
        desiredState=sup.desired_state.value,
        waitingForSource=bool(running and sup.waiting_for_source and not sup.source_blocked),
        startsAt=current_session.startsAt if current_session else (cfg.startsAt if cfg else None),
        quality=sup.quality,
        viewers=telemetry.viewers.counts().get(str(sup.stream_id), 0),
        sourceUrl=sup.source_url,
        youtubeVideoId=_youtube_video_id(sup.source_url),
        playbackMode=PlaybackModeEnum(playback_mode),
        playback=playback,
        currentSession=cfg.currentSession if cfg else None,
        autoAdvance=cfg.autoAdvance if cfg else True,
        sessions=_session_statuses(cfg),
        order=sup.order,
        uptime=_format_uptime(sup.live_since) if is_live else "0m",
        restartCount=sup.metrics.restarts,
        lastError=sup.metrics.last_error,
        lastRecovery=sup.metrics.last_recovery,
        bitrate=f"{bitrate_bps / 1_000_000:.1f} Mbps" if bitrate_bps else "—",
        resolution=(snap.resolution if snap and is_live and snap.resolution else "—"),
        fps=(snap.fps if snap and is_live and snap.fps else "—"),
        sourceHealth=source_health,
        streamlinkHealth=_alive_health(running, sl_alive),
        ffmpegHealth=_alive_health(running, ff_alive),
        mediaOriginHealth=origin_health,
        hlsHealth=hls_health,
    )


# ============================================================
# STREAM API ENDPOINTS
# ============================================================

@router.api_route("/api/streams", methods=["GET", "HEAD"], response_model=List[StreamStatusResponse])
async def list_streams(request: Request = None, response: Response = Response()):
    """List operational status of all livestreams.

    Viewer pages add ?watch=<room id|home|session>&vid=<random tab id> to their regular
    poll; that check-in is what the viewer counts are built from (no extra requests).
    """
    params = request.query_params if request is not None else {}
    vid, watching = params.get("vid"), params.get("watch")
    if vid and watching:
        telemetry.viewers.touch(vid, watching)
    response.headers["Cache-Control"] = "private, no-store"
    return [format_supervisor_to_response(sup) for sup in sorted(supervisors.values(), key=lambda s: s.order)]


@router.api_route("/api/streams/{stream_id}", methods=["GET", "HEAD"], response_model=StreamStatusResponse)
async def get_stream(stream_id: str):
    """Get operational status of a single livestream."""
    return format_supervisor_to_response(_get_supervisor(stream_id))


@router.post("/api/streams/{stream_id}/start", dependencies=[Depends(verify_operator_auth)])
async def start_stream(stream_id: str):
    """Start an individual stream pipeline."""
    sup = _get_supervisor(stream_id)

    if not sup.source_url or not sup.source_url.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SOURCE_URL_EMPTY", "message": f"Stream '{stream_id}' does not have a source URL configured. Please set a YouTube link first."},
        )

    await sup.start()
    persist_configs()
    await broadcast_event(stream_id, "action", "Start initiated", "Operator started stream pipeline", "action")
    return {"status": "ok", "streamId": stream_id, "desiredState": sup.desired_state.value}


@router.post("/api/streams/{stream_id}/stop", dependencies=[Depends(verify_operator_auth)])
async def stop_stream(stream_id: str):
    """Intentionally stop an individual stream pipeline."""
    sup = _get_supervisor(stream_id)
    await sup.stop()
    persist_configs()
    await broadcast_event(stream_id, "warning", "Stream stopped", "Operator stopped stream pipeline", "action")
    return {"status": "ok", "streamId": stream_id, "desiredState": sup.desired_state.value}


@router.post("/api/streams/{stream_id}/restart", dependencies=[Depends(verify_operator_auth)])
async def restart_stream(stream_id: str):
    """Restart an individual stream pipeline."""
    sup = _get_supervisor(stream_id)
    await sup.restart()
    persist_configs()
    await broadcast_event(stream_id, "action", "Restart initiated", "Operator triggered manual restart", "action")
    return {"status": "ok", "streamId": stream_id, "desiredState": sup.desired_state.value}


@router.post("/api/streams/{stream_id}/session", dependencies=[Depends(verify_operator_auth)])
async def switch_session(stream_id: str, req: SessionSwitchRequest):
    """Operator override: make a room play one of its scheduled sessions now."""
    return await advance_room(stream_id, req.slug, "Operator switched session")


@router.put("/api/streams/{stream_id}", dependencies=[Depends(verify_operator_auth)])
async def update_stream(stream_id: str, req: StreamUpdateRequest):
    """Update stream configuration (source URL, quality, title, order, thumbnail, playback mode)."""
    sup = _get_supervisor(stream_id)

    if req.sourceUrl or req.quality:
        try:
            new_url = canonicalize_youtube_url(req.sourceUrl) if req.sourceUrl else sup.source_url
        except InvalidYouTubeURLError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_SOURCE_URL", "message": str(exc)},
            ) from exc
        new_quality = (req.quality or sup.quality).strip()
        if new_url != sup.source_url or new_quality != sup.quality:
            if new_url:
                # Restarts the pipeline if it is running
                await sup.update_source(new_url, new_quality)
                current = _current_session(configs.get(stream_id))
                new_id = _youtube_video_id(new_url)
                if current and new_id:
                    current.youtubeVideoId = new_id
            else:
                sup.quality = new_quality
            await broadcast_event(
                stream_id, "action", "Source updated", f"Source: {new_url or '(none)'} · quality {new_quality}", "action"
            )

    if req.playbackMode is not None:
        try:
            mode = PlaybackModeEnum(req.playbackMode).value
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_PLAYBACK_MODE", "message": "playbackMode must be auto, restream or youtube."},
            ) from exc
        if mode != sup.playback_mode:
            sup.playback_mode = mode
            if mode != "auto":
                sup.fallback_active = False
            labels = {"auto": "Auto (YouTube only after a long outage)", "restream": "Restream only", "youtube": "YouTube embed"}
            await broadcast_event(stream_id, "action", "Viewer playback changed", labels[mode], "action")

    cfg = configs.get(stream_id)
    if req.autoAdvance is not None and cfg and req.autoAdvance != cfg.autoAdvance:
        cfg.autoAdvance = req.autoAdvance
        await broadcast_event(
            stream_id, "action", "Automatic session switching " + ("on" if req.autoAdvance else "off"), None, "action"
        )

    if req.title:
        sup.title = req.title.strip()
    if req.order is not None:
        sup.order = req.order
    if req.thumbnail:
        sup.thumbnail = req.thumbnail.strip()

    persist_configs()
    return format_supervisor_to_response(sup)


# ============================================================
# SYSTEM HEALTH & MONITORING
# ============================================================

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect (HTTP -> HTTPS once DOMAIN is set) already proves the edge answers


def _probe(url: str, timeout: float = 1.0) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "IndiaFOSS-Controller"})
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


@router.api_route("/api/system/health", methods=["GET", "HEAD"], response_model=SystemHealthResponse)
async def system_health():
    """Get aggregate system and service health status (host CPU/memory as seen from the controller)."""
    cpu_percent = 0.0
    mem_used = 0.0
    mem_total = 0.0
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        mem_used = round(mem.used / (1024**3), 1)
        mem_total = round(mem.total / (1024**3), 1)
    except Exception:
        pass

    running = [s for s in supervisors.values() if s.desired_state == DesiredState.RUNNING]
    live = [s for s in running if s.status == StreamStatus.LIVE]
    # Waiting for a scheduled broadcast to start is expected, not a failure
    waiting = [s for s in running if s.status != StreamStatus.LIVE and s.waiting_for_source and not s.source_blocked]
    fine = len(live) + len(waiting)

    origin_ok = bool(watchdog and watchdog.origin_reachable)
    edge_url = os.getenv("EDGE_HEALTH_URL", "http://edge:80/healthz")
    edge_ok = await asyncio.to_thread(_probe, edge_url)

    if not running:
        hls = ServiceHealthEnum.UNKNOWN
    elif fine == len(running):
        hls = ServiceHealthEnum.HEALTHY
    elif fine:
        hls = ServiceHealthEnum.DEGRADED
    else:
        hls = ServiceHealthEnum.DOWN

    services = [
        ServiceStatusItem(name="Stream Controller", key="controller", health=ServiceHealthEnum.HEALTHY),
        ServiceStatusItem(
            name="Media Origin",
            key="mediamtx",
            health=ServiceHealthEnum.HEALTHY if origin_ok else ServiceHealthEnum.DOWN,
        ),
        ServiceStatusItem(
            name="HTTP Edge",
            key="edge",
            health=ServiceHealthEnum.HEALTHY if edge_ok else ServiceHealthEnum.UNKNOWN,
            detail=None if edge_ok else f"Not reachable at {edge_url}",
        ),
        ServiceStatusItem(
            name="Running Streams",
            key="hls",
            health=hls,
            detail=f"{len(live)} live, {len(waiting)} waiting for their broadcast, of {len(running)} running",
        ),
    ]

    proxy_ok = getattr(watchdog, "proxy_ok", None) if watchdog else None
    if getattr(watchdog, "youtube_proxy", None):
        if proxy_ok is None:
            proxy_health, proxy_detail = ServiceHealthEnum.UNKNOWN, "Not checked yet"
        elif proxy_ok:
            proxy_health, proxy_detail = ServiceHealthEnum.HEALTHY, None
        else:
            proxy_health, proxy_detail = ServiceHealthEnum.DOWN, watchdog.proxy_error
        services.append(
            ServiceStatusItem(name="YouTube Proxy (WARP)", key="youtube-proxy", health=proxy_health, detail=proxy_detail)
        )

    if not origin_ok and running:
        overall = ServiceHealthEnum.DOWN
    elif proxy_ok is False and running:
        overall = ServiceHealthEnum.DOWN
    elif hls in (ServiceHealthEnum.DEGRADED, ServiceHealthEnum.DOWN):
        overall = ServiceHealthEnum.DEGRADED
    else:
        overall = ServiceHealthEnum.HEALTHY

    return SystemHealthResponse(
        overall=overall,
        cpu=cpu_percent,
        memoryUsed=mem_used,
        memoryTotal=mem_total,
        networkBandwidth=(
            f"{telemetry.network.tx_mbps:.0f} Mbps out · {telemetry.format_bytes(telemetry.network.tx_total)} sent"
            if telemetry.network.tx_mbps is not None else "—"
        ),
        services=services,
        streamsHealthy=fine,
        streamsTotal=len(supervisors),
        totalViewers=sum(n for page, n in telemetry.viewers.counts().items() if page in supervisors),
    )


# ============================================================
# OPERATOR LOGIN (called by the edge via forward_auth for every admin request)
# ============================================================

@router.get("/api/auth/check")
async def auth_check(request: Request):
    """200 lets the admin request through; 401 asks the browser for the login; 429 = locked out."""
    # The edge sets X-Forwarded-For to the real client address (incoming values are not trusted)
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    decision = await asyncio.to_thread(auth.gate.check, address, request.headers.get("authorization"))

    if decision.newly_locked:
        await broadcast_event(
            None, "warning", "Admin login locked for one address",
            f"{auth.MAX_FAILURES} wrong passwords in a row; locked for {auth.LOCKOUT_SECONDS // 60} min", "warning",
        )
    if decision.status == 200:
        return Response(status_code=200)
    headers = {"Cache-Control": "no-store"}
    if decision.status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="IndiaFOSS admin", charset="UTF-8"'
    if decision.retry_after:
        headers["Retry-After"] = str(decision.retry_after)
    return Response(content=decision.message, status_code=decision.status, headers=headers, media_type="text/plain")


# ============================================================
# VIEWER TELEMETRY
# ============================================================

MAX_TELEMETRY_BODY = 4096


@router.post("/api/telemetry", status_code=204)
async def player_telemetry(request: Request):
    """Anonymous player quality report (public; no IP or cookies stored). Always 204."""
    body = await request.body()
    if len(body) > MAX_TELEMETRY_BODY:
        return Response(status_code=204)
    try:
        import json
        report = telemetry.sanitize_player_report(json.loads(body or b"null"))
    except ValueError:
        report = None
    if report and telemetry.limiter.allow(report["vid"]):
        telemetry.writer.record("player", report)
    return Response(status_code=204)


def telemetry_snapshot() -> Dict[str, Any]:
    """One row per minute: viewers, per-room state and host load, for the post-event report."""
    counts = telemetry.viewers.counts()
    rooms = []
    for sup in sorted(supervisors.values(), key=lambda s: s.order):
        cfg = configs.get(sup.stream_id)
        snap = watchdog.get_snapshot(sup.stream_id) if watchdog else None
        rooms.append({
            "room": sup.stream_id,
            "session": cfg.currentSession if cfg else None,
            "status": sup.status.value,
            "desired": sup.desired_state.value,
            "waiting": bool(sup.waiting_for_source),
            "playback": "youtube" if sup.playback_mode == "youtube" or sup.fallback_active else "restream",
            "viewers": counts.get(str(sup.stream_id), 0),
            "bitrateKbps": round(snap.bitrate_bps / 1000) if snap and snap.bitrate_bps else None,
            "resolution": snap.resolution if snap else None,
            "restarts": sup.metrics.restarts,
        })
    cpu = mem = None
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        mem = round(psutil.virtual_memory().used / (1024**3), 2)
    except Exception:
        pass
    net = telemetry.network
    return {
        "viewersTotal": sum(counts.values()),
        "viewersByPage": counts,
        "rooms": rooms,
        "cpuPercent": cpu,
        "memUsedGB": mem,
        "txMbps": net.tx_mbps,
        "rxMbps": net.rx_mbps,
        "txBytesTotal": net.tx_total,
        "rxBytesTotal": net.rx_total,
        "telemetryDropped": telemetry.writer.dropped,
    }


async def telemetry_loop(flush_every: float = 10.0, snapshot_every: float = 60.0) -> None:
    """Sample bandwidth and flush buffered telemetry every 10s; write a snapshot every minute."""
    last_snapshot = 0.0
    while True:
        try:
            telemetry.network.sample()
            now = time.monotonic()
            if now - last_snapshot >= snapshot_every:
                last_snapshot = now
                telemetry.writer.record("snapshots", telemetry_snapshot())
            await asyncio.to_thread(telemetry.writer.flush)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # telemetry must never take anything else down
            logger.warning("Telemetry loop error: %s", e)
        await asyncio.sleep(flush_every)


# ============================================================
# SERVER-SENT EVENTS (SSE) EVENT STREAM
# ============================================================

@router.get("/api/events")
async def events_sse_stream(request: Request):
    """Server-Sent Events stream delivering real-time status and log events to admin console."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _event_subscribers.add(queue)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Recent history first, so a freshly opened console isn't empty
            for item in event_ring_buffer[-SSE_BACKLOG:]:
                yield f"data: {item.model_dump_json()}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {item.model_dump_json()}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat comment to keep HTTP connection alive
                    yield ": ping\n\n"
        finally:
            _event_subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# CONTAINER HEALTH & READINESS PROBES (GET + HEAD)
# ============================================================

@router.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    return {"status": "ok", "time": time.time()}


@router.api_route("/readyz", methods=["GET", "HEAD"])
async def readyz():
    return {"status": "ready", "supervisors": len(supervisors)}
