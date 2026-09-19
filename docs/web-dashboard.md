# Web dashboard

An optional browser dashboard for managing C3P0 per server, running as its own container (`c3p0-web`) next to the bot. Everything in it goes through the same services as the Discord commands, so a rule enforced in one place is enforced in the other.

## What's in it

- **General**: prefix, default role, log channels.
- **Welcome, Roles, Moderation, Custom Commands**: a full settings page for each, including creating reaction, button, and select-menu role messages, and a searchable, sortable, paginated infraction log.
- **Music**: configuration plus live now-playing, queue, and transport controls (add and remove tracks, pause, skip, volume). Tracks added from the dashboard can be announced in a Discord channel you choose.
- **Server management**: create, edit, delete, and drag-and-drop reorder roles and channels, edit per-role channel permissions, apply or save layout templates, and review an audit log of every change. This is the most powerful part of the dashboard, since it changes real server structure, so destructive actions ask for confirmation.

## Who can log in

Login is Discord OAuth2 and requests only the `identify` and `guilds` scopes. Anyone can sign in, but a session only shows servers where the person has **Manage Server** or **Administrator** and C3P0 is actually installed. Permissions are re-checked on a short interval, not trusted for the life of a session. Session cookies are random tokens, stored hashed, and never contain readable data.

The dashboard is built to be safe when reachable from the public internet: the login and permission checks are the security boundary, not the network path to it. It also can't grant anyone a permission they don't already hold.

## Setup

1. **Developer Portal.** Add the redirect URL and copy the client secret, as described in [discord-setup.md](discord-setup.md#6-optional-dashboard-login).
2. **`.env`.** Set `DISCORD_CLIENT_SECRET` and `PUBLIC_BASE_URL`, the exact `https://` address you'll reach the dashboard at, with no trailing slash.
3. **HTTPS.** Discord login and secure session cookies both require it. Pick one of the options below.
4. **Start it.** `docker compose up -d` starts both containers; `c3p0-web` waits for the bot to report healthy first, because only the bot runs database migrations.

## Getting HTTPS in front of it

The compose file publishes the dashboard on `127.0.0.1:8180` only. Either of these works.

**Reverse proxy on the same machine.** Point it at `127.0.0.1:8180`. A complete [Caddy](https://caddyserver.com/) example, including automatic certificates, is in [deployment.md](deployment.md#reaching-the-dashboard).

**Cloudflare Tunnel.** Useful when the machine has no public address or open ports. Create a tunnel in the Cloudflare Zero Trust dashboard under **Networks → Tunnels**, put its token in `.env` as `CLOUDFLARE_TUNNEL_TOKEN`, and add a `cloudflared` service to `compose.yaml` on the same Compose network:

```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
```

Then add a **Public Hostname** on that tunnel pointing at `http://c3p0-web:8080`. You can delete the dashboard's `ports:` entry in this setup.

If you already run a tunnel or proxy container elsewhere (another Docker network or another machine), it can't resolve `c3p0-web` by name. Change the dashboard's port mapping to `"8180:8080"`, point the public hostname at `http://<this-machine's-LAN-IP>:8180`, and keep that port firewalled from the internet so the tunnel is the only way in.

## Operational notes

- `docker compose logs -f c3p0-web` for dashboard logs.
- The dashboard and bot share one SQLite database in WAL mode, which is what makes access from two processes safe.
- `WEB_COOKIE_SECURE=false` exists only for testing over plain `http://` on your own machine. Never set it in a real deployment, because it stops session cookies being marked `Secure`.
- Live music controls work through a small internal API the bot exposes on port `8100`, protected by the shared `INTERNAL_API_TOKEN`. That port is never published to the host; the dashboard reaches it over the Compose network. Both containers must have the same token, which they do when both read one `.env`.
