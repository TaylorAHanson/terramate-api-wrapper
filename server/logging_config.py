"""One-time logging setup for the deployed app (#41, architecture.md §14).

The engine logs through per-module `logging.getLogger(__name__)` loggers under
the `server.*` tree. Python's root logger, left unconfigured, only surfaces
`WARNING`+ via its last-resort handler — so without this the `INFO` state-
transition and PR-open lines the engine emits would be silently dropped in a
deployed `uvicorn server.main:app` process. `configure_logging()` installs a
single stream handler + level so those lines actually reach stdout, where the
Databricks Apps log drain collects them.

Level is read from `LOG_LEVEL` (default `INFO`); set `LOG_LEVEL=DEBUG` to also
surface the per-call GitHub transport lines (`server.github_client`).

Log lines are intentionally flat `event key=value ...` records — greppable and
parseable without a JSON log pipeline, and cheap (`%`-style, so args are only
formatted when the line is actually emitted).
"""
from __future__ import annotations

import logging
import os

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# The engine's loggers all live under this package, so setting the level here
# surfaces them even when a root handler already exists (uvicorn installs one),
# which would otherwise make a plain basicConfig level change a no-op.
_APP_LOGGER = "server"


def configure_logging(level: str | None = None) -> None:
    """Install the app's log handler + level. Idempotent and safe to call once
    at startup (server.main lifespan); tests may call it directly.

    `level` overrides `LOG_LEVEL`; both default to `INFO`. An unknown level
    name falls back to `INFO` rather than raising — a bad env var must never
    take the app down on startup.
    """
    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    resolved = logging.getLevelName(level_name)
    if not isinstance(resolved, int):
        resolved = logging.INFO

    # basicConfig installs a root stream handler the first time only, so a
    # process that already has one (uvicorn) keeps its single handler and we
    # don't double up. Either way we then force our package logger's level, so
    # INFO surfaces regardless of what the root level ended up being.
    logging.basicConfig(level=resolved, format=_LOG_FORMAT)
    logging.getLogger(_APP_LOGGER).setLevel(resolved)
