"""Health/readiness signal for Docker.

Rather than opening a second HTTP server just for health checks, we expose
readiness via a small text file on disk that the bot updates on gateway
connect/disconnect. The Docker healthcheck (see Dockerfile) reads this file
directly - no extra port, no extra dependency.

This intentionally does not use the metrics HTTP server: metrics should stay
up even if we want health semantics to differ from "process is running"
(e.g. "gateway is connected").
"""

from __future__ import annotations

import time
from pathlib import Path

_DEFAULT_HEALTH_FILE = Path("/data/health")
_STALE_AFTER_SECONDS = 90


def mark_healthy(path: Path = _DEFAULT_HEALTH_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()))


def mark_unhealthy(path: Path = _DEFAULT_HEALTH_FILE) -> None:
    if path.exists():
        path.unlink(missing_ok=True)


def is_healthy(path: Path = _DEFAULT_HEALTH_FILE, stale_after: int = _STALE_AFTER_SECONDS) -> bool:
    if not path.exists():
        return False
    try:
        last = float(path.read_text().strip())
    except (ValueError, OSError):
        return False
    return (time.time() - last) < stale_after


if __name__ == "__main__":
    # Used directly by the Docker HEALTHCHECK instruction:
    #   HEALTHCHECK CMD python -m app.health || exit 1
    import sys

    sys.exit(0 if is_healthy() else 1)
