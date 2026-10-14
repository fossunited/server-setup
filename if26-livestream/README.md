# IndiaFOSS 2026 livestream

Multi-track livestreaming platform used for **IndiaFOSS 2026** (26-27 September 2026). It restreamed 4 tracks per day (Audi 1, Audi 2, Devroom 1, Devroom 2) to a single viewer site with a per-track player, schedule, and operator console.

This directory is a snapshot cloned off the server over SSH after the event. The full, runnable project is under `indiafoss-livestream/`.

- **Recordings** :: all talks, per track and session, are at <https://fossunited.org/indiafoss/2026/live>.

- **Analytics, stats, and performance numbers** :: see the post-event writeup at <https://forum.fossunited.org/t/if26-livestreaming-recording/8494/10>.

## Credits

Built and operated by [Jasil Faras](https://fossunited.org/u/artemis) and [Muneer S](https://fossunited.org/u/prpetrator), Yasir and Venkatesh, who handled the entire setup, the stack, and the two days of live operations. Muneer and Jeswin did the design overall.

## Hosting

Ran on a **temporary Linode VPS spun up only for the event**, at **stream.fossunited.org**, live for the **2 days** of the conference and then deleted.

## How it worked

Each of the 4 talks per day was already being streamed to **YouTube Live**. This platform re-ingested those YouTube streams and re-served them to viewers, so the public site was fast, branded, and ad-free, with all 4 tracks in one place.

The per-track pipeline, with **zero transcoding** (stream copy only, so the VPS stays light):

```
YouTube Live  ->  Streamlink  ->  FFmpeg (-c copy)  ->  MediaMTX  ->  Caddy edge  ->  viewer
                  (pull src)      (remux to RTSP)      (RTSP->HLS)    (HLS + SPA)
```

1. **Streamlink**
   ([streamlink/streamlink](https://github.com/streamlink/streamlink)) pulls the YouTube live source for a track at a quality fallback chain (1080p60 down to 720p/best).

2. **FFmpeg**
   (<https://ffmpeg.org>) remuxes that feed with `-c copy` (no re-encode) and pushes it over RTSP into MediaMTX (`rtsp://mediamtx:8554`).

3. **MediaMTX** v1.11.3
   ([bluenviron/mediamtx](https://github.com/bluenviron/mediamtx)) is the media
   origin. It remuxes RTSP to **HLS** (MPEG-TS segments kept in RAM, no disk
   recording) on `:8888`.

4. **Caddy** v2.9.1
   (<https://caddyserver.com>) is the public edge. It terminates TLS, serves the prebuilt frontend, proxies `/live/*` HLS from MediaMTX, and proxies `/api/*` to the controller.

## Components

- **Controller / operator API** :: Python **FastAPI** (<https://fastapi.tiangolo.com>) app run under **uvicorn** (<https://www.uvicorn.org>), in `indiafoss-livestream/backend/`.
  It is the brain of the system: a `StreamSupervisor` that runs all 4 Streamlink + FFmpeg pipelines, a health watchdog that checks MediaMTX HLS output, a config manager that persists `streams.yaml`, an operator admin console, and anonymous viewer telemetry. Runs on `:8000` (bound to localhost; reached only through the Caddy edge or over SSH).

- **MediaMTX** ([bluenviron/mediamtx](https://github.com/bluenviron/mediamtx)) :: the actual streaming server. RTSP ingest + HLS delivery. RTMP/WebRTC/SRT disabled to cut attack surface and memory. Only the controller may publish or call its API.

- **Caddy edge** :: reverse proxy and static host. Serves the prebuilt **Preact SPA** from `dist/`, caches hashed bundles, guards the admin path and state-changing API calls with HTTP basic auth (operator login).

- **Frontend** :: a Preact (<https://preactjs.com>) single-page app, prebuilt into `dist/` and served as static files by Caddy. The `streams.yaml` config drives the track list, session schedule, thumbnails, and accent colors shown in the UI.

### Cloudflare WARP

YouTube rate-limits and bot-checks datacenter IPs like a VPS. All Streamlink traffic was routed through a **Cloudflare WARP** (<https://developers.cloudflare.com/warp-client/>) SOCKS5 proxy on the host (`STREAMLINK_PROXY=socks5h://172.30.40.1:40000`), with a YouTube cookies file as an extra fallback. See `indiafoss-livestream/scripts/setup-warp.sh`.

## Layout

- `indiafoss-livestream/backend/` :: FastAPI controller, supervisor, pipeline, health, config, auth, telemetry.

- `indiafoss-livestream/deploy/` :: `docker-compose.yml` + `docker-compose.prod.yml`, `mediamtx.yml`, `Caddyfile`, `.env`.

- `indiafoss-livestream/dist/` :: prebuilt Preact frontend and session artwork.

- `indiafoss-livestream/scripts/` :: `bootstrap.sh` (one-command provision + launch), `setup-warp.sh`, `smoke-test.sh`.
- `backend/config/streams.yaml` :: the 4 tracks and their per-session YouTube
  video IDs and start times.

## Running it

Three containers: `controller` (FastAPI/uvicorn), `mediamtx`, `edge` (Caddy).

```bash
cd indiafoss-livestream
# one-command bootstrap (checks Docker, builds frontend if needed, launches prod stack)
./scripts/bootstrap.sh

# or directly:
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

Set `DOMAIN`, `ADMIN_USER`, and `ADMIN_PASSWORD_HASH` in `deploy/.env` first (see `deploy/.env.example`). Operators then start/stop each track and advance sessions from the admin console behind `ADMIN_PATH`.
