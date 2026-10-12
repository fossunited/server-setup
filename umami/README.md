# Umami

- site: https://docs.umami.is/
- source: https://github.com/umami-software/umami

Privacy-friendly web analytics (no cookies) for our GitHub-Pages sites. Runs on the vaultwarden Linode behind host nginx. Served at https://analytics.fossunited.org.

## Stack

- `umami` :: Next.js app, container port `3000`, published on `127.0.0.1:8091`.
- `umami-db` :: Postgres 15, data in `./db-data`, private `internal` network.

nginx proxies `analytics.fossunited.org` -> `127.0.0.1:8091` (loopback only, not exposed directly).

## First run

Run as a `docker`-group user; nginx/certbot need sudo.

1. Secrets:

   ```
   cd umami && cp .env.example .env
   openssl rand -hex 16   # DB_PASSWORD
   openssl rand -hex 32   # APP_SECRET
   ```

   Put them in `.env`, then PGP-encrypt the secret lines inline (Emacs
   `M-x epa-encrypt-region`), like the other apps.

2. Start (migrates on first boot):

   ```
   docker compose up -d
   docker compose ps     # wait for healthy
   curl -sf http://127.0.0.1:8091/api/heartbeat && echo OK
   ```

3. nginx + TLS (point DNS at this Linode first):

   ```
   sudo cp nginx-analytics.conf /etc/nginx/sites-available/analytics.fossunited.org
   sudo ln -s /etc/nginx/sites-available/analytics.fossunited.org /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d analytics.fossunited.org
   ```

## Login

Open the site, default `admin` / `umami`, change the password immediately
(Settings -> Profile).

## Add a site

Settings -> Websites -> Add website, then paste the snippet into the site
`<head>` (Jekyll: `_includes/head.html`):

```html
<script defer src="https://analytics.fossunited.org/script.js" data-website-id="YOUR-ID"></script>
```

## Ops

- Update: `docker compose pull && docker compose up -d`.
- Logs: `docker compose logs -f umami`.
- Backup: `docker compose exec umami-db pg_dump -U umami umami > umami-backup.sql`.
- ~150-250 MB RAM. Could reuse the host MySQL to save ~150 MB; kept on its own
  Postgres to stay self-contained.
