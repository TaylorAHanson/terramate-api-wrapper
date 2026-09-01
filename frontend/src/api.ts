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
