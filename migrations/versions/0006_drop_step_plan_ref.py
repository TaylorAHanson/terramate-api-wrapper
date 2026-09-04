"""Drop step.plan_ref (ADR-0004).

ADR-0004 makes the API stop polling GitHub for a Step's plan: the plan is a
GitHub-side human concern (a reviewer reads it on the PR), so the API no
longer fetches it, stores it, or surfaces it (`get_plan`, the `/plan` route,
and the `pr_open -> awaiting_approval` machinery all go away). `step.plan_ref`
was the captured `terraform plan` text; nothing writes or reads it any more,
so the column is dropped.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("step", "plan_ref")


def downgrade() -> None:
    op.add_column("step", sa.Column("plan_ref", sa.String(1024), nullable=True))
