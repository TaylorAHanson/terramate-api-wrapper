"""Add stuck-surfacing columns to step (#43).

A merged Step with apply-derived Outputs is held at `applying` until its
GitHub Action writes those Outputs (architecture.md §14, ADR-0002); if the
Action never does, the Step sits there silently forever. `status_changed_at`
records when a Step entered its current status (needed because `updated_at`
isn't `onupdate`-maintained and `claimed_at` predates `applying`), so the
reconcile loop can tell how long it's been held; `stuck` is the persisted,
log-once flag the loop raises when that exceeds the threshold and clears on
the next transition, so a deployed operator can both query it and alert on it.

`status_changed_at` defaults to `now()` so existing rows get a sane baseline;
`stuck` defaults to false.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "step",
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "step",
        sa.Column("stuck", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("step", "stuck")
    op.drop_column("step", "status_changed_at")
