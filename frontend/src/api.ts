export interface BuildInfo {
  version: string;
  git_sha: string;
  build_time: string;
}

export async function getBuildInfo(): Promise<BuildInfo> {
  const response = await fetch("/version");
  if (!response.ok) {
    throw new Error(`GET /version failed: ${response.status}`);
  }
  return response.json();
}

export interface Step {
  ordinal: number;
  key: string;
  status: string;
  pr_number: number | null;
  pr_url: string | null;
  plan_ref: string | null;
  depends_on: string[];
  // #43: true while held at `applying` past the stuck threshold (its Action
  // never wrote the ADR-0002 outputs); status_changed_at is when it entered
  // its current status.
  stuck: boolean;
  status_changed_at: string;
}

export interface RequestDetail {
  id: string;
  type: string;
  params: Record<string, unknown>;
  version: string;
  requester: string;
  status: string;
  created_at: string;
  updated_at: string;
  steps: Step[];
}

export async function getRequest(requestId: string): Promise<RequestDetail> {
  const response = await fetch(`/v1/requests/${requestId}`);
  if (!response.ok) {
    throw new Error(`GET /v1/requests/${requestId} failed: ${response.status}`);
  }
  return response.json();
}

export interface StepPlan {
  ordinal: number;
  key: string;
  status: string;
  plan: string;
}

export async function getStepPlan(requestId: string, ordinal: number): Promise<StepPlan> {
  const response = await fetch(`/v1/requests/${requestId}/steps/${ordinal}/plan`);
  if (response.status === 409) {
    throw new Error("Plan not available yet");
  }
  if (!response.ok) {
    throw new Error(`GET .../steps/${ordinal}/plan failed: ${response.status}`);
  }
  return response.json();
}

export interface CancelResult {
  request_id: string;
  status: string;
}

export async function cancelRequest(requestId: string): Promise<CancelResult> {
  const response = await fetch(`/v1/requests/${requestId}/cancel`, { method: "POST" });
  if (response.status === 409) {
    throw new Error("Request already reached a terminal state");
  }
  if (!response.ok) {
    throw new Error(`POST /v1/requests/${requestId}/cancel failed: ${response.status}`);
  }
  return response.json();
}

export interface IntakeGate {
  enabled: boolean;
  updated_at: string;
}

export async function getIntakeGate(): Promise<IntakeGate> {
  const response = await fetch("/v1/admin/intake-gate");
  if (!response.ok) {
    throw new Error(`GET /v1/admin/intake-gate failed: ${response.status}`);
  }
  return response.json();
}

export async function setIntakeGate(enabled: boolean): Promise<IntakeGate> {
  const response = await fetch("/v1/admin/intake-gate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error(`POST /v1/admin/intake-gate failed: ${response.status}`);
  }
  return response.json();
}
