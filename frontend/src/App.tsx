import { useEffect, useState } from "react";
import {
  cancelRequest,
  getBuildInfo,
  getIntakeGate,
  getRequest,
  getStepPlan,
  setIntakeGate,
  type BuildInfo,
  type IntakeGate,
  type RequestDetail,
  type Step,
} from "./api";

// The status + operator-controls UI (architecture.md §11/§15.2): paste a
// request_id, see its status and Steps (including awaiting_approval), read a
// Step's terraform plan once it has one (#19), cancel an in-flight request,
// and flip the global intake off-switch (#21).
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
      <IntakeGateControl />
      <RequestLookup />
    </main>
  );
}

const TERMINAL_REQUEST_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

function IntakeGateControl() {
  const [gate, setGate] = useState<IntakeGate | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    getIntakeGate()
      .then(setGate)
      .catch((err: Error) => setError(err.message));
  };

  useEffect(load, []);

  const toggle = () => {
    if (!gate) return;
    setError(null);
    setIntakeGate(!gate.enabled)
      .then(setGate)
      .catch((err: Error) => setError(err.message));
  };

  return (
    <section>
      <h2>Intake</h2>
      {error && <p role="alert">{error}</p>}
      {gate && (
        <p>
          New requests are currently <strong>{gate.enabled ? "open" : "closed"}</strong>
          {" — "}
          <button type="button" onClick={toggle}>
            {gate.enabled ? "Close intake" : "Open intake"}
          </button>
        </p>
      )}
    </section>
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
      {request && <RequestView request={request} onChanged={() => load(request.id)} />}
    </section>
  );
}

function RequestView({ request, onChanged }: { request: RequestDetail; onChanged: () => void }) {
  const [cancelError, setCancelError] = useState<string | null>(null);
  const cancellable = !TERMINAL_REQUEST_STATUSES.has(request.status);

  const cancel = () => {
    setCancelError(null);
    cancelRequest(request.id).then(onChanged).catch((err: Error) => setCancelError(err.message));
  };

  return (
    <div>
      <p>
        <strong>{request.type}</strong> — status: <code>{request.status}</code>
        {cancellable && (
          <>
            {" "}
            <button type="button" onClick={cancel}>
              Cancel request
            </button>
          </>
        )}
      </p>
      {cancelError && <p role="alert">{cancelError}</p>}
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
        <td>
          {step.status}
          {step.stuck && (
            <span role="alert" title="Held at applying past the threshold — the apply Action never wrote its outputs (#43)">
              {" "}
              ⚠ stuck
            </span>
          )}
        </td>
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
