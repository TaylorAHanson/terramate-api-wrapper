"""Intake gate: the global off-switch (architecture.md §3.1, §10, #21).

A single seeded row (`id == 1`) rather than a generic settings table — the
API needs exactly one boolean today, and a generic key-value store would be
speculative (ADR-0001's "simple first"). `POST /v1/requests` checks
`enabled` before admitting new work; the reconcile loop never reads it, so
already-queued and already-open Steps keep draining while intake is closed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01

Renumbered from 0002 to 0003 during the feature/21 rebase onto main: #20's
migration (`0002_step_produces_consumes.py`) merged to main first and also
claimed revision "0002" (both branched off 0001 independently) — see the
terramate-api-wrapper-phase1 memory note on the collision.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "intake_gate",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("INSERT INTO intake_gate (id, enabled) VALUES (1, true)")


def downgrade() -> None:
    op.drop_table("intake_gate")
