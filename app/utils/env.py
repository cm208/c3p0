"""Shared environment-variable parsing helpers.

Used by both app/config.py (the bot process) and app/web/config.py (the
dashboard process) so the two don't duplicate parsing logic or import each
other's private names.
"""

from __future__ import annotations

import os


class EnvError(RuntimeError):
    """Raised when an environment variable is present but malformed."""


def get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise EnvError(f"{name} must be an integer, got {raw!r}") from exc


def get_int_list(name: str) -> list[int]:
    raw = os.environ.get(name, "")
    values: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.append(int(chunk))
        except ValueError as exc:
            raise EnvError(f"{name} must be a comma-separated list of IDs, got {raw!r}") from exc
    return values
