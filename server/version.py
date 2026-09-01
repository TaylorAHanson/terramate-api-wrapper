"""Build info surfaced by GET /version.

`GIT_SHA` and `BUILD_TIME` are set by CI at build time (see
.github/workflows/ci.yml); locally and in a dev checkout they fall back to
"unknown" rather than shelling out to git, so importing this module never has
a side effect.
"""
from __future__ import annotations

import os

APP_VERSION = "0.1.0"


def build_info() -> dict[str, str]:
    return {
        "version": APP_VERSION,
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
        "build_time": os.environ.get("BUILD_TIME", "unknown"),
    }
