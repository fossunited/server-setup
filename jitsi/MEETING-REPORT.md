# Meeting report: deploy runbook

How to install the custom per-meeting report on the server. What it does and the
output layout are in `README.md`. Design notes:
`docs/superpowers/specs/2026-07-22-jitsi-meeting-report-design.md`.

Server layout: checkout in `/root/docker-jitsi-meet/`, config in
`~/.jitsi-meet-cfg` (`CONFIG` in `.env`). The module and finalize script live
under CONFIG, not the checkout.

| Repo file | Server path |
|---|---|
| `prosody-plugins-custom/mod_meeting_report.lua` | `~/.jitsi-meet-cfg/prosody/prosody-plugins-custom/` |
| `jibri/finalize.sh` | `~/.jitsi-meet-cfg/jibri/finalize.sh` |
| `.env`, `docker-compose.yml` edits | `/root/docker-jitsi-meet/` |

## Deploy

1. Copy the files up:

```bash
scp jitsi/prosody-plugins-custom/mod_meeting_report.lua root@meet.fossunited.org:/root/.jitsi-meet-cfg/prosody/prosody-plugins-custom/
scp jitsi/jibri/finalize.sh root@meet.fossunited.org:/root/.jitsi-meet-cfg/jibri/finalize.sh
```

2. On the server, set perms and make the shared meetings dir (prosody and jibri
   run as different UIDs, so 0777):

```bash
chmod +x /root/.jitsi-meet-cfg/jibri/finalize.sh
mkdir -p /root/.jitsi-meet-cfg/meetings && chmod 0777 /root/.jitsi-meet-cfg/meetings
```

3. In `/root/docker-jitsi-meet/.env`:

```
XMPP_MUC_MODULES=token_affiliation,meeting_report
XMPP_BREAKOUT_MUC_MODULES=meeting_report
MEETINGS_DIR=~/.jitsi-meet-cfg/meetings
JIBRI_FINALIZE_RECORDING_SCRIPT_PATH=/config/finalize.sh
ENABLE_REQUIRE_DISPLAY_NAME=1
PROSODY_ENABLE_RECORDING_METADATA=1
PROSODY_ENABLE_METRICS=1
PROSODY_METRICS_ALLOWED_CIDR=127.0.0.1/32
JIBRI_ENABLE_PROMETHEUS=1
```

4. In `docker-compose.yml`, add this volume to BOTH `prosody` and `jibri`:

```yaml
- ${MEETINGS_DIR}:/meetings:Z
```

5. Recreate (env/volume changed, so not `restart`):

```bash
cd /root/docker-jitsi-meet && docker compose up -d
```

6. Confirm it loaded (expect two lines, main + breakout):

```bash
docker compose logs prosody | grep "mod_meeting_report loaded"
```

Test: open a room in two tabs, set names, send chat, close both. The room is
destroyed ~60s later and files appear under `/root/.jitsi-meet-cfg/meetings/`.
For recording, record a meeting then check
`docker compose logs jibri | grep finalize`.

## Rollback

Set `.env` back to `XMPP_MUC_MODULES=token_affiliation`, remove the `/meetings`
volume lines, then `docker compose up -d`. Existing output is left untouched.

## Troubleshooting

| Symptom | Check |
|---|---|
| no `mod_meeting_report loaded` | module path wrong or lua syntax error; read full prosody log |
| no files after meeting | room not destroyed yet (wait 60s); `/meetings` perms (0777); prosody log for "cannot write" |
| `talk_min` all 0 | normal if nobody held the floor; else `speakerStats` shape differs this version |
| chat.txt missing, attendance present | no messages sent, or `muc-occupant-groupchat` not firing |
| recording not in folder | `docker compose logs jibri \| grep finalize`; check `metadata.json` has `meeting_url` |

## Notes and limits

- Breakout rooms write their own `<date>/breakout-<name>/` folder, not merged
  into the main room's chat. The breakout module only loads if prosody is
  recreated, not restarted.
- Guest names are self-typed (require-display-name helps).
- Same room name twice a day shares a folder; the second run's files get a
  `-HHMM` suffix so nothing is overwritten.
- A meeting crossing UTC midnight may split date folders between
  chat/attendance and recording.
- Metrics (prosody `:5280/metrics`, jibri prometheus) are bound to localhost.
