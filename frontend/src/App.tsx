import { Fragment, useCallback, useEffect, useState, type CSSProperties } from "react";
import {
  cancelAllRequests,
  cancelRequest,
  getBuildInfo,
  getIntakeGate,
  listRequests,
  setIntakeGate,
  type BuildInfo,
  type CancelledRequest,
  type IntakeGate,
  type RequestDetail,
  type Step,
} from "./api";

// The status + operator-controls UI (architecture.md §11/§15.2): every pending
// request with its Steps and PRs, per-request and bulk cancel, and the global
// intake off-switch (#21). Per ADR-0004 the API no longer surfaces a Step's
// terraform plan — reviewers read it on the PR itself.
const REFRESH_MS = 5000;
const TERMINAL_REQUEST_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

export default function App() {
  const [buildInfo, setBuildInfo] = useState<BuildInfo | null>(null);
  const [buildError, setBuildError] = useState<string | null>(null);

  useEffect(() => {
    getBuildInfo().then(setBuildInfo).catch((err: Error) => setBuildError(err.message));
  }, []);

  return (
    <main style={styles.main}>
      <h1>Terramate Provisioning</h1>
      {buildError && <p role="alert">Could not reach the API: {buildError}</p>}
      {buildInfo && (
        <p style={styles.muted}>
          API version {buildInfo.version} ({buildInfo.git_sha})
        </p>
      )}
      <IntakeGateControl />
      <Requests />
    </main>
  );
}

function IntakeGateControl() {
  const [gate, setGate] = useState<IntakeGate | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getIntakeGate()
      .then(setGate)
      .catch((err: Error) => setError(err.message));
  }, []);

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
          New requests are currently <strong>{gate.enabled ? "open" : "closed"}</strong>{" "}
          <button type="button" onClick={toggle}>
            {gate.enabled ? "Close intake" : "Open intake"}
          </button>
        </p>
      )}
    </section>
  );
}

function Requests() {
  const [requests, setRequests] = useState<RequestDetail[] | null>(null);
  const [includeFinished, setIncludeFinished] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastCancelAll, setLastCancelAll] = useState<CancelledRequest[] | null>(null);

  const load = useCallback(() => {
    listRequests(includeFinished)
      .then((rows) => {
        setRequests(rows);
        setError(null);
      })
      .catch((err: Error) => setError(err.message));
  }, [includeFinished]);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const cancelAll = () => {
    if (!window.confirm("Cancel every pending request? Open PRs on GitHub stay open.")) return;
    cancelAllRequests()
      .then((cancelled) => {
        setLastCancelAll(cancelled);
        load();
      })
      .catch((err: Error) => setError(err.message));
  };

  const pendingCount = requests?.filter((r) => !TERMINAL_REQUEST_STATUSES.has(r.status)).length ?? 0;

  return (
    <section>
      <h2>Requests</h2>
      <p style={styles.toolbar}>
        <label>
          <input
            type="checkbox"
            checked={includeFinished}
            onChange={(e) => setIncludeFinished(e.target.checked)}
          />{" "}
          Show finished
        </label>
        <button type="button" onClick={load}>
          Refresh
        </button>
        <button type="button" onClick={cancelAll} disabled={pendingCount === 0}>
          Cancel all pending ({pendingCount})
        </button>
        <span style={styles.muted}>Auto-refreshes every {REFRESH_MS / 1000}s</span>
      </p>
      {error && <p role="alert">{error}</p>}
      {lastCancelAll && <CancelAllResult cancelled={lastCancelAll} onDismiss={() => setLastCancelAll(null)} />}
      {requests && requests.length === 0 && <p>No {includeFinished ? "" : "pending "}requests.</p>}
      {requests && requests.length > 0 && (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Created</th>
              <th style={styles.th}>Type</th>
              <th style={styles.th}>Status</th>
              <th style={styles.th}>Requester</th>
              <th style={styles.th}>Steps</th>
              <th style={styles.th}>Request ID</th>
              <th style={styles.th} />
            </tr>
          </thead>
          <tbody>
            {requests.map((request) => (
              <RequestRow key={request.id} request={request} onChanged={load} />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function CancelAllResult({ cancelled, onDismiss }: { cancelled: CancelledRequest[]; onDismiss: () => void }) {
  const openPrs = cancelled.flatMap((c) => c.open_pr_urls);
  return (
    <div style={styles.notice}>
      Cancelled {cancelled.length} request{cancelled.length === 1 ? "" : "s"}.
      {openPrs.length > 0 && (
        <>
          {" "}
          These PRs are still open on GitHub. Close them so nobody merges one by mistake:
          <ul>
            {openPrs.map((url) => (
              <li key={url}>
                <a href={url} target="_blank" rel="noreferrer">
                  {url}
                </a>
              </li>
            ))}
          </ul>
        </>
      )}{" "}
      <button type="button" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}

function RequestRow({ request, onChanged }: { request: RequestDetail; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancellable = !TERMINAL_REQUEST_STATUSES.has(request.status);
  const stuck = request.steps.some((s) => s.stuck);

  const cancel = () => {
    if (!window.confirm(`Cancel ${request.type} request ${request.id}?`)) return;
    setError(null);
    cancelRequest(request.id).then(onChanged).catch((err: Error) => setError(err.message));
  };

  return (
    <Fragment>
      <tr>
        <td style={styles.td}>{new Date(request.created_at).toLocaleString()}</td>
        <td style={styles.td}>
          <strong>{request.type}</strong>
        </td>
        <td style={styles.td}>
          <code>{request.status}</code>
          {stuck && <span title="A Step is held at submitted past the stuck threshold"> ⚠ stuck</span>}
        </td>
        <td style={styles.td}>{request.requester}</td>
        <td style={styles.td}>
          {request.steps.map((step) => (
            <StepBadge key={step.ordinal} step={step} />
          ))}
        </td>
        <td style={styles.td}>
          <code style={styles.small}>{request.id}</code>
        </td>
        <td style={styles.td}>
          <button type="button" onClick={() => setExpanded(!expanded)}>
            {expanded ? "Hide" : "Details"}
          </button>{" "}
          {cancellable && (
            <button type="button" onClick={cancel}>
              Cancel
            </button>
          )}
          {error && <div role="alert">{error}</div>}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td style={styles.td} colSpan={7}>
            <strong>Params</strong>
            <pre style={styles.pre}>{JSON.stringify(request.params, null, 2)}</pre>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function StepBadge({ step }: { step: Step }) {
  const label = `${step.key}: ${step.status}${step.stuck ? " ⚠" : ""}`;
  return (
    <div>
      {step.pr_url ? (
        <a href={step.pr_url} target="_blank" rel="noreferrer">
          {label} (#{step.pr_number})
        </a>
      ) : (
        label
      )}
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  main: { fontFamily: "system-ui, sans-serif", margin: "1.5rem", lineHeight: 1.4 },
  muted: { color: "#666" },
  toolbar: { display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" },
  table: { borderCollapse: "collapse", width: "100%" },
  th: { textAlign: "left", borderBottom: "2px solid #ccc", padding: "0.4rem" },
  td: { borderBottom: "1px solid #eee", padding: "0.4rem", verticalAlign: "top" },
  small: { fontSize: "0.8em" },
  pre: { background: "#f6f6f6", padding: "0.5rem", overflowX: "auto" },
  notice: { background: "#fff8e1", border: "1px solid #f0d58c", padding: "0.75rem", marginBottom: "1rem" },
};
