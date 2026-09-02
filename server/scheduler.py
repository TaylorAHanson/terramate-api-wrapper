"""The reconcile-loop driver — the engine's motor (architecture.md §4.2, §8, #39).

`server.orchestrator.tick()` is a *synchronous* entry point: production and
tests call the same function, so the engine's behaviour is driven
deterministically in tests (architecture.md §14). This module is what calls it
on an interval in the deployed app, so queued and in-flight Steps advance with
no manual trigger.

Databricks Apps expose a single port and run this app as one `uvicorn` process
(see databricks.yml), so the driver lives *inside* that process, started and
stopped by the FastAPI lifespan (server.main). `tick()` blocks on the database
and on synchronous GitHub HTTP calls (bounded retries aside, `get_plan` itself
never blocks waiting on GitHub — #45), so each tick is still offloaded to a
worker thread (`asyncio.to_thread`) rather than run inline, so it never stalls
uvicorn's event loop.

Two properties make the loop safe to just keep running:

- **Crash-safe per tick.** A tick that raises is logged and swallowed; the loop
  runs again next interval. `tick()` reconciles from stored state every pass, so
  a missed or failed tick is simply recovered on the next one.
- **Replica-safe.** No new locking here — `tick()` already claims Steps with
  `SELECT ... FOR UPDATE SKIP LOCKED`, so two drivers (or two replicas) never
  double-open a Step's PR.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from sqlalchemy.orm import Session

from server import orchestrator
from server.database import get_session
from server.github_client import GitHubClient

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


def run_tick_once(github_client: GitHubClient, session_factory: SessionFactory = get_session) -> None:
    """Run exactly one reconcile pass, in its own session, never raising.

    This is the unit the loop repeats and the seam a test drives. Any error is
    logged and swallowed so one bad tick can't kill the loop (the next tick
    reconciles from stored state regardless).
    """
    session = session_factory()
    try:
        orchestrator.tick(session, github_client)
    except Exception:  # noqa: BLE001 — a driver tick must never propagate and kill the loop
        logger.exception("reconcile tick failed; will retry on the next interval")
        session.rollback()
    finally:
        session.close()


async def reconcile_loop(
    *,
    github_client: GitHubClient,
    interval_seconds: float,
    session_factory: SessionFactory = get_session,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Call `run_tick_once` every `interval_seconds` until cancelled or stopped.

    Runs each (blocking) tick in a worker thread so the event loop stays free.
    Sleeps interruptibly between ticks: setting `stop_event` (or cancelling the
    task) ends the loop promptly rather than after the full interval.
    """
    stop_event = stop_event or asyncio.Event()
    logger.info("reconcile driver started (interval=%.1fs)", interval_seconds)
    try:
        while not stop_event.is_set():
            await asyncio.to_thread(run_tick_once, github_client, session_factory)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass  # the interval elapsed with no stop requested — tick again
    except asyncio.CancelledError:
        logger.info("reconcile driver cancelled")
        raise
    finally:
        logger.info("reconcile driver stopped")
