#!/usr/bin/env bash
# ==============================================================================
# Cloudflare WARP as a proxy for the controller's YouTube traffic (run as root on the server).
#
# Why: YouTube bot-checks datacenter IPs ("Sign in to confirm you're not a bot"). Through WARP,
# YouTube sees a Cloudflare IP instead. Only Streamlink's YouTube traffic uses it; viewers,
# SSH and everything else stay direct.
#
# What it sets up (idempotent, safe to re-run):
#   1. cloudflare-warp in proxy mode: SOCKS5 on 127.0.0.1:40000, starts on boot
#   2. warp-docker-forward.service: socat on 172.30.40.1:40000 (the Docker bridge gateway of
#      deploy/docker-compose.yml, never the public interface) -> 127.0.0.1:40000, so the
#      controller container can use it
#   3. deploy/.env: STREAMLINK_PROXY=socks5h://172.30.40.1:40000
# ==============================================================================
set -euo pipefail

BRIDGE_IP="172.30.40.1"
PORT="40000"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export DEBIAN_FRONTEND=noninteractive

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }

echo "[1/4] Installing Cloudflare WARP client..."
if ! command -v warp-cli >/dev/null 2>&1; then
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
        | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/cloudflare-client.list
    apt-get update -qq
    apt-get install -y -qq cloudflare-warp
fi
apt-get install -y -qq socat >/dev/null
systemctl enable --now warp-svc >/dev/null

echo "[2/4] Configuring WARP in proxy mode on 127.0.0.1:${PORT}..."
for _ in $(seq 1 15); do warp-cli --accept-tos status >/dev/null 2>&1 && break; sleep 1; done
warp-cli --accept-tos registration show >/dev/null 2>&1 || warp-cli --accept-tos registration new
warp-cli --accept-tos mode proxy
warp-cli --accept-tos proxy port "${PORT}"
warp-cli --accept-tos connect
for _ in $(seq 1 20); do warp-cli --accept-tos status | grep -q Connected && break; sleep 1; done
warp-cli --accept-tos status | head -1

echo "[3/4] Installing the Docker-bridge forwarder (${BRIDGE_IP}:${PORT} -> 127.0.0.1:${PORT})..."
cat > /etc/systemd/system/warp-docker-forward.service <<EOF
[Unit]
Description=Expose the WARP proxy to the livestream Docker network only (${BRIDGE_IP}:${PORT})
After=docker.service warp-svc.service
Wants=warp-svc.service
# The bridge IP exists only once the compose network is up: keep retrying, never give up
StartLimitIntervalSec=0

[Service]
ExecStart=/usr/bin/socat TCP-LISTEN:${PORT},bind=${BRIDGE_IP},reuseaddr,fork TCP:127.0.0.1:${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable warp-docker-forward.service >/dev/null
systemctl restart warp-docker-forward.service

if [ -f "${ROOT_DIR}/deploy/.env" ] && ! grep -q "^STREAMLINK_PROXY=" "${ROOT_DIR}/deploy/.env"; then
    echo "STREAMLINK_PROXY=socks5h://${BRIDGE_IP}:${PORT}" >> "${ROOT_DIR}/deploy/.env"
    echo "      Added STREAMLINK_PROXY to deploy/.env"
fi

echo "[4/4] Verifying YouTube through WARP..."
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 -x "socks5h://127.0.0.1:${PORT}" https://www.youtube.com/generate_204 || true)
if [ "${code}" = "204" ]; then
    echo "OK: YouTube reachable through WARP."
else
    echo "WARNING: YouTube check through WARP returned '${code}'." >&2
fi
if ip -4 addr show | grep -q "inet ${BRIDGE_IP}/"; then
    systemctl is-active --quiet warp-docker-forward.service && echo "OK: forwarder listening on ${BRIDGE_IP}:${PORT}."
else
    echo "Note: ${BRIDGE_IP} appears once the compose stack is up; the forwarder starts by itself then."
fi
