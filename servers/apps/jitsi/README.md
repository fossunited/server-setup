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


## TODOs

- [ ] Make fossunited team moderator for all. Any guest who joins early becomes mod, we need Mod for team forcefully (may need Auth or some weird setup)

## Changes

### April 2026
- Connect etherpad to jitsi to make it usable
- Set max resolution to 1080
- Removed adaptive mode to avoid screen share getting turned off to save bandwidth
