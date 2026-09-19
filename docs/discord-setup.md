# Discord setup

Everything you do in the [Discord Developer Portal](https://discord.com/developers/applications) before C3P0 can run. Budget ten minutes.

## 1. Create the application and bot

1. **New Application**, give it a name.
2. From **General Information**, copy the **Application ID** into `.env` as `DISCORD_APPLICATION_ID`.
3. Open the **Bot** tab and choose **Reset Token**. Copy the token into `.env` as `DISCORD_TOKEN`. Treat it like a password: anyone holding it controls your bot. If it ever leaks, reset it here.
4. Leave **Public Bot** off unless you want strangers to be able to add your instance to their servers.

## 2. Enable the privileged intents

Still on the **Bot** tab, under **Privileged Gateway Intents**, turn on both:

| Intent | Why C3P0 needs it |
| --- | --- |
| **Server Members Intent** | Welcome messages, default roles, and looking up members for moderation |
| **Message Content Intent** | `!` prefix commands and custom commands both read message text |

Without them the bot exits at startup with a privileged-intents error. C3P0 does not use the Presence intent, so leave that one off.

> Discord lets any bot in fewer than 100 servers enable these freely. A bot in 100 or more servers has to apply for approval, which only matters if you run a large public instance.

## 3. Invite the bot to your server

Replace `YOUR_APPLICATION_ID` and open the link while logged in as someone with **Manage Server** on the target server:

```
https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands&permissions=1099783302230
```

That link requests these permissions and nothing more (Administrator is not required):

| Permission | Used for |
| --- | --- |
| View Channels, Send Messages, Embed Links, Read Message History | Everything: replying, embeds, welcome messages |
| Add Reactions | Reaction roles |
| Manage Roles | Auto-assigned and self-assignable roles, role management in the dashboard, channel permission overwrites |
| Manage Channels | `!lock` / `!unlock` / `!slowmode`, channel management in the dashboard |
| Manage Messages | `!clear` |
| Kick Members, Ban Members, Moderate Members | `!kick`, `!ban` / `!unban`, `!timeout` |
| Connect, Speak | Music |

Trim the list if you don't use a feature. If a command does nothing, a missing permission on that channel is the first thing to check (channel-specific overrides can deny the bot even when its role allows it).

## 4. Move the bot's role up

Discord only lets a bot manage roles **below** its own highest role. After the bot joins, open **Server Settings → Roles** and drag the bot's role above any role you want it to assign, edit, or moderate around. The dashboard shows roles above the bot as read-only for the same reason.

## 5. Slash commands take a moment to appear

By default C3P0 registers its slash commands globally at startup, and Discord can take a while (up to an hour) to show them everywhere. To get them instantly on your own server, set your server's ID in `.env` and restart:

```
DISCORD_DEV_GUILD_IDS=123456789012345678
```

(Enable **Developer Mode** in Discord's advanced settings, then right-click your server icon and choose **Copy Server ID**. Separate several IDs with commas.) Prefix commands like `!play` work immediately either way.

## 6. Optional: dashboard login

Only needed if you want the web dashboard. On the **OAuth2** tab:

1. Copy the **Client Secret** into `.env` as `DISCORD_CLIENT_SECRET`.
2. Under **Redirects**, add `${PUBLIC_BASE_URL}/auth/callback`, exactly as it will appear in your browser, scheme included. It must match `PUBLIC_BASE_URL` character for character, or Discord rejects the login with `INVALID_OAUTH2_REDIRECT`.

The dashboard asks users only for the `identify` and `guilds` scopes, and shows each person just the servers where they already have **Manage Server**. The rest of the dashboard setup, including HTTPS, is in [web-dashboard.md](web-dashboard.md).

## Verify it worked

In your server, run `/config show`. If C3P0 answers, the token, intents, and invite are all correct.
