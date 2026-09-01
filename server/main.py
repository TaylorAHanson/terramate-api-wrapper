"""FastAPI entrypoint: the `/v1` HTTP layer plus the built React shell.

Databricks Apps expose one port, so the API and the frontend share this
process: API routes are mounted first, then the React build (`frontend/dist`)
is served as static files with an index.html fallback for client-side
routing. Locally the frontend instead runs its own dev server and proxies
`/v1` and `/version` to this one (see frontend/vite.config.ts).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.routes import health, requests

app = FastAPI(title="Terramate Provisioning API")

app.include_router(health.router)
app.include_router(requests.router)

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
