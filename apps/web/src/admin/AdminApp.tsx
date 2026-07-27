/**
 * The private admin surface (F007 slice 4).
 *
 * Rendered only for the `/admin` route (see `main.tsx`). On mount it probes
 * `GET /api/admin/session`: an authenticated, allowlisted admin gets the four
 * admin panels (AI kill switch, review/contradiction queue, source health,
 * validated config-diff); anyone else sees a login gate that starts the GitHub
 * OAuth flow. All mutating calls carry the in-memory CSRF token returned by the
 * session probe. The component holds no secret — the session cookie is HttpOnly.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AdminUnauthenticated,
  actOnReviewItem,
  fetchKillSwitch,
  fetchReviewQueue,
  fetchSession,
  fetchSourceHealth,
  loginUrl,
  logout,
  postConfigDiff,
  setKillSwitch,
  type AdminSession,
  type ConfigDiffResponse,
  type KillSwitchState,
  type ReviewQueueResponse,
  type SourceHealthResponse,
} from "./api";

function ErrorNotice({ message }: { message: string }) {
  return (
    <p role="alert" className="admin-error">
      {message}
    </p>
  );
}

function KillSwitchPanel({ csrfToken }: { csrfToken: string }) {
  const [state, setState] = useState<KillSwitchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setState(await fetchKillSwitch());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the kill switch.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async () => {
    if (!state) return;
    setBusy(true);
    setError(null);
    try {
      const next = await setKillSwitch(!state.enabled, csrfToken);
      setState({ ...state, enabled: next.enabled, effective: next.enabled || state.env_override });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to toggle the kill switch.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="killswitch-heading" className="admin-panel">
      <h2 id="killswitch-heading">AI kill switch</h2>
      {error && <ErrorNotice message={error} />}
      {state ? (
        <>
          <p>
            Persisted flag: <strong>{state.enabled ? "ON" : "OFF"}</strong>
            {state.env_override && " · forced ON by environment override"} · effective:{" "}
            <strong>{state.effective ? "ON" : "OFF"}</strong>
          </p>
          <button type="button" onClick={() => void toggle()} disabled={busy}>
            {state.enabled ? "Disable" : "Enable"} kill switch
          </button>
        </>
      ) : (
        <p>Loading…</p>
      )}
    </section>
  );
}

function ReviewQueuePanel({ csrfToken }: { csrfToken: string }) {
  const [data, setData] = useState<ReviewQueueResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await fetchReviewQueue());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the review queue.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (id: number, disposition: string) => {
    setBusyId(id);
    setError(null);
    try {
      await actOnReviewItem(id, disposition, csrfToken);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update the item.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section aria-labelledby="queue-heading" className="admin-panel">
      <h2 id="queue-heading">Review / contradiction queue</h2>
      {error && <ErrorNotice message={error} />}
      {data === null ? (
        <p>Loading…</p>
      ) : data.items.length === 0 ? (
        <p>No items need review.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Reason</th>
              <th scope="col">Recommended</th>
              <th scope="col">Disposition</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.id}>
                <td>{item.id}</td>
                <td>{item.reason}</td>
                <td>{item.recommended_action ?? "—"}</td>
                <td>{item.admin_disposition}</td>
                <td>
                  {data.valid_actions.map((action) => (
                    <button
                      key={action}
                      type="button"
                      onClick={() => void act(item.id, action)}
                      disabled={busyId === item.id}
                    >
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function SourceHealthPanel() {
  const [data, setData] = useState<SourceHealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchSourceHealth()
      .then((result) => active && setData(result))
      .catch((err: unknown) =>
        active && setError(err instanceof Error ? err.message : "Failed to load source health."),
      );
    return () => {
      active = false;
    };
  }, []);

  return (
    <section aria-labelledby="health-heading" className="admin-panel">
      <h2 id="health-heading">Source health</h2>
      {error && <ErrorNotice message={error} />}
      {data === null ? (
        <p>Loading…</p>
      ) : data.sources.length === 0 ? (
        <p>No sources are registered.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col">Adapter</th>
              <th scope="col">Enabled</th>
              <th scope="col">Health</th>
              <th scope="col">Last scan</th>
              <th scope="col">Errors</th>
              <th scope="col">Last snapshot</th>
            </tr>
          </thead>
          <tbody>
            {data.sources.map((source) => (
              <tr key={source.source_id}>
                <td>{source.slug ?? `#${source.source_id}`}</td>
                <td>{source.adapter_type}</td>
                <td>{source.enabled ? "yes" : "no"}</td>
                <td>{source.health ?? "unknown"}</td>
                <td>{source.last_scan_status ?? "—"}</td>
                <td>{source.last_errors_count ?? "—"}</td>
                <td>{source.last_snapshot_fetched_at ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ConfigDiffPanel({ csrfToken }: { csrfToken: string }) {
  const [candidate, setCandidate] = useState("");
  const [result, setResult] = useState<ConfigDiffResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await postConfigDiff(candidate, csrfToken));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to validate the candidate config.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="configdiff-heading" className="admin-panel">
      <h2 id="configdiff-heading">Config diff (validate only)</h2>
      <p>
        Paste a candidate YAML configuration. It is validated with the same
        validators used at load time and diffed against the running config. This
        view never writes configuration.
      </p>
      {error && <ErrorNotice message={error} />}
      <form onSubmit={(event) => void submit(event)} aria-label="Validate a candidate config">
        <label htmlFor="config-candidate">Candidate YAML</label>
        <textarea
          id="config-candidate"
          value={candidate}
          onChange={(event) => setCandidate(event.target.value)}
          rows={10}
          required
        />
        <button type="submit" disabled={busy || candidate.trim() === ""}>
          Validate &amp; diff
        </button>
      </form>
      {result && (
        <div className="admin-configdiff-result">
          <p>
            Target: <code>{result.target}</code> ·{" "}
            <strong>{result.valid ? "VALID" : "INVALID"}</strong>
            {!result.committed_present && " · no running config to compare against"}
          </p>
          {result.problems.length > 0 && (
            <ul aria-label="Validation problems">
              {result.problems.map((problem, index) => (
                <li key={index}>{problem}</li>
              ))}
            </ul>
          )}
          <pre aria-label="Unified diff">{result.diff.join("\n") || "(no differences)"}</pre>
        </div>
      )}
    </section>
  );
}

export function AdminApp() {
  const [session, setSession] = useState<AdminSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchSession()
      .then((result) => {
        if (active) {
          setSession(result);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof AdminUnauthenticated) {
          setSession(null);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load the admin session.");
        }
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const signOut = useCallback(async () => {
    if (!session) return;
    try {
      await logout(session.csrf_token);
    } catch {
      // Even if the call fails, drop the local session so the gate reappears.
    }
    setSession(null);
  }, [session]);

  const panels = useMemo(() => {
    if (!session) return null;
    return (
      <>
        <KillSwitchPanel csrfToken={session.csrf_token} />
        <ReviewQueuePanel csrfToken={session.csrf_token} />
        <SourceHealthPanel />
        <ConfigDiffPanel csrfToken={session.csrf_token} />
      </>
    );
  }, [session]);

  return (
    <main className="admin-root">
      <h1>FreeTier Atlas — Admin</h1>
      {loading && <p>Loading…</p>}
      {!loading && error && <ErrorNotice message={error} />}
      {!loading && !error && !session && (
        <section aria-labelledby="login-heading" className="admin-panel">
          <h2 id="login-heading">Sign in</h2>
          <p>This is a private area restricted to allowlisted GitHub administrators.</p>
          <a className="admin-login-button" href={loginUrl()}>
            Sign in with GitHub
          </a>
        </section>
      )}
      {!loading && !error && session && (
        <>
          <p>
            Signed in as <strong>{session.login}</strong>{" "}
            <button type="button" onClick={() => void signOut()}>
              Sign out
            </button>
          </p>
          {panels}
        </>
      )}
    </main>
  );
}

export default AdminApp;
