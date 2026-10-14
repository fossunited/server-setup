# ==============================================================================
# IndiaFOSS 2026 Livestream Controller Dockerfile
# ==============================================================================
# Base image pinned to stable Debian bookworm Python 3.12 slim
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/home/appuser/.local/bin:$PATH"

# Install runtime dependencies:
# - ffmpeg: media demuxing and copy-only remuxing
# - tini: zombie process reaping and proper PID 1 signal forwarding
# - curl: healthcheck probes
# - ca-certificates: TLS verification for YouTube and Streamlink
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tini \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root operational user
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -m -s /bin/bash appuser

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application backend code
COPY . /app/backend/

# Create config and log directories with appropriate ownership
RUN mkdir -p /app/config /app/dist && \
    chown -R appuser:appgroup /app

USER appuser

# Healthcheck probe using HEAD method on /healthz
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f -I http://localhost:8000/healthz || exit 1

EXPOSE 8000

# Tini guarantees sub-process reaping and forwards SIGTERM directly to Uvicorn
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
