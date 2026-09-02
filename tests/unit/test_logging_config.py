"""`configure_logging` (#41): the one-time log setup that makes the engine's
`server.*` INFO lines actually surface in a deployed process. Pure unit tests —
they only inspect the resulting logger level, so they restore it afterwards.
"""
from __future__ import annotations

import logging

import pytest

from server.logging_config import configure_logging

_APP_LOGGER = "server"


@pytest.fixture(autouse=True)
def _restore_level():
    """Snapshot and restore the package logger level, since configure_logging
    mutates global logging state."""
    logger = logging.getLogger(_APP_LOGGER)
    previous = logger.level
    try:
        yield
    finally:
        logger.setLevel(previous)


def test_defaults_to_info_with_no_arg_or_env(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    configure_logging()
    assert logging.getLogger(_APP_LOGGER).level == logging.INFO


def test_explicit_level_argument_wins_over_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    configure_logging("DEBUG")
    assert logging.getLogger(_APP_LOGGER).level == logging.DEBUG


def test_reads_level_from_env_when_no_argument(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    configure_logging()
    assert logging.getLogger(_APP_LOGGER).level == logging.WARNING


def test_unknown_level_falls_back_to_info_rather_than_raising(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    configure_logging("NOT_A_LEVEL")  # a bad env var must never crash startup
    assert logging.getLogger(_APP_LOGGER).level == logging.INFO
