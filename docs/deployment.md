# Deployment

C3P0 runs as two containers from one image: **`c3p0`** (the bot) and **`c3p0-web`** (the dashboard). They share one SQLite database in a Docker volume. You need Docker with the Compose plugin and a machine that stays on. The image is published for both `amd64` and `arm64`, so a Raspberry Pi works.

Do the [Discord setup](discord-setup.md) first; you need the token and application ID it produces.

## Quickstart

```bash
git clone https://github.com/cm208/c3p0.git
cd c3p0
cp .env.example .env
```

Edit `.env` and set at least `DISCORD_TOKEN`, `DISCORD_APPLICATION_ID`, and `INTERNAL_API_TOKEN` (generate the last one with `openssl rand -hex 32`). Then:

```bash
docker compose pull
docker compose up -d c3p0
```

That runs the bot only. Migrations run automatically on first start. To also run the dashboard, finish the dashboard variables in `.env` (see [web-dashboard.md](web-dashboard.md)) and start it too:

```bash
docker compose up -d
```

> Starting `c3p0-web` without `DISCORD_CLIENT_SECRET` and `PUBLIC_BASE_URL` set makes it exit with a configuration error and restart in a loop. The bot is unaffected, but if you don't want the dashboard, start only `c3p0` as above.

To build from source instead of pulling the published image, use `docker compose up -d --build`.

## Verify

```bash
docker compose ps                    # c3p0 should report "healthy"
docker compose logs -f c3p0          # look for "Bot ready"
curl -s localhost:8000/metrics | head
curl -s localhost:8180/healthz       # dashboard only
```

`c3p0` reports healthy only once it is connected to the Discord gateway, so a bad token shows up here as a container that never becomes healthy. Then run `/config show` in your server.

Logs are JSON by default. Set `LOG_HUMAN=true` in `.env` for readable console output.

## Configuration

Everything else is configured from Discord slash commands or the dashboard. Environment variables are for secrets and infrastructure only. `.env.example` documents every variable and its default.

| Variable | Needed for | Notes |
| --- | --- | --- |
| `DISCORD_TOKEN` | bot, dashboard | From the Bot tab |
| `DISCORD_APPLICATION_ID` | bot, dashboard | Doubles as the OAuth2 client ID |
| `INTERNAL_API_TOKEN` | bot, dashboard | Shared secret; must be identical for both, which one `.env` guarantees |
| `DISCORD_CLIENT_SECRET` | dashboard | OAuth2 page |
| `PUBLIC_BASE_URL` | dashboard | Exact public `https://` URL, no trailing slash |
| `DISCORD_DEV_GUILD_IDS` | optional | Comma-separated server IDs for instant slash-command sync |
| `DEFAULT_PREFIX` | optional | Starting prefix for new servers, default `!` |

## Data and backups

The database lives in the `c3p0-data` Docker volume, mounted at `/data` in both containers. It runs in WAL mode, so **don't copy the file while the bot is running**; use SQLite's backup API, which is safe against a live database:

```bash
docker compose exec c3p0 python -c "import sqlite3; s = sqlite3.connect('/data/c3p0.db'); d = sqlite3.connect('/data/backup.db'); s.backup(d); d.close(); s.close()"
docker compose cp c3p0:/data/backup.db "./c3p0-$(date +%F).db"
docker compose exec c3p0 rm /data/backup.db
```

To restore, stop the stack with `docker compose down` (never `-v`; that deletes the volume), then copy the backup over `c3p0.db` inside the volume and make sure it is owned by uid `999`, the container's non-root user. Also delete any leftover `c3p0.db-wal` and `c3p0.db-shm` files there so they don't replay over the restored copy.

Prefer a plain folder on the host? Replace `c3p0-data:/data` with `./data:/data` in `compose.yaml` and run `mkdir -p data && sudo chown 999:999 data` first. Without the `chown`, the non-root container can't create the database and fails with `unable to open database file`.

## Updating

```bash
docker compose pull
docker compose up -d
```

Schema migrations apply on startup. Pin a version instead of `latest` by changing `image:` in `compose.yaml` to a tag such as `ghcr.io/cm208/c3p0:1.3.0`. [Releases](https://github.com/cm208/c3p0/releases) list what changed.

Music depends on `yt-dlp`, and YouTube changes often enough that a copy baked into an old image will eventually stop resolving tracks. If `!play` starts failing on links that work in a browser, update first: pull the newest image, or rebuild from source with `docker compose build --no-cache --pull` to fetch the latest `yt-dlp`.

## Reaching the dashboard

The compose file publishes the dashboard on `127.0.0.1:8180` only. Discord login requires HTTPS, so put a reverse proxy or tunnel in front of it. With [Caddy](https://caddyserver.com/), which handles certificates for you, that is:

```
c3p0.example.com {
    reverse_proxy 127.0.0.1:8180
}
```

Set `PUBLIC_BASE_URL=https://c3p0.example.com` and register `https://c3p0.example.com/auth/callback` in the Developer Portal. [web-dashboard.md](web-dashboard.md) also covers Cloudflare Tunnel. For local testing over plain HTTP only, set `WEB_COOKIE_SECURE=false`, and never in production.

## Security defaults

No privileged mode, no host networking, no Docker socket. The containers run as a non-root user. Metrics and the dashboard bind to localhost. The internal control-plane port (`8100`) is never published; the dashboard reaches it over the Compose network with a bearer token.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `unable to open database file` | A `./data` bind mount the container's user can't write to. Use the default named volume, or `chown 999:999 data`. |
| Bot exits immediately with a privileged intents error | Enable Server Members and Message Content intents on the Bot tab. |
| Container never becomes healthy | Usually a bad `DISCORD_TOKEN`. Check `docker compose logs c3p0`. |
| `c3p0-web` restarts in a loop | `DISCORD_CLIENT_SECRET` or `PUBLIC_BASE_URL` is unset. Set them, or run only `c3p0`. |
| Slash commands don't show up | Global sync is slow. Set `DISCORD_DEV_GUILD_IDS` and restart. |
| Dashboard login fails with `INVALID_OAUTH2_REDIRECT` | The redirect URL in the Developer Portal doesn't match `PUBLIC_BASE_URL` exactly. |
| Bot can't assign or edit a role | The role is above the bot's highest role. Move the bot's role up. |
| `!play` fails on working links | Stale `yt-dlp`. Update the image as described above. |
