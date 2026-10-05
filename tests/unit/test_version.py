"""`build_info()` (#65): the build-info dict surfaced by GET /version.

`GIT_SHA` / `BUILD_TIME` come from CI at build time and fall back to "unknown"
locally. These are pure, no-DB unit tests that pin that contract; they use
`monkeypatch` to set/clear the env so they never depend on the real process
environment.
"""
from __future__ import annotations

from server.version import APP_VERSION, build_info


def test_falls_back_to_unknown_when_env_unset(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("BUILD_TIME", raising=False)

    info = build_info()

    assert info["git_sha"] == "unknown"
    assert info["build_time"] == "unknown"


def test_reports_env_values_when_set(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc1234")
    monkeypatch.setenv("BUILD_TIME", "2026-09-29T00:00:00Z")

    info = build_info()

    assert info["git_sha"] == "abc1234"
    assert info["build_time"] == "2026-09-29T00:00:00Z"


def test_version_equals_app_version(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("BUILD_TIME", raising=False)

    assert build_info()["version"] == APP_VERSION


def test_result_has_exactly_the_three_expected_keys(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("BUILD_TIME", raising=False)

    assert set(build_info()) == {"version", "git_sha", "build_time"}
