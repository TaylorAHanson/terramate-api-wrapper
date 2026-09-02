"""SQLAlchemy ORM models for the durable domain (architecture.md §9).

Mirrors `migrations/versions/0001_initial_schema.py` exactly — the migration
is authoritative for the database DDL; this module is the ORM mapping the
rest of the app reads and writes through.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, JSON, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _server_now():
    """`server_default` mirroring the migration's DDL (doesn't redefine the
    schema — Alembic already created these columns with this same default);
    tells the ORM to omit created_at/updated_at from INSERTs and let Postgres
    fill them in, instead of sending an explicit NULL.
    """
    return mapped_column(server_default=func.now())


class Base(DeclarativeBase):
    pass


class ProvisioningRequest(Base):
    __tablename__ = "provisioning_request"

    id: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[str]
    params: Mapped[dict] = mapped_column(JSON)
    version: Mapped[str]
    requester: Mapped[str]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str]
    # Reserved seam, #12 — the future UUIDv5 business asset identity.
    asset_id: Mapped[str | None]
    created_at: Mapped[datetime] = _server_now()
    updated_at: Mapped[datetime] = _server_now()

    steps: Mapped[list["Step"]] = relationship(back_populates="request", order_by="Step.ordinal")


class Step(Base):
    __tablename__ = "step"
    __table_args__ = (UniqueConstraint("request_id", "ordinal", name="uq_step_request_ordinal"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("provisioning_request.id"))
    ordinal: Mapped[int]
    key: Mapped[str]
    status: Mapped[str]
    pr_number: Mapped[int | None]
    pr_url: Mapped[str | None]
    plan_ref: Mapped[str | None]
    depends_on: Mapped[list[str]] = mapped_column(JSON)
    # Wiring for #20's ordering-and-value-passing: `produces` is the apply-derived
    # output names this Step will emit; `consumes` is the OutputRef ({step_key,
    # output_name}) list this Step's bundle needs resolved before its PR opens.
    produces: Mapped[list[str]] = mapped_column(JSON)
    consumes: Mapped[list[dict]] = mapped_column(JSON)
    claimed_at: Mapped[datetime | None]
    claimed_by: Mapped[str | None]
    # Reserved seam, #10 — the future per-Step locking model.
    lock_token: Mapped[str | None]
    # Stuck-surfacing (#43): `status_changed_at` is when this Step entered its
    # current status (updated on every transition — `updated_at` isn't
    # `onupdate`-maintained), and `stuck` is the log-once flag the reconcile
    # loop raises when a Step is held at `applying` past the threshold and
    # clears on the next transition.
    status_changed_at: Mapped[datetime] = _server_now()
    stuck: Mapped[bool] = mapped_column(server_default=func.false())
    created_at: Mapped[datetime] = _server_now()
    updated_at: Mapped[datetime] = _server_now()

    request: Mapped["ProvisioningRequest"] = relationship(back_populates="steps")


class IntakeGate(Base):
    """The global off-switch (#21) — a single seeded row, `id == 1`."""

    __tablename__ = "intake_gate"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool]
    updated_at: Mapped[datetime] = _server_now()


class Output(Base):
    __tablename__ = "output"
    __table_args__ = (UniqueConstraint("step_id", "key", name="uq_output_step_key"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("step.id"))
    key: Mapped[str]
    # Any JSON-serializable apply-derived value (architecture.md §9's example
    # is a bare terraform-emitted id) — not necessarily an object.
    value: Mapped[Any] = mapped_column(JSON)
    captured_at: Mapped[datetime] = _server_now()
