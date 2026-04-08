# Server

- uname -a
`Linux localhost 6.1.0-37-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.1.140-1 (2025-05-22) x86_64 GNU/Linux`

```bash
echo "=== CPU ==="
lscpu | grep -E 'Model name|CPU\(s\)|Thread|Core|Socket'

echo -e "\n=== RAM ==="
free -h

echo -e "\n=== Disk ==="
df -h /

echo -e "\n=== OS ==="
cat /etc/os-release | grep PRETTY_NAME

echo -e "\n=== Kernel ==="
uname -r
```

```text
=== CPU ===
CPU(s):                                  4
On-line CPU(s) list:                     0-3
Model name:                              AMD EPYC 7713 64-Core Processor
BIOS Model name:                         pc-q35-7.2  CPU @ 2.0GHz
Thread(s) per core:                      1
Core(s) per socket:                      4
Socket(s):                               1
NUMA node0 CPU(s):                       0-3

=== RAM ===
               total        used        free      shared  buff/cache   available
Mem:           7.8Gi       2.1Gi       2.9Gi       2.3Mi       3.0Gi       5.7Gi
Swap:          511Mi       103Mi       408Mi

=== Disk ===
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda        157G   16G  134G  11% /

=== OS ===
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"

=== Kernel ===
6.1.0-37-amd64
```

# Jitsi Meet on Docker

[Jitsi](https://jitsi.org/) is a set of Open Source projects that allows you to easily build and deploy secure videoconferencing solutions.

[Jitsi Meet](https://jitsi.org/jitsi-meet/) is a fully encrypted, 100% Open Source video conferencing solution that you can use all day, every day, for free — with no account needed.

This repository contains the necessary tools to run a Jitsi Meet stack on [Docker](https://www.docker.com) using [Docker Compose](https://docs.docker.com/compose/).

- The docker setup is directly from: https://github.com/jitsi/docker-jitsi-meet

Only config changes to `.env` is made, thats it.

- `config.js`: jitsi frontend Web configuration
  - ~/.jitsi-meet-cfg/web/config.js

- `docker-compose.yml` is same as default
  just that etherpad is included to bundle with jitsi
  
- `.env` file is configured as required.

## Updating

- Modify `.env` file and run: `docker compose up -d --force-recreate` in that dir (~/docker-jitsi-meet)
- `docker network connect docker-jitsi-meet_meet.jitsi caddy-caddy-1` (if caddy network is not synced with jitsi for etherpad to work)
- `docker compose -f ~/docker-jitsi-meet/docker-compose.yml restart web` (restart web if testing frontned changes via config.js)


## TODOs

- [ ] Make fossunited team moderator for all. Any guest who joins early becomes mod, we need Mod for team forcefully (may need Auth or some weird setup)

## Changes

### April 2026
- Connect etherpad to jitsi to make it usable
- Set max resolution to 1080
- Removed adaptive mode to avoid screen share getting turned off to save bandwidth
- Make etherpad files persist by mounting volume
