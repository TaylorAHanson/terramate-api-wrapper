#!/usr/bin/env python3
"""Report this Step's terminal outcome to the provisioning API over HTTP
(ADR-0003/ADR-0004).

Supersedes the ADR-0002 direct-Lakebase write (`scripts/write_output.py`): the
API is the *sole* writer of Lakebase, so CI holds only a scoped API credential
(a Databricks service principal) — no DB credential, no knowledge of the schema.

Per **ADR-0004** the API opens the PR and then never polls GitHub — every Step
transition after that is *this push*. The outcome (`REPORT_STATUS`) is:

- `done` — the merged PR's `terraform apply` succeeded; this reports the
  apply-derived outputs (run by the `apply` job).
- `failed` — the apply ran and failed (also the `apply` job, via
  `steps.apply.outcome`).
- `rejected` — the PR was closed without merging (run by the on-close
  `rejected` job); no outputs, no apply console.

Auth (M2M): mint a Databricks workspace OAuth token via the service principal's
client-credentials grant, then call the deployed app. The app sits behind the
Databricks Apps OAuth proxy, which authenticates the token and stamps the SP's
identity onto `X-Forwarded-User` — exactly what `server.auth.require_ci_principal`
checks against `CI_PRINCIPALS`.

The endpoint is ordinal-scoped (`.../steps/{ordinal}/outputs`) but the branch
carries the step *key*, so this resolves key -> ordinal via a `GET` on the
request first.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

_BRANCH_PATTERN = re.compile(r"^provision/(?P<request_id>[^/]+)/(?P<step_key>[^/]+)$")
_VALID_STATUSES = {"done", "failed", "rejected"}


def _oauth_token(host: str, client_id: str, client_secret: str) -> str:
    """A workspace OAuth access token via the SP client-credentials grant."""
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        f"{host}/oidc/v1/token",
        data=b"grant_type=client_credentials&scope=all-apis",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def _api(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        # Surface the API's own error body (e.g. 403 not-authorized, 409
        # wrong-state) so a failed run is diagnosable from the Actions log.
        detail = e.read().decode(errors="replace")
        print(f"HTTP {e.code} from {method} {url}: {detail}", file=sys.stderr)
        raise


def _terraform_outputs() -> dict:
    raw = subprocess.run(
        ["terraform", "output", "-json"], capture_output=True, text=True, check=True
    ).stdout
    return {key: value["value"] for key, value in json.loads(raw).items()}


def main() -> int:
    status = os.environ.get("REPORT_STATUS", "done")
    if status not in _VALID_STATUSES:
        print(f"REPORT_STATUS must be one of {sorted(_VALID_STATUSES)}, got {status!r}.", file=sys.stderr)
        return 1

    branch = os.environ["HEAD_REF"]
    match = _BRANCH_PATTERN.match(branch)
    if match is None:
        print(f"'{branch}' isn't a provisioning-API branch (provision/<request_id>/<step_key>) — skipping.")
        return 0
    request_id, step_key = match["request_id"], match["step_key"]

    app_url = os.environ["APP_URL"].rstrip("/")
    token = _oauth_token(
        os.environ["DATABRICKS_HOST"].rstrip("/"),
        os.environ["DATABRICKS_CLIENT_ID"],
        os.environ["DATABRICKS_CLIENT_SECRET"],
    )

    # The branch names the Step by key; the endpoint is ordinal-scoped.
    detail = _api("GET", f"{app_url}/v1/requests/{request_id}", token)
    ordinal = next((s["ordinal"] for s in detail["steps"] if s["key"] == step_key), None)
    if ordinal is None:
        print(f"No step {step_key!r} in request {request_id!r}.", file=sys.stderr)
        return 1

    # Only a successful apply has outputs to report; `failed`/`rejected` carry
    # none (a rejected PR never applied at all).
    outputs = _terraform_outputs() if status == "done" else {}
    body = {
        "status": status,
        "outputs": outputs,
        "tf_console": os.environ.get("TF_CONSOLE", ""),
    }
    result = _api(
        "PUT", f"{app_url}/v1/requests/{request_id}/steps/{ordinal}/outputs", token, body
    )
    print(f"Reported {status!r} for step {step_key!r} (ordinal {ordinal}): {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
