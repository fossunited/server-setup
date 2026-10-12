# Jitsi Meet (https://meet.fossunited.org)

Stock [docker-jitsi-meet](https://github.com/jitsi/docker-jitsi-meet). We only change `.env` and `config.js`, everything else is upstream default. Etherpad is bundled in for meeting notes. Runs on the meet host (specs at the bottom).

On the server the dir is in `/root/docker-jitsi-meet` and config lives in `~/.jitsi-meet-cfg`.

## Files here

- `.env` :: config.
- `config.js` :: web frontend tweaks, goes to `~/.jitsi-meet-cfg/web/config.js`.
- `docker-compose.yml` :: stock, plus etherpad and a `/meetings` volume.
- `prosody-plugins-custom/mod_meeting_report.lua` :: custom plugin, see below.
- `jibri/finalize.sh` :: moves a recording into its meeting folder.
- `gen-mod-token.py` :: makes a moderator JWT, `python gen-mod-token.py <name> [room]`.

## Custom: per-meeting report (Lua)

`mod_meeting_report.lua` saves one folder per meeting to `~/.jitsi-meet-cfg/meetings/<date>/<room>/` (IST) when the room closes:

- `chat.txt` :: group chat transcript.
- `attendance.csv` :: name, join, leave, duration_min, talk_min.
- `recording.mp4` + `metadata.json` :: only if the meeting was recorded.

Turned on in `.env` via `XMPP_MUC_MODULES=token_affiliation,meeting_report` and `XMPP_BREAKOUT_MUC_MODULES=meeting_report` (breakout rooms get their own folder). Full install, rollback and troubleshooting steps are in `MEETING-REPORT.md`.

Live transcription (Jigasi + Vosk) was tried and dropped: poor accuracy, too heavy for this host. For notes, record and run Whisper on the mp4 afterwards.

## Updating

Work in `/root/docker-jitsi-meet` on the server.

- After `.env` changes: `docker compose up -d --force-recreate`.
- Etherpad not loading? reconnect caddy to the jitsi network:
  `docker network connect docker-jitsi-meet_meet.jitsi caddy-caddy-1`.
- Testing a `config.js` change: `docker compose restart web`.
- Changed a prosody env or volume: recreate it (`docker compose up -d prosody`), a plain `restart` will not reload the module.

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

- [ ] Force the fossunited team as moderator for everyone. Right now whoever
  joins first becomes mod; we want the team to be mod by default (likely needs
  auth or the JWT token above).

## Changes

** Oct 2026**

- Did latest pull and updated jitsi to stable-11031

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
