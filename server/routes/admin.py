"""`GET`/`POST /v1/admin/intake-gate` — the global off-switch (architecture.md
§3.1, §10, #21). No admin auth model exists yet (caller-level authorization
is fog for v1, architecture.md §10); this is the observability + control seam
the off-switch decision calls for.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.database import get_db
from server.intake_gate import get_gate

router = APIRouter()


class IntakeGateResponse(BaseModel):
    enabled: bool
    updated_at: datetime


class SetIntakeGateRequest(BaseModel):
    enabled: bool


@router.get("/v1/admin/intake-gate", response_model=IntakeGateResponse)
def read_intake_gate(session: Session = Depends(get_db)) -> IntakeGateResponse:
    gate = get_gate(session)
    return IntakeGateResponse(enabled=gate.enabled, updated_at=gate.updated_at)


@router.post("/v1/admin/intake-gate", response_model=IntakeGateResponse)
def set_intake_gate(
    body: SetIntakeGateRequest, session: Session = Depends(get_db)
) -> IntakeGateResponse:
    gate = get_gate(session)
    gate.enabled = body.enabled
    gate.updated_at = datetime.now(timezone.utc)
    session.commit()
    return IntakeGateResponse(enabled=gate.enabled, updated_at=gate.updated_at)
