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
