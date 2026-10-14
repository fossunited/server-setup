#!/usr/bin/env bash
# List the Streamlink/FFmpeg processes inside the controller container (the image has no `ps`).
# Expect exactly one Streamlink and one FFmpeg per running track.
set -euo pipefail
docker exec -i indiafoss-controller python3 - <<'PY'
import os

for pid in sorted((p for p in os.listdir("/proc") if p.isdigit()), key=int):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        continue  # process exited while listing
    if "/streamlink " in cmd or cmd.startswith("ffmpeg "):
        print(pid, cmd[:160])
PY
