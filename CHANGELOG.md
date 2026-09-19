# Changelog

All notable changes are recorded here. This project follows [Semantic Versioning](https://semver.org/) from 1.3.0 onward.

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
