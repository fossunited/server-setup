# Mattermost

Standalone binary install, `/opt/mattermost`, systemd-managed. Runs on its own dedicated server, with its own Caddy instance.

## Files here

- `config.json` :: sanitized reference copy. `SqlSettings.DataSource` / `ElasticsearchSettings.Password` are placeholders, not real values; the real config with real credentials lives only on the server.

- `caddy/Caddyfile` :: this server's own Caddy config, fronts `mattermost.fossunited.org` only. DNS-only in Cloudflare, plain HTTP-01 cert :: no `acme_dns`, no API token.
- `mattermost.service` / `caddy.service` :: copies of the live systemd units (`/lib/systemd/system/` on the server). Edit there, then copy back here to keep in sync, no automated deploy.

## Maintenance

- Restart: `sudo systemctl restart mattermost` / `caddy`.

- Logs: `journalctl -u mattermost -f` / `journalctl -u caddy -f`.

- Runs as the `mattermost` user, not root :: the "running as root" warning only shows if you invoke `./bin/mattermost` manually from a root shell.

- Live Caddy config is `/etc/caddy/Caddyfile` on that server :: keep in sync with `caddy/Caddyfile` here by hand, no automated deploy.

- Feature flags are env vars, not System Console settings:
  ```bash
  sudo systemctl edit mattermost
  # [Service]
  # Environment=MM_FEATUREFLAGS_<NAME>=true
  sudo systemctl daemon-reload && sudo systemctl restart mattermost
  ```
  - `WYSIWYGEDITOR` :: beta rich-text composer (v11.10+), free.
  - `RECURRINGSCHEDULEDPOSTS` :: weekly recurring scheduled posts (v11.11+), needs a Professional+ license too :: not usable on this Team Edition install.

## Upgrading

Check before any major-version jump (v11 breaking changes):

- `TeamSettings.ExperimentalViewArchivedChannels` not `false` :: server won't start on v11 otherwise.
- `ExperimentalSettings.ClientSideCertEnable` is `false`.
- Bleve search off, `SqlSettings.DisableDatabaseSearch` is `false`.
- PostgreSQL >= 14.
- no automation uses removed `mmctl` subcommands (`channel add/remove/restore/make-private`, `command delete`, `permissions show`, `user email`, `user username`).

```bash
# backup -- DB dump + small dirs only, the upgrade never touches data/
sudo -u postgres pg_dump mattermost > /root/mattermost_backup_$(date +%F).sql
sudo cp -a /opt/mattermost/config /opt/mattermost-config-backup-$(date +%F)
sudo cp -a /opt/mattermost/plugins /opt/mattermost-plugins-backup-$(date +%F)
sudo cp -a /opt/mattermost/client/plugins /opt/mattermost-client-plugins-backup-$(date +%F)

# download + extract target version (Team Edition)
cd /tmp
wget https://releases.mattermost.com/X.X.X/mattermost-team-X.X.X-linux-amd64.tar.gz
tar -xf mattermost-team-X.X.X-linux-amd64.tar.gz --transform='s,^[^/]\+,\0-upgrade,'

# stop, swap preserving config/logs/plugins/client-plugins/data, fix ownership
sudo systemctl stop mattermost
cd /opt
sudo find mattermost/ mattermost/client/ -mindepth 1 -maxdepth 1 \! \( -type d \( -path mattermost/client -o -path mattermost/client/plugins -o -path mattermost/config -o -path mattermost/logs -o -path mattermost/plugins -o -path mattermost/data \) -prune \) | sort | sudo xargs rm -r
sudo cp -an /tmp/mattermost-upgrade/. /opt/mattermost/
sudo chown -R mattermost:mattermost /opt/mattermost

# start, verify
sudo systemctl start mattermost
journalctl -u mattermost --no-pager -f
/opt/mattermost/bin/mattermost version
```

Rollback: stop the service, restore the config/plugins backups above (or the full `/opt/mattermost-back-<timestamp>` copy if one was taken) plus re-extract the old version's tarball, restart. Restore the `pg_dump` too if the DB schema was migrated on startup :: file swaps alone won't undo that.

## Changes

**Oct 2026**

- Upgraded 10.8.1 -> 11.11.0. All pre-upgrade checks passed, no config changes needed.
- Fixed TLS: `mattermost.fossunited.org` was Cloudflare-proxied with DNS-01 (`acme_dns` + Cloudflare API token), which broke when the token lost visibility into the `fossunited.org` zone (Cloudflare error 525). Switched to DNS-only + plain HTTP-01, simpler and matches every other subdomain in this repo.
