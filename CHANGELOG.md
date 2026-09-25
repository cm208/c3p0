# Changelog

All notable changes are recorded here. This project follows [Semantic Versioning](https://semver.org/) from 1.3.0 onward.

## [1.4.0] - 2026-09-24

A redesign of the whole dashboard, plus the features the new design needed.

### Dashboard

- **Amber Phosphor Console.** Every page was rebuilt as a CRT terminal: a bezel and curved tube, an inverse-video status bar with the bot's live version, shard state and gateway latency, box-drawing frames, and a boot sequence on the login screen. The CRT effects can be turned down (`crt normal`) or off (`crt off`), and are off by default for anyone who prefers reduced motion.
- **Command line on every page.** Navigate (`general`, `music`, `mod`, `cd <page>`, F1-F7, F9 guilds, F10 logout), control music (`play <song>`, `skip`, `pause`, `resume`, `prev`, `stop`, `vol 60`), remove things (`rm !rules`, `rm #channel`), and change the display (`phosphor green`). History with up/down. Every command calls the same routes as the page's forms.
- **`(y/N)` confirmations.** Destructive actions (deleting commands, cases, roles, templates, channels in the draft; applying a template) ask in the command line instead of a browser dialog. Only `y` or `yes` proceeds.
- **Live SYSLOG.** Guild pages show a feed of what the bot is doing: member joins and leaves, custom command uses, moderation actions, and music (queued, now playing, skipped, stopped). Form results appear there as `[OK]` / `ERR` lines.
- **Message editor.** Every message the bot sends (welcome message, embed and DM; custom command responses; reaction-role messages) gets a formatting toolbar and a live Discord-style preview. Formatting, variables, role and channel mentions, and timestamps are one click instead of typed syntax.
- **Guild picker** shows member and online counts, and lists servers you manage that don't have C3P0 yet, with an invite link that preselects the server.
- **Server page** opens on the channel tree, with MEMBERS, ONLINE, CHANNELS and UPTIME tiles, a selected-channel panel, and quick channel creation.
- **Music** gains an oscilloscope and spectrum display, previous (restart track) and stop controls, the voice channel name, and a live queue.
- **Mod log** has per-action filter chips with counts.

### Bot

- **Custom command use counts** are recorded and shown on the dashboard.
- **New message variables:** `{count}` (short for `{member_count}`) and `{uptime}` (how long the bot has been running).
- **Creating a custom command from the dashboard** adds the server's prefix if the trigger leaves it off, and the name is now optional.

### Upgrading

- Database migration `0006` runs automatically on startup (adds the activity feed table and the command use counter). No new environment variables.

## [1.3.1] - 2026-09-24

### Fixed

- The bot crashed on startup in any freshly built image. SQLAlchemy 2.1 stopped installing `greenlet`, which the async database engine needs, and the old `SQLAlchemy>=2.0,<3.0` range let new builds pick up 2.1. The dependency is now `SQLAlchemy[asyncio]>=2.0,<2.1`.

## [1.3.0] - 2026-09-19

First public release. It rolls up everything built before it, including the two earlier milestones that were tracked internally as 2.0 (server management) and 2.5 (channel canvas and infraction management); those numbers are retired and versioning starts fresh here.

### Bot

- **Welcome**: channel or DM messages, embed or plain text, template variables, automatic default role, join logging.
- **Self-assignable roles**: reaction, button, and select-menu roles.
- **Moderation**: `kick`, `ban`, `unban`, `timeout`, `warn`, `warnings`, `clear`, `slowmode`, `lock`, `unlock`, with persistent infractions and configurable auto-escalation.
- **Custom commands**: template-only responses with cooldowns, role restrictions, and embed toggles. No code execution.
- **Music**: YouTube links or search terms, plus Spotify track, album, and playlist links resolved to audio automatically without any Spotify API credentials (albums and playlists queue their first track). Per-guild queues, shuffle and loop, DJ-role gating, and the requester shown on `!play` and `!queue`.

### Dashboard

- Discord OAuth2 login limited to servers where the user holds Manage Server.
- A settings page for every feature, plus live music controls. Tracks added from the dashboard can be announced in a Discord channel of your choice.
- **Server management**: role and channel create, edit, delete, and drag-and-drop reorder through a stage-then-apply canvas; per-role channel permission overwrites; layout templates with preview; an audit log of every change.
- **Infraction management**: a searchable, sortable, paginated log with per-infraction editing and removal.

### Deployment

- Multi-architecture (`amd64`, `arm64`) container image published to GitHub Container Registry, alongside a Compose file that runs the bot and dashboard.
- Setup guides for Discord, Docker deployment, and the dashboard in `docs/`.
- Continuous integration running the linter and the full test suite.

### Fixed

- Channels could vanish or duplicate in the channel canvas on real servers because Discord IDs lost precision when passed through JavaScript numbers. IDs are now strings end to end.
- A failed error reply (for example a missing Send Messages permission) no longer raises a second, noisier error on top of the original.

[1.3.0]: https://github.com/cm208/c3p0/releases/tag/v1.3.0
