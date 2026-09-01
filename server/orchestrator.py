"""The reconcile loop (architecture.md §4.2, §5).

`tick()` is the orchestrator's synchronous entry point — the same one a timer
would call in production and a test calls directly for deterministic control
(architecture.md §14, "operator wants a synchronous advance/tick entry
point"). It is deliberately a no-op here: there are no Playbooks or Steps yet
for it to advance (Recipe engine and request intake land in #18, the state
machine walk in #19). This ticket only stands the seam up so those tickets
have a real signature to fill in rather than inventing one under pressure.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from server.github_client import GitHubClient


def tick(session: Session, github_client: GitHubClient) -> None:
    """Advance every in-flight Step by one reconciliation pass. No-op for now."""
    return None
