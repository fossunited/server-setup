# Libredesk

- site: https://libredesk.io/
- source: https://github.com/abhinavxd/libredesk

Self-hosted support desk (email inbox, live chat, help center) that turns a support mailbox into tickets. Runs on its **own dedicated Linode**, so it ships its own Caddy container for TLS (separate from `caddy/`).

## Stack

- `app` :: Libredesk (Go), port `9000`, reachable only through Caddy. Config from a mounted `config.toml` (env-var-only config panics on required keys like `db.max_open`, so use the file).
- `db` :: Postgres 17, data in `./db-data`, also on `127.0.0.1:5432` for host `psql`/`pg_dump`.
- `redis` :: Redis 7, data in `./redis-data`, also on `127.0.0.1:6379`.
- `caddy` :: standalone Caddy, publishes 80/443, TLS for `support.fossunited.org` (change in `Caddyfile`).

`config.toml` holds the DB password and encryption key in plaintext, so it is gitignored and server-only. `.env` uses the usual inline PGP blocks and is committed.

## First run (new Linode)

Provision first :: Debian 13, Shared 2GB, add swap, install Docker Engine + Compose. Point the domain's DNS A record at the Linode before starting Caddy, or cert issuance fails.

1. Secrets:
   ```
   cd libredesk
   cp .env.example .env
   cp config.sample.toml config.toml
   openssl rand -hex 16   # ENCRYPTION_KEY (32 chars)
   openssl rand -hex 16   # POSTGRES_PASSWORD
   ```
   - In `.env`: set `POSTGRES_PASSWORD` and `LIBREDESK_SYSTEM_USER_PASSWORD`, then PGP-encrypt those two lines before committing.
   - In `config.toml`: set `[db].password` to the same `POSTGRES_PASSWORD` and `[app].encryption_key` to `ENCRYPTION_KEY`. Never committed; only needs to exist on the server.

2. Set the domain in `Caddyfile` if not using `support.fossunited.org`.

3. Start:
   ```
   docker compose up -d
   docker compose ps   # wait for db/redis healthy, app running
   ```

4. Visit `https://<domain>`, log in as email `System` with `LIBREDESK_SYSTEM_USER_PASSWORD`. If it was left empty: `docker exec -it libredesk_app ./libredesk --set-system-user-password`.

## Access control

SSO/OIDC sign-in **never auto-creates accounts** :: agents are created manually (Admin -> Agents), so access is restricted by simply not creating accounts for outsiders. Plain email/password is used for now; generic OpenID Connect is supported if that changes (see `configuration/sso.mdx` in the docs clone).

## Connecting the Google Group mailbox

Libredesk polls individual mailboxes, not mailing lists. To route the Google Group in:

1. Add a dedicated address as a **member** of the Google Group so it receives every list message.
2. Admin -> Inboxes -> New inbox -> Google, connect that mailbox (app password is quickest).
3. Agent replies go out via that mailbox's SMTP to the original sender.

Mailbox is scanned every 5 minutes, not instantly.

## Ops

- Update :: `docker compose pull && docker compose up -d`.
- Logs :: `docker compose logs -f app`. The app runs `--upgrade` on every start, so a bad upgrade shows in the logs right after `up -d`.

### Backups

`./db-data` is the live data dir, not a backup. Take point-in-time `pg_dump` snapshots.

`backup.sh` (create, `chmod +x`):
```bash
#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)
docker compose exec -T db pg_dump -U libredesk libredesk | gzip > "backups/libredesk-$STAMP.sql.gz"
find backups -name 'libredesk-*.sql.gz' -mtime +14 -delete
```

Cron (daily 03:00): `0 3 * * * /home/<user>/libredesk/backup.sh >> /home/<user>/libredesk/backup.log 2>&1`

`backups/` is gitignored and local-only, so copy it off-Linode for real disaster recovery (not set up yet).

Restore :: `gunzip -c backups/libredesk-<stamp>.sql.gz | docker compose exec -T db psql -U libredesk libredesk`.
