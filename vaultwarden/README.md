## Setup for FOSS United Org Vaultwarden Instance

Vaultwarden is self hosted on the same server as the mon-web2 and the livecode server. 

- Server: vault-analytics
- OS: Ubuntu
- Webserver: Caddy inside Docker Container

The docker-compose file is located in `/home/mangesh/vaultwarden` on the server. The hash for ADMIN_PASSWORD with generated with argon2. Check [wiki](https://github.com/dani-garcia/vaultwarden/wiki/Enabling-admin-page) for key generation guide.


## Changes

** Oct 2026**

- Updated to latest from 2024 version -> 2026 version. Just new UI.
