# Miniflux

- site: https://miniflux.app/
- source: https://github.com/miniflux/v2

Self-hosted RSS reader for the team. Runs on the vaultwarden Linode behind host nginx. Served at https://reader.fossunited.org.

Team-only, no public signup. Admin adds users (Settings -> Users -> Add user); local username/password, no OAuth. Share credentials, they reset on first login. (SSO path if ever wanted: Google Workspace Internal OAuth app + `OAUTH2_PROVIDER=google`, `OAUTH2_USER_CREATION=1`, `DISABLE_LOCAL_AUTH=true`.)

Maintainers community may request if they have habit of reading more.

## Stack

- `miniflux` :: Go app, container port `8080`, published on `127.0.0.1:8092`.
- `miniflux-db` :: Postgres 17, data in `./db-data`, private `internal` network.

## First run

Run as a `docker`-group user; nginx/certbot need sudo.

1. Secrets:

   ```
   cd miniflux && cp .env.example .env
   openssl rand -hex 16      # DB_PASSWORD
   openssl rand -base64 18   # ADMIN_PASSWORD
   ```

   Set values in `.env`, then PGP-encrypt the secret lines inline for git.

2. Start (migrates + creates admin on first boot):

   ```
   docker compose up -d
   docker compose ps     # wait for healthy
   curl -sf http://127.0.0.1:8092/healthcheck && echo OK
   ```

3. nginx + TLS (point DNS at this Linode first):

   ```
   sudo cp nginx-reader.conf /etc/nginx/sites-available/reader.fossunited.org
   sudo ln -s /etc/nginx/sites-available/reader.fossunited.org /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d reader.fossunited.org
   ```

## Onboard the team

Log in as `admin`, change the password (Settings -> Password). Add one user per
teammate (Settings -> Users -> Add user).

## Mobile / desktop apps

Each user enables a sync protocol under Settings -> Integrations:

- Google Reader API :: Reeder, NetNewsWire, Capy Reader. Set user + password,
  point the app at `https://reader.fossunited.org`.
- Fever API :: FeedMe, Fiery Feeds. Set a Fever password; endpoint
  `https://reader.fossunited.org/fever/`.
- REST API :: for scripts; create a key in Settings -> API Keys.

## Ops

- Update: `docker compose pull && docker compose up -d`.
- Logs: `docker compose logs -f miniflux`.
- Backup: `docker compose exec miniflux-db pg_dump -U miniflux miniflux > miniflux-backup.sql`.
- ~50-100 MB RAM consumption.
