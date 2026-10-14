#!/usr/bin/env bash
# ==============================================================================
# IndiaFOSS 2026 — VPS One-Command Bootstrap Script
#
# Idempotently provisions and starts the complete livestreaming stack:
# - Verifies Docker and Docker Compose
# - Prepares configuration files
# - Validates frontend distribution build
# - Launches production container stack with health checking
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=========================================================="
echo " IndiaFOSS 2026 Livestream Platform — Bootstrap"
echo "=========================================================="

cd "${ROOT_DIR}"

# 1. Check prerequisites
echo "[1/6] Checking system prerequisites..."
command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker is not installed. Please install Docker first." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: Docker Compose is not installed." >&2; exit 1; }

# 2. Ensure environment configuration exists
echo "[2/6] Verifying environment configuration..."
if [ ! -f "backend/.env" ]; then
    echo "Creating backend/.env from template..."
    cp backend/.env.example backend/.env
fi
set -a
# shellcheck disable=SC1091
source backend/.env
set +a

# Compose reads deploy/.env; the edge refuses to start without an operator login
if [ -z "${ADMIN_PASSWORD_HASH:-}" ] && ! grep -qE "^ADMIN_PASSWORD_HASH=.+" deploy/.env 2>/dev/null; then
    echo "ERROR: Operator console login is not configured." >&2
    echo "       cp deploy/.env.example deploy/.env and set ADMIN_PASSWORD_HASH as described there." >&2
    exit 1
fi

# 3. Ensure frontend build exists
echo "[3/6] Verifying frontend distribution..."
if [ ! -f "dist/index.html" ]; then
    echo "Frontend build 'dist/index.html' not found."
    if command -v npm >/dev/null 2>&1; then
        echo "Building frontend with npm run build..."
        npm ci || npm install
        npm run build
    else
        echo "WARNING: npm not found on host. Frontend must be built before starting Caddy."
    fi
else
    echo "Frontend distribution assets found in dist/"
fi

# 4. Ensure config directory and streams.yaml exist
echo "[4/6] Verifying streams configuration..."
mkdir -p backend/config
# The controller container runs as uid 1000 and saves settings here (atomic writes need the directory)
chown -R 1000:1000 backend/config
# ...and appends anonymous viewer telemetry here
mkdir -p telemetry
chown -R 1000:1000 telemetry
if [ ! -f "backend/config/streams.yaml" ]; then
    echo "Creating default streams.yaml..."
    cat <<'EOF' > backend/config/streams.yaml
streams:
  - id: "1"
    index: 0
    streamNumber: "STREAM 01"
    title: "IndiaFOSS 2026 — Track 1 (Main Hall)"
    devroom: "MAIN HALL"
    thumbnail: "/assets/streams/stream1.jpg"
    accentColor: "#3b82f6"
    route: "/stream/1"
    sourceUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    quality: "1080p60"
    order: 1
    enabled: true

  - id: "2"
    index: 1
    streamNumber: "STREAM 02"
    title: "IndiaFOSS 2026 — Track 2 (DevRoom A)"
    devroom: "DEVROOM A"
    thumbnail: "/assets/streams/stream2.jpg"
    accentColor: "#10b981"
    route: "/stream/2"
    sourceUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    quality: "1080p60"
    order: 2
    enabled: true

  - id: "3"
    index: 2
    streamNumber: "STREAM 03"
    title: "IndiaFOSS 2026 — Track 3 (DevRoom B)"
    devroom: "DEVROOM B"
    thumbnail: "/assets/streams/stream3.jpg"
    accentColor: "#f59e0b"
    route: "/stream/3"
    sourceUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    quality: "1080p60"
    order: 3
    enabled: true

  - id: "4"
    index: 3
    streamNumber: "STREAM 04"
    title: "IndiaFOSS 2026 — Track 4 (Workshop Room)"
    devroom: "WORKSHOP"
    thumbnail: "/assets/streams/stream4.jpg"
    accentColor: "#8b5cf6"
    route: "/stream/4"
    sourceUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    quality: "1080p60"
    order: 4
    enabled: true
EOF
fi

# 5. Build and launch the production Docker Compose stack
echo "[5/6] Launching Docker Compose stack (production)..."
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build

# 6. Wait for controller readiness
echo "[6/6] Waiting for controller readiness..."
EDGE_PORT_VAL="${EDGE_PORT:-80}"
DOMAIN_VAL="$(sed -n 's/^DOMAIN=//p' deploy/.env 2>/dev/null | tail -1)"
if [ -n "${DOMAIN_VAL}" ]; then
    PUBLIC_URL="https://${DOMAIN_VAL}"
else
    PUBLIC_URL="http://localhost:${EDGE_PORT_VAL}"
fi
RETRIES=30
READY=0
while [ $RETRIES -gt 0 ]; do
    if curl -s -f -I http://localhost:8000/healthz >/dev/null 2>&1 || curl -s -f -I "http://localhost:${EDGE_PORT_VAL}/healthz" >/dev/null 2>&1; then
        READY=1
        break
    fi
    echo -n "."
    sleep 1
    RETRIES=$((RETRIES - 1))
done
echo ""

if [ $READY -eq 1 ]; then
    echo "=========================================================="
    echo " SUCCESS: IndiaFOSS 2026 Livestream Stack is online!"
    echo " Viewer App:        ${PUBLIC_URL}/"
    echo " Admin Console:     ${PUBLIC_URL}$(sed -n "s/^ADMIN_PATH=//p" deploy/.env | tail -1 | grep . || echo /admin) (login from deploy/.env)"
    echo " Operator API:      http://localhost:8000/api/streams (host only)"
    echo " MediaMTX API:      docker exec indiafoss-controller curl -s http://mediamtx:9997/v3/paths/list"
    echo " Run smoke tests:   SMOKE_AUTH='operator:PASSWORD' ./scripts/smoke-test.sh ${PUBLIC_URL}"
    echo "=========================================================="
else
    echo "WARNING: Health check timed out. Check container logs with:"
    echo "  docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml logs"
    exit 1
fi
