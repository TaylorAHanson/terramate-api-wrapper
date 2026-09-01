"""Initial schema: provisioning_request, step, output.

The durable core (architecture.md §9): a ProvisioningRequest expands into an
ordered set of Steps, each Step's applied outputs are captured by row, and the
work queue is expressed directly on `step.status` + a claim column rather than
a separate table (`SELECT ... FOR UPDATE SKIP LOCKED`, architecture.md §4.2).

Two columns are reserved, unused seams rather than guesses at unsettled
design: `provisioning_request.asset_id` (the future UUIDv5 business identity,
#12) and `step.lock_token` (the future per-Step locking model, #10). Both are
nullable so today's rows are valid and those features land later as no-ops,
not migrations.

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provisioning_request",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("type", sa.String(255), nullable=False),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("requester", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        # Reserved seam, #12 — the future UUIDv5 business asset identity.
        sa.Column("asset_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "step",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(36),
            sa.ForeignKey("provisioning_request.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("pr_number", sa.Integer, nullable=True),
        sa.Column("pr_url", sa.String(1024), nullable=True),
        sa.Column("plan_ref", sa.String(1024), nullable=True),
        sa.Column("depends_on", sa.JSON, nullable=False, server_default="[]"),
        # Queue claim columns — a worker claims a runnable Step with
        # `SELECT ... FOR UPDATE SKIP LOCKED` before opening its PR.
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        # Reserved seam, #10 — the future per-Step locking model.
        sa.Column("lock_token", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("request_id", "ordinal", name="uq_step_request_ordinal"),
    )

    op.create_table(
        "output",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("step.id"), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("step_id", "key", name="uq_output_step_key"),
    )


def downgrade() -> None:
    op.drop_table("output")
    op.drop_table("step")
    op.drop_table("provisioning_request")
