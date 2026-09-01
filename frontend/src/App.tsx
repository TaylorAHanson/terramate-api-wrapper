import { useEffect, useState } from "react";
import { getBuildInfo, type BuildInfo } from "./api";

// The thin read-only status UI (architecture.md §11/§15.2). This ticket only
// proves the shell loads and can call the API; submitting/watching a request
// lands once #18/#19 give it something real to show.
export default function App() {
  const [buildInfo, setBuildInfo] = useState<BuildInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBuildInfo().then(setBuildInfo).catch((err: Error) => setError(err.message));
  }, []);

  return (
    <main>
      <h1>Terramate Provisioning</h1>
      {error && <p role="alert">Could not reach the API: {error}</p>}
      {!error && !buildInfo && <p>Loading…</p>}
      {buildInfo && (
        <p>
          Connected to API version {buildInfo.version} ({buildInfo.git_sha})
        </p>
      )}
    </main>
  );
}
