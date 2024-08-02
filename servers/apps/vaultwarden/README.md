## Setup for FOSS United Org Vaultwarden Instance

Vaultwarden is self hosted on the same server as the mon-web2 and the livecode server. 

- Server: mon-web2 
- OS: Ubuntu
- Webserver: Caddy inside Docker Container

The docker-compose file is located in `/home/mangesh/vaultwarden` on the server. The hash for ADMIN_PASSWORD with generated with argon2. Check [wiki](https://github.com/dani-garcia/vaultwarden/wiki/Enabling-admin-page) for key generation guide.
