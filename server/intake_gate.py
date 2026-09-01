"""The global intake off-switch (architecture.md §3.1, §10, #21).

A single durable row (`intake_gate.id == 1`, seeded by migration 0002) that
`POST /v1/requests` checks before admitting new work. The reconcile loop
never reads this — closing the gate stops new intake but lets already-queued
and already-open Steps keep draining to a terminal state on their own.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from server.models import IntakeGate

GATE_ID = 1


def get_gate(session: Session) -> IntakeGate:
    return session.get(IntakeGate, GATE_ID)
