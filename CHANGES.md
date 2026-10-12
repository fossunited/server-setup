# Changes to self-hosted apps

This file documents changes made to applications self-hosted by FOSS United.

## 08 April 2026
- Updated Listmonk: `6.0.0 -> 6.1.0`
Had some security fixes so:  https://github.com/knadh/listmonk/releases/tag/v6.1.0

## 09 March 2026
- Updated Listmonk: `5.1.0 -> 6.0.0`
  https://github.com/knadh/listmonk/releases/tag/v6.0.0

Note: Listmonk backup files are stored in `~/listmon-${date}` or even in `/opt/backups/`

## 24 November 2025

- Updated Listmonk from `5.0.2 -> 5.1.0`
  https://github.com/knadh/listmonk/releases/tag/v5.1.0

## 14 October 2024

* Update Discourse and Discourse `docker_manager` to latest versions. The
  update was performed by logging into the Digital Ocean machine that hosts
  Discourse and running the necessary commands. OS updates were also applied
  and unused disk space was reclaimed by removing stopped & unused containers.

  @rahulporuri now has access to the Digital Ocean machine via SSH but please
  note that the Digital Ocean account isn't owned by the FOSS United org.

