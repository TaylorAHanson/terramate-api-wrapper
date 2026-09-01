"""Add produces/consumes wiring columns to step.

A multi-Step Recipe's ordering-and-value-passing (#20) needs each Step's
`produces` (apply-derived output names it will emit) and `consumes`
(`OutputRef`s it needs resolved from an earlier Step) available at reconcile
time — not just `depends_on`. Both are nullable-free JSON with an empty-list
default so existing rows (single-Step Playbooks with neither) stay valid.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("step", sa.Column("produces", sa.JSON, nullable=False, server_default="[]"))
    op.add_column("step", sa.Column("consumes", sa.JSON, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("step", "consumes")
    op.drop_column("step", "produces")
