"""Template-only display formatting helpers, registered as Jinja filters in
app/web/app.py rather than computed inline in templates (this codebase's
templates otherwise do no arithmetic of their own)."""

from __future__ import annotations


def format_duration(seconds: float | int | None) -> str:
    """Render a duration as M:SS, or H:MM:SS past an hour. "--:--" for
    None (unknown duration, or nothing playing)."""
    if seconds is None:
        return "--:--"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def progress_percent(elapsed: float | None, duration: int | None) -> float:
    """0-100 playback-progress percentage, for the now-playing bar's width.

    0 whenever either value is missing or duration isn't positive - avoids
    a divide-by-zero for a track whose length yt-dlp couldn't determine.
    """
    if elapsed is None or not duration or duration <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * elapsed / duration))
