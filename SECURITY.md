# Security policy

## Reporting a vulnerability

Please report security issues privately, not in a public issue. Use the **Report a vulnerability** button under this repository's **Security** tab to open a private advisory with the maintainer.

Include what you found, how to reproduce it, and which version or commit you tested. You can expect an acknowledgement within a few days. This is a personal project, so fix timelines depend on severity and availability, but credible reports are taken seriously.

## Scope

C3P0 is designed to be exposed to the public internet, so the following are all in scope:

- Anything that lets a user act beyond their own Discord permissions, on the dashboard or through the bot (privilege escalation, hierarchy bypass).
- Authentication, session, or CSRF weaknesses in the dashboard.
- Injection through custom command templates or any other user-supplied text.
- Exposure of the bot token, OAuth client secret, or `INTERNAL_API_TOKEN`.

## Supported versions

Only the latest release receives fixes.

## Operating it safely

- Keep `.env` private and never commit it. If the bot token leaks, reset it in the Discord Developer Portal.
- Keep the internal API port (`8100`) unpublished, as the shipped `compose.yaml` does.
- Serve the dashboard over HTTPS only.
