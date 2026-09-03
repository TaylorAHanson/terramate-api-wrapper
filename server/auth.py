"""Trusted-identity resolution + admin authorization (#47, architecture.md §10).

The Databricks Apps platform proxy authenticates every inbound call itself
and stamps the resolved caller identity onto the request as
`X-Forwarded-Email` / `X-Forwarded-User` before it ever reaches this
process — overwriting any such header an external caller tried to set. That
makes these headers trustworthy in a way a plain client-supplied header
(the old `X-Requester`) never was: nothing this process does can be spoofed
by a caller setting an arbitrary header, because the platform's own proxy
owns them.

`X-Forwarded-Email` carries a human end-user's email; `X-Forwarded-User` is
present for both human and service-principal callers (the SP's id) and is
the fallback when no email is forwarded (the expected shape for the
self-service app's M2M service-principal calls, architecture.md §10).
"""
from __future__ import annotations

import logging

from fastapi import Header, HTTPException

from server.config import get_settings

logger = logging.getLogger(__name__)


def _forwarded_identity(x_forwarded_email: str | None, x_forwarded_user: str | None) -> str | None:
    return x_forwarded_email or x_forwarded_user or None


def resolve_requester(
    x_forwarded_email: str | None = Header(default=None, alias="X-Forwarded-Email"),
    x_forwarded_user: str | None = Header(default=None, alias="X-Forwarded-User"),
) -> str:
    """The trusted caller identity recorded as a ProvisioningRequest's
    `requester` (architecture.md §10, §9). A request the platform forwarded
    no identity for is rejected rather than silently attributed.
    """
    identity = _forwarded_identity(x_forwarded_email, x_forwarded_user)
    if identity is None:
        logger.warning("auth_rejected reason=no_forwarded_identity")
        raise HTTPException(status_code=401, detail="No resolvable caller identity")
    logger.info("auth_accepted identity=%s", identity)
    return identity


def require_admin(
    x_forwarded_email: str | None = Header(default=None, alias="X-Forwarded-Email"),
    x_forwarded_user: str | None = Header(default=None, alias="X-Forwarded-User"),
) -> str:
    """Gate for the intake off-switch (#47): the caller must both resolve to
    a trusted forwarded identity and appear on the `ADMIN_PRINCIPALS`
    allowlist — no resolvable identity is `401`, resolved-but-not-allowed is
    `403`.
    """
    identity = _forwarded_identity(x_forwarded_email, x_forwarded_user)
    if identity is None:
        logger.warning("admin_auth_rejected reason=no_forwarded_identity")
        raise HTTPException(status_code=401, detail="No resolvable caller identity")
    if identity not in get_settings().admin_principals:
        logger.warning("admin_auth_rejected reason=not_authorized identity=%s", identity)
        raise HTTPException(status_code=403, detail="Not authorized")
    logger.info("admin_auth_accepted identity=%s", identity)
    return identity


def require_ci_principal(
    x_forwarded_email: str | None = Header(default=None, alias="X-Forwarded-Email"),
    x_forwarded_user: str | None = Header(default=None, alias="X-Forwarded-User"),
) -> str:
    """Gate for the ADR-0003 output-report ingress (`PUT .../steps/{n}/outputs`,
    #55): the caller must resolve to a trusted forwarded identity (same
    platform-stamped-header trust model as `resolve_requester`/`require_admin`
    — never a client-controlled header) that also appears on the
    `CI_PRINCIPALS` allowlist, the CI M2M service principal a Step's GitHub
    Action authenticates as. No resolvable identity is `401`,
    resolved-but-not-allowed is `403` — either way, no state changes.
    """
    identity = _forwarded_identity(x_forwarded_email, x_forwarded_user)
    if identity is None:
        logger.warning("ci_auth_rejected reason=no_forwarded_identity")
        raise HTTPException(status_code=401, detail="No resolvable caller identity")
    if identity not in get_settings().ci_principals:
        logger.warning("ci_auth_rejected reason=not_authorized identity=%s", identity)
        raise HTTPException(status_code=403, detail="Not authorized")
    logger.info("ci_auth_accepted identity=%s", identity)
    return identity
