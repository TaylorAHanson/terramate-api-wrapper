"""Liveness and build-info endpoints (architecture.md §11)."""
from __future__ import annotations

from fastapi import APIRouter

from server.version import build_info

router = APIRouter()


@router.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/version")
def version() -> dict[str, str]:
    return build_info()
