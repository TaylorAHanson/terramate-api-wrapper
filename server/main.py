"""FastAPI entrypoint: the `/v1` HTTP layer, the reconcile driver, and the
built React shell.

Databricks Apps expose one port, so the API and the frontend share this
process: API routes are mounted first, then the React build (`frontend/dist`)
is served as static files with an index.html fallback for client-side
routing. Locally the frontend instead runs its own dev server and proxies
`/v1` and `/version` to this one (see frontend/vite.config.ts).

The same single process also runs the reconcile driver (server.scheduler, #39)
as a background task managed by the app lifespan — the motor that advances
Steps. It only starts when a real GitHubClient can be built (GITHUB_PAT +
GITHUB_REPO); locally and in Seam 1/2 tests it stays off, and those tests drive
`orchestrator.tick()` / `scheduler.run_tick_once` directly instead.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.config import get_settings
from server.github_client import GitHubClient, RealGitHubClient
from server.logging_config import configure_logging
from server.routes import admin, health, requests
from server.scheduler import reconcile_loop

logger = logging.getLogger(__name__)


def _build_reconcile_client() -> GitHubClient | None:
    """The GitHubClient the driver reconciles through, or None to stay off.

    Off is the correct local/test default: without a PAT and repo there is no
    real GitHub to poll, so the driver would have nothing to do.
    """
    settings = get_settings()
    if not (settings.github_pat and settings.github_repo):
        logger.info("reconcile driver disabled: GITHUB_PAT/GITHUB_REPO not configured")
        return None
    return RealGitHubClient.from_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("app_startup environment=%s log_level=%s", settings.app_environment, settings.log_level)
    client = _build_reconcile_client()
    task: asyncio.Task | None = None
    if client is not None:
        task = asyncio.create_task(
            reconcile_loop(
                github_client=client,
                interval_seconds=settings.reconcile_interval_seconds,
            )
        )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if isinstance(client, RealGitHubClient):
            client.close()


app = FastAPI(title="Terramate Provisioning API", lifespan=lifespan)

app.include_router(health.router)
app.include_router(requests.router)
app.include_router(admin.router)

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str) -> FileResponse:
        candidate = _FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
