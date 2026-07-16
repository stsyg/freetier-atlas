import { useCallback, useEffect, useState } from "react";
import { fetchApiHealth, type ApiHealth } from "./api";
import "./App.css";

type Status =
  | { kind: "loading" }
  | { kind: "ok"; health: ApiHealth }
  | { kind: "error"; message: string };

export default function App() {
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  const load = useCallback((signal?: AbortSignal) => {
    setStatus({ kind: "loading" });
    fetchApiHealth(signal)
      .then((health) => setStatus({ kind: "ok", health }))
      .catch((error: unknown) => {
        if (signal?.aborted) return;
        const message = error instanceof Error ? error.message : "Unknown error.";
        setStatus({ kind: "error", message });
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
    <main className="page">
      <header className="hero">
        <h1>FreeTier Atlas</h1>
        <p className="tagline">
          Evidence-backed catalogue and architecture adviser for cloud and developer service
          tiers.
        </p>
      </header>

      <section className="card" aria-labelledby="api-status-heading">
        <h2 id="api-status-heading">API status</h2>
        <ApiStatusPanel status={status} onRetry={() => load()} />
      </section>

      <footer className="footer">
        <p>Application scaffold — F002. Catalogue and adviser features arrive in later increments.</p>
      </footer>
    </main>
  );
}

function ApiStatusPanel({ status, onRetry }: { status: Status; onRetry: () => void }) {
  if (status.kind === "loading") {
    return (
      <p className="status status--loading" role="status">
        Checking API health…
      </p>
    );
  }

  if (status.kind === "error") {
    return (
      <div className="status status--error" role="alert">
        <p>API unavailable: {status.message}</p>
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      </div>
    );
  }

  const { health } = status;
  return (
    <div className="status status--ok" role="status">
      <p>
        <span className="dot" aria-hidden="true" /> API is <strong>{health.status}</strong>
      </p>
      <dl className="details">
        <div>
          <dt>Service</dt>
          <dd>{health.service}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd>{health.version}</dd>
        </div>
        <div>
          <dt>Environment</dt>
          <dd>{health.environment}</dd>
        </div>
      </dl>
    </div>
  );
}
