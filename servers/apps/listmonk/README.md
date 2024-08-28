# Listmonk 

[Listmonk](https://listmonk.app) is a bulk email sending service which is used heavily at FOSS United for sending out bulk emails to attendees, speakers and in general to the whole FOSS Community.

Server: Listmonk (Nanode: 1GB) 
App: Listmonk
Latest updated version: ```v3.0.0 (f9120d9 2024-02-04T11:20:27Z, linux/amd64)```
Install Dir: `/opt/listmonk`
Backups Dir: `/opt/backups`
OS: Ubuntu 20.04 LTS
RAM: 1GB
Database: Postgresql 

### self host instructions

- [`config.toml`](./config.toml) has all the necessary configuration to get listmonk up and running. 
- Listmonk is ran as a systemd service, here's the [listmonk.service](./listmonk.service).
- Backups were not automated previously, but it is now (date: 28/8/2024) automated to perform backups everyday at 4 AM. Please note, for backups `pg_dump` is being used.


Recent DB Password change happened on 28 Aug, 2024.
