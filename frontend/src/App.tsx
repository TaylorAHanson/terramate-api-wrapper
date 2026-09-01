import { useEffect, useState } from "react";
import { getBuildInfo, getRequest, getStepPlan, type BuildInfo, type RequestDetail, type Step } from "./api";

// The thin read-only status UI (architecture.md §11/§15.2): paste a
// request_id, see its status and Steps (including awaiting_approval), and
// read a Step's terraform plan once it has one (#19).
export default function App() {
  const [buildInfo, setBuildInfo] = useState<BuildInfo | null>(null);
  const [buildError, setBuildError] = useState<string | null>(null);

  useEffect(() => {
    getBuildInfo().then(setBuildInfo).catch((err: Error) => setBuildError(err.message));
  }, []);

  return (
    <main>
      <h1>Terramate Provisioning</h1>
      {buildError && <p role="alert">Could not reach the API: {buildError}</p>}
      {!buildError && !buildInfo && <p>Loading…</p>}
      {buildInfo && (
        <p>
          Connected to API version {buildInfo.version} ({buildInfo.git_sha})
        </p>
      )}
      <RequestLookup />
    </main>
  );
}

function RequestLookup() {
  const [requestId, setRequestId] = useState("");
  const [request, setRequest] = useState<RequestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = (id: string) => {
    setError(null);
    getRequest(id)
      .then(setRequest)
      .catch((err: Error) => {
        setRequest(null);
        setError(err.message);
      });
  };

  return (
    <section>
      <h2>Look up a request</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          load(requestId);
        }}
      >
        <label>
          Request ID{" "}
          <input
            name="requestId"
            value={requestId}
            onChange={(e) => setRequestId(e.target.value)}
            placeholder="request_id"
          />
        </label>
        <button type="submit">Load</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {request && <RequestView request={request} />}
    </section>
  );
}

function RequestView({ request }: { request: RequestDetail }) {
  return (
    <div>
      <p>
        <strong>{request.type}</strong> — status: <code>{request.status}</code>
      </p>
      <p>
        Requester: {request.requester} · Version: {request.version}
      </p>
      <table>
        <thead>
          <tr>
            <th>Ordinal</th>
            <th>Key</th>
            <th>Status</th>
            <th>PR</th>
            <th>Plan</th>
          </tr>
        </thead>
        <tbody>
          {request.steps.map((step) => (
            <StepRow key={step.ordinal} requestId={request.id} step={step} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StepRow({ requestId, step }: { requestId: string; step: Step }) {
  const [plan, setPlan] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);

  const loadPlan = () => {
    setPlanError(null);
    getStepPlan(requestId, step.ordinal)
      .then((result) => setPlan(result.plan))
      .catch((err: Error) => setPlanError(err.message));
  };

  return (
    <>
      <tr>
        <td>{step.ordinal}</td>
        <td>{step.key}</td>
        <td>{step.status}</td>
        <td>
          {step.pr_url ? (
            <a href={step.pr_url} target="_blank" rel="noreferrer">
              #{step.pr_number}
            </a>
          ) : (
            "—"
          )}
        </td>
        <td>
          <button type="button" onClick={loadPlan}>
            View plan
          </button>
          {planError && <span role="alert"> {planError}</span>}
        </td>
      </tr>
      {plan && (
        <tr>
          <td colSpan={5}>
            <pre>{plan}</pre>
          </td>
        </tr>
      )}
    </>
  );
}
