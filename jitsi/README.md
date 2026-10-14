# Jitsi Meet (https://meet.fossunited.org)

Docker repo: https://github.com/jitsi/docker-jitsi-meet
Guide: https://github.com/jitsi/handbook/blob/master/docs/devops-guide/docker.md
Images: https://ghcr.io/jitsi/

Stock [docker-jitsi-meet](https://github.com/jitsi/docker-jitsi-meet). We only change `.env` and `config.js`, everything else is upstream default. Etherpad is bundled in for meeting notes. Runs on the meet host (specs at the bottom).

On the server the dir is in `/root/docker-jitsi-meet` and config lives in `~/.jitsi-meet-cfg`.

## Files here

- `.env` :: config.
- `config.js` :: web frontend tweaks, goes to `~/.jitsi-meet-cfg/web/config.js`.
- `docker-compose.yml` :: stock, plus etherpad and a `/meetings` volume.
- `prosody-plugins-custom/mod_meeting_report.lua` :: custom plugin, see below.
- `jibri/finalize.sh` :: moves a recording into its meeting folder.
- `gen-mod-token.py` :: moderator JWT; `<name> [room]` for one person, `--universal` for a shared all-rooms token (each user types their own name).

## Custom: per-meeting report (Lua)

`mod_meeting_report.lua` saves one folder per meeting to `~/.jitsi-meet-cfg/meetings/<date>/<room>/` (IST) when the room closes:

- `chat.txt` :: group chat transcript.
- `attendance.csv` :: name, join, leave, duration_min, talk_min.
- `recording.mp4` + `metadata.json` :: only if the meeting was recorded.

Turned on in `.env` via `XMPP_MUC_MODULES=token_affiliation,meeting_report` and `XMPP_BREAKOUT_MUC_MODULES=meeting_report` (breakout rooms get their own folder). Full install, rollback and troubleshooting steps are in `MEETING-REPORT.md`.

Live transcription (Jigasi + Vosk) was tried and dropped: poor accuracy, too heavy for this host. For notes, record and run Whisper on the mp4 afterwards.

## Updating

Work in `/root/docker-jitsi-meet`.

- `.env` change :: `docker compose up -d --force-recreate`.
- `config.js` change :: `docker compose restart web`.
- prosody env/volume change :: `docker compose up -d prosody` (recreate; `restart` will not reload the module).
- etherpad 502 after a recreate :: `docker network connect docker-jitsi-meet_meet.jitsi caddy-caddy-1`.

### Upgrade images

Pinned to GHCR (`ghcr.io/jitsi/*`); docker.io lags months behind. Latest tag: [releases](https://github.com/jitsi/docker-jitsi-meet/releases).

```
cd /root/docker-jitsi-meet && docker compose pull && docker compose up -d
docker compose logs prosody | grep "mod_meeting_report loaded on"   # 2 lines = healthy
```

### Rootless migration (stable-11146, already applied)

New GHCR stable runs non-root (uid 1000) + s6-overlay v3 ([PR #2258](https://github.com/jitsi/docker-jitsi-meet/pull/2258), [handbook](https://jitsi.github.io/handbook/docs/devops-guide/docker)). One-time fixes, listed so a future dev can trace them to source; all are in `docker-compose.yml` except the two host commands:

- exec `/run` tmpfs on every service or it crash-loops :: `tmpfs: - /run:exec,mode=755` ([#2263](https://github.com/jitsi/docker-jitsi-meet/pull/2263)).
- web binds container `8000`/`8443`, so map host port to `:8000` ([#2261](https://github.com/jitsi/docker-jitsi-meet/pull/2261)).
- regenerate `~/.jitsi-meet-cfg/prosody` once for uid-1000 cert ownership, else jicofo fails `No stream features` ([#2304](https://github.com/jitsi/docker-jitsi-meet/pull/2304)): `mv` it aside, recreate `config/` + `prosody-plugins-custom/`, copy `mod_meeting_report.lua` back, `up -d prosody && restart jicofo`.
- `chmod -R 0777 ~/.jitsi-meet-cfg/meetings` so uid-1000 prosody can write reports.

### Keep in mind

- `.env` floats on `stable`; the next pull can bring another rootless change. Pin `stable-NNNNN` to avoid surprises.
- We still use the old volume layout; upstream moved to `storage/` + `tmp/`. Regenerate compose from stock at a major bump.
- jibri `cap_add: SYS_ADMIN` is no longer required (harmless, removable).
- iptables is not persisted (see SSH-lockout below); a reboot can drop host firewall rules.

## After a Linode reboot: SSH lockout

Symptom: host unreachable over SSH after a reboot, but LISH works and Jitsi/Caddy still serve traffic. Cause: the host `iptables` policy is `INPUT DROP` but the allow-rules were never persisted, so they vanish on reboot (container ports survive via Docker's own chains, only host SSH breaks).

Recover from LISH:

```
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
```

Fix for good: `apt install iptables-persistent && netfilter-persistent save`, or move inbound filtering to a Linode Cloud Firewall (allow 22, 80, 443, 10000/udp) and set the host INPUT policy back to ACCEPT.

## TODO

- [x] Force the fossunited team as moderator for everyone. Right now whoever
  joins first becomes mod; we want the team to be mod by default (likely needs
  auth or the JWT token above).
  We've a JWT which gives MOD access for any room if joined anytime.


## Changes

** Oct 2026**

- Switched images Docker Hub to GHCR (`ghcr.io/jitsi/*`) and bumped to current `stable` (non-root + s6-overlay v3). Required fixes in "Rootless migration" above.
- Removed "End meeting for all" (`ENABLE_END_CONFERENCE=0`); grant-moderator stays on.
- Added `gen-mod-token.py --universal` for a shared all-rooms moderator token.

**Jul 2026**

- Added the custom Lua meeting-report (chat + attendance per meeting).
- Tried Vosk transcription, dropped it (poor accuracy). Record + Whisper instead.

**Apr 2026**

- Bundled etherpad into jitsi and persisted its files with a volume.
- Set max resolution to 1080.
- Disabled adaptive mode so screen share stops dropping to save bandwidth.

## Server specs

```
uname -a
Linux localhost 6.1.0-37-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.1.140-1 (2025-05-22) x86_64 GNU/Linux
```

```text
=== CPU ===
CPU(s): 4   Model name: AMD EPYC 7713 64-Core Processor   (1 thread/core, 1 socket)

=== RAM ===
Mem: 7.8Gi total   Swap: 511Mi

=== Disk ===
/dev/sda  157G total, 134G free

=== OS ===
Debian GNU/Linux 12 (bookworm), kernel 6.1.0-37-amd64
```
