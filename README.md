Note: Make sure no secrets are leaked!
```bash
gitleaks detect --source . -v
```

# Server Setups and Stuff we Self Host

Complete Stack we depend on: https://fossunited.org/stack

Shell scripts, ansible scripts and config files for foss united servers and self-hosted applications.

### List of Applications

- [Listmonk](https://listmonk.app) - For sending bulk emails and newsletter

  https://listmonk.fossunited.org (only admin access)

- [Vaultwarden](./servers/apps/vaultwarden/README.md) - Password Management on Organization and User level

  https://vault.fossunited.org/  

- [Mattermost](./servers/apps/mattermost/mattermost.md) - For Internal team discussions

  https://mattermost.fossunited.org (only team access)

- [Livecode](https://github.com/fossunited/falcon) -  A service that execute code in any programming language in a sandboxed environment.


- [Jisti](https://github.com/jitsi/jitsi-meet) - For all Meetings in and around organization

  https://meet.fossunited.org (open for all to use)

  - It also has Etherpad lite for meeting notes

    https://meet.fossunited.org/etherpad/p/dtest (open for all)

- [Caddy file server](https://caddyserver.com/) - For file sharing with community

  https://files.fossunited.org/ (seldom used, has some files)


#### Shutdown or seldom used
- [Nextcloud](./servers/apps/nextcloud/README.md) - Nextcloud instance self hosted for FOSS United Team's internal usage, and for switching from the GSuite (like Google Docs, Google Sheets).
- [Librechat](https://www.librechat.ai/) - AI chat platform/search engine called [Echo](https://echo.fossunited.org)
