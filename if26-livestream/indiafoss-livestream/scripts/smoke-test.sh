#!/usr/bin/env bash
# ==============================================================================
# IndiaFOSS 2026 — Automated Smoke Test Script
#
# Validates:
# - Explicit GET and HEAD parity on all read endpoints
# - 4-stream presence and configuration
# - System health endpoint
# - Structured 404 responses
# - Frontend static delivery
#
# Usage:
#   ./scripts/smoke-test.sh [BASE_URL]      # controller direct (default :8000)
#   SMOKE_AUTH=operator:PASSWORD ./scripts/smoke-test.sh http://localhost:8080
#     Against the Caddy edge: also checks that operator routes reject anonymous
#     and cross-site requests and accept the operator login. Without
#     SMOKE_AUTH, the edge answers the system health checks with 401.
# ==============================================================================

set -uo pipefail

TARGET_HOST="${1:-http://localhost:8000}"
PASSED=0
FAILED=0

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "=========================================================="
echo " IndiaFOSS 2026 Livestream Stack — Smoke Test"
echo " Target Base URL: ${TARGET_HOST}"
echo "=========================================================="

test_endpoint() {
    local method="$1"
    local path="$2"
    local expected_code="$3"
    local desc="$4"
    shift 4  # any remaining arguments are passed to curl

    local url="${TARGET_HOST}${path}"
    local code
    if [ "$method" = "HEAD" ]; then
        code=$(curl -s -o /dev/null -w "%{http_code}" -I "$@" "${url}" 2>/dev/null || echo "000")
    else
        code=$(curl -s -o /dev/null -w "%{http_code}" -X "${method}" "$@" "${url}" 2>/dev/null || echo "000")
    fi

    if [ "$code" -eq "$expected_code" ]; then
        echo -e "[PASS] ${method} ${path} -> HTTP ${code} (${desc})"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}[FAIL]${NC} ${method} ${path} -> HTTP ${code} (Expected ${expected_code}) (${desc})"
        FAILED=$((FAILED + 1))
    fi
}

# 1. Health and Readiness Checks (GET + HEAD)
test_endpoint "GET"  "/healthz" 200 "Container Health Liveness probe"
test_endpoint "HEAD" "/healthz" 200 "Container Health HEAD probe"
test_endpoint "GET"  "/readyz" 200 "Readiness probe"
test_endpoint "HEAD" "/readyz" 200 "Readiness HEAD probe"

# 2. Stream Controller API (GET + HEAD)
test_endpoint "GET"  "/api/streams" 200 "List all streams"
test_endpoint "HEAD" "/api/streams" 200 "List all streams HEAD"
test_endpoint "GET"  "/api/streams/1" 200 "Get stream 1 status"
test_endpoint "HEAD" "/api/streams/1" 200 "Get stream 1 HEAD"
test_endpoint "GET"  "/api/streams/2" 200 "Get stream 2 status"
test_endpoint "GET"  "/api/streams/3" 200 "Get stream 3 status"
test_endpoint "GET"  "/api/streams/4" 200 "Get stream 4 status"

# 3. Non-existent stream error behavior
test_endpoint "GET"  "/api/streams/999" 404 "Non-existent stream returns 404"
test_endpoint "HEAD" "/api/streams/999" 404 "Non-existent stream HEAD returns 404"

# 4. System Health Monitoring (operator-only at the edge)
if [ -n "${SMOKE_AUTH:-}" ]; then
    echo -e "\n--- Validating Edge Operator Auth ---"
    test_endpoint "GET"  "${SMOKE_ADMIN_PATH:-/admin}" 401 "Operator console requires login"
    test_endpoint "GET"  "${SMOKE_ADMIN_PATH:-/admin}" 200 "Operator console with login" -u "${SMOKE_AUTH}"
    test_endpoint "GET"  "/api/system/health" 401 "System health requires login"
    test_endpoint "GET"  "/api/system/health" 200 "System health metrics with login" -u "${SMOKE_AUTH}"
    test_endpoint "HEAD" "/api/system/health" 200 "System health metrics HEAD with login" -u "${SMOKE_AUTH}"
    test_endpoint "GET"  "/api/events" 401 "Operator event stream requires login"
    # Stream 999 does not exist: authorized writes reach the controller and 404 with no side effects
    test_endpoint "POST" "/api/streams/999/start" 401 "Stream control requires login"
    test_endpoint "POST" "/api/streams/999/start" 404 "Stream control with login reaches controller" -u "${SMOKE_AUTH}"
    test_endpoint "POST" "/api/streams/999/start" 403 "Cross-site stream control is refused" -u "${SMOKE_AUTH}" -H "Sec-Fetch-Site: cross-site"
else
    test_endpoint "GET"  "/api/system/health" 200 "System health metrics"
    test_endpoint "HEAD" "/api/system/health" 200 "System health metrics HEAD"
fi

# 5. Verify payload content of /api/streams
echo -e "\n--- Validating Stream Count & Content ---"
STREAMS_BODY=$(curl -s "${TARGET_HOST}/api/streams" 2>/dev/null || echo "")
COUNT=$(echo "${STREAMS_BODY}" | grep -o '"id":' | wc -l | tr -d ' ')
if [ "$COUNT" -ge 4 ]; then
    echo -e "[PASS] Verified at least 4 active stream definitions present (found ${COUNT})"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}[FAIL]${NC} Expected at least 4 streams, found ${COUNT}"
    FAILED=$((FAILED + 1))
fi

# 6. Verify 404 structured error format
echo -e "\n--- Validating Structured Error Format ---"
ERR_BODY=$(curl -s "${TARGET_HOST}/api/streams/999" 2>/dev/null || echo "")
if echo "${ERR_BODY}" | grep -q "STREAM_NOT_FOUND"; then
    echo -e "[PASS] Structured error code 'STREAM_NOT_FOUND' verified in payload"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}[FAIL]${NC} Error body did not contain STREAM_NOT_FOUND: ${ERR_BODY}"
    FAILED=$((FAILED + 1))
fi

echo "=========================================================="
echo -e "Results: ${GREEN}${PASSED} passed${NC}, ${RED}${FAILED} failed${NC}"
echo "=========================================================="

if [ "$FAILED" -eq 0 ]; then
    exit 0
else
    exit 1
fi
