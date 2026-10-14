#!/usr/bin/env bash
#
# Jibri finalize script. Jibri runs this after a recording completes, passing
# the recording directory as $1. That directory holds the .mp4 plus Jibri's
# metadata.json. This moves them into the shared meeting folder so they sit
# next to chat.txt / attendance.csv written by mod_meeting_report:
#
#     /meetings/<YYYY-MM-DD>/<room>/recording.mp4
#     /meetings/<YYYY-MM-DD>/<room>/metadata.json
#
# The folder is keyed by room name + date, computed the same way as the prosody
# module, so the two jobs converge without a shared id. If a file already
# exists (same room name reused the same day) a -HHMM suffix avoids overwrite.
#
# Enable with JIBRI_FINALIZE_RECORDING_SCRIPT_PATH=/config/finalize.sh
set -euo pipefail

REC_DIR="${1:?usage: finalize.sh <recording-dir>}"
MEETINGS_ROOT="${MEETINGS_ROOT:-/meetings}"
META="$REC_DIR/metadata.json"

log() { echo "[finalize] $*"; }

if [ ! -d "$REC_DIR" ]; then
    log "recording dir not found: $REC_DIR"
    exit 1
fi

# Room name from the meeting_url in metadata.json (jq if present, else grep).
room="unknown"
if [ -f "$META" ]; then
    if command -v jq >/dev/null 2>&1; then
        url="$(jq -r '.meeting_url // empty' "$META" 2>/dev/null || true)"
    else
        url="$(grep -o '"meeting_url"[[:space:]]*:[[:space:]]*"[^"]*"' "$META" \
                | head -1 | sed -E 's/.*"([^"]*)"$/\1/' || true)"
    fi
    if [ -n "${url:-}" ]; then
        room="${url%/}"
        room="${room##*/}"
    fi
fi
# Strip anything not filesystem-safe.
room="$(printf '%s' "$room" | tr -c 'A-Za-z0-9._-' '_')"
[ -z "$room" ] && room="unknown"

# IST, to match the folder date the prosody module computes.
date_dir="$(TZ=Asia/Kolkata date +%F)"
suffix="$(TZ=Asia/Kolkata date +%H%M)"
dest="$MEETINGS_ROOT/$date_dir/$room"
mkdir -p "$dest"

shopt -s nullglob
moved=0
for f in "$REC_DIR"/*.mp4; do
    target="$dest/recording.mp4"
    [ -e "$target" ] && target="$dest/recording-$suffix.mp4"
    mv -f "$f" "$target"
    log "moved $(basename "$f") -> $target"
    moved=1
done

if [ -f "$META" ]; then
    target="$dest/metadata.json"
    [ -e "$target" ] && target="$dest/metadata-$suffix.json"
    cp -f "$META" "$target"
    log "copied metadata.json -> $target"
fi

if [ "$moved" -eq 0 ]; then
    log "no .mp4 found in $REC_DIR"
    exit 1
fi

log "done -> $dest"
