"""Add tf_console storage to step (#54, ADR-0003).

ADR-0003 pivots output-capture to a GitHub Action -> API PUT (superseding
ADR-0002's direct Lakebase write) and adds a requirement the direct-write
path never carried: persisting the apply run's **console text** for status /
agent-reasoning (architecture.md "Persist Console Output"). There's exactly
one console per Step's apply attempt, so this is a column on `step` rather
than a new table — no history/versioning is needed here, unlike `output`
which is keyed and unique per `(step_id, key)` because a Step can produce
several named outputs.

Nullable: a Step hasn't reported an apply result yet for most of its
lifecycle (queued/pr_open/awaiting_approval/applying), and existing rows
have none.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("step", sa.Column("tf_console", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("step", "tf_console")
