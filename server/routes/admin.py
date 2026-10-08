"""Operator controls, all gated on a trusted forwarded identity on the
`ADMIN_PRINCIPALS` allowlist (server.auth, #47):

- `GET`/`POST /v1/admin/intake-gate` — the global off-switch (architecture.md
  §3.1, §10, #21).
- `POST /v1/admin/requests/cancel-all` — cancel every request that hasn't
  reached a terminal state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.auth import require_admin
from server.database import get_db
from server.intake_gate import get_gate
from server.models import ProvisioningRequest
from server.orchestrator import TERMINAL_REQUEST_STATUSES

logger = logging.getLogger(__name__)

router = APIRouter()


class IntakeGateResponse(BaseModel):
    enabled: bool
    updated_at: datetime


class SetIntakeGateRequest(BaseModel):
    enabled: bool


@router.get("/v1/admin/intake-gate", response_model=IntakeGateResponse)
def read_intake_gate(
    session: Session = Depends(get_db), principal: str = Depends(require_admin)
) -> IntakeGateResponse:
    gate = get_gate(session)
    return IntakeGateResponse(enabled=gate.enabled, updated_at=gate.updated_at)


@router.post("/v1/admin/intake-gate", response_model=IntakeGateResponse)
def set_intake_gate(
    body: SetIntakeGateRequest,
    session: Session = Depends(get_db),
    principal: str = Depends(require_admin),
) -> IntakeGateResponse:
    gate = get_gate(session)
    previous = gate.enabled
    gate.enabled = body.enabled
    gate.updated_at = datetime.now(timezone.utc)
    session.commit()
    logger.info("intake_gate_set from=%s to=%s by=%s", previous, gate.enabled, principal)
    return IntakeGateResponse(enabled=gate.enabled, updated_at=gate.updated_at)


class CancelledRequest(BaseModel):
    request_id: str
    type: str
    previous_status: str
    # PRs this request still has open on GitHub. Cancelling doesn't close
    # them; close them there so nobody merges (and applies) one by mistake.
    open_pr_urls: list[str]


class CancelAllRequestsResponse(BaseModel):
    cancelled: list[CancelledRequest]


@router.post("/v1/admin/requests/cancel-all", response_model=CancelAllRequestsResponse)
def cancel_all_requests(
    session: Session = Depends(get_db), principal: str = Depends(require_admin)
) -> CancelAllRequestsResponse:
    """Cancel every request not already in a terminal state — the bulk form of
    `POST /v1/requests/{id}/cancel`, with the same semantics: applied Steps
    stay applied, nothing on GitHub is touched, and the reconcile loop stops
    claiming or advancing these requests. Requests that already succeeded,
    failed, or were cancelled are left as they are, so calling it twice is a
    no-op the second time.
    """
    live = session.scalars(
        select(ProvisioningRequest)
        .where(ProvisioningRequest.status.not_in(TERMINAL_REQUEST_STATUSES))
        .order_by(ProvisioningRequest.created_at)
    ).all()
    cancelled = []
    for request_row in live:
        cancelled.append(
            CancelledRequest(
                request_id=request_row.id,
                type=request_row.type,
                previous_status=request_row.status,
                open_pr_urls=[s.pr_url for s in request_row.steps if s.status == "submitted" and s.pr_url],
            )
        )
        request_row.status = "cancelled"
    session.commit()
    for entry in cancelled:
        logger.info(
            "request_cancelled request_id=%s from=%s by=%s bulk=true",
            entry.request_id,
            entry.previous_status,
            principal,
        )
    logger.info("requests_cancel_all count=%s by=%s", len(cancelled), principal)
    return CancelAllRequestsResponse(cancelled=cancelled)
