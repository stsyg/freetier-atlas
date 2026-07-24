import { useCallback, useState } from "react";
import { fetchDeploymentExport, type DeploymentExport, type RecommendationRequest } from "../api";
import { downloadZip } from "./zip";

type DownloadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: DeploymentExport }
  | { kind: "error"; message: string };

function zipName(workloadName: string | null): string {
  const base = (workloadName ?? "deployment")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return `freetier-atlas-${base || "deployment"}.zip`;
}

/**
 * A "Download deployment" control for a computed recommendation (F007 slice 3).
 *
 * On activation it POSTs the SAME structured `request` to the stateless
 * `/adviser/export` endpoint, which returns server-VALIDATED, SECRET-FREE file
 * contents plus a manifest **without persisting anything**. The `.zip` is then
 * assembled entirely in the browser from those contents and offered as a
 * download — no secret material is ever included (`.env.example` carries only
 * placeholders).
 *
 * Display-only + honest: it renders exactly what the manifest reports (file
 * list, sizes, asserted platforms, validation checks) and never re-derives any
 * of it. The control is a native `<button>` (keyboard-operable), the region is
 * labelled, and status changes are announced via `role="status"`/`role="alert"`.
 */
export function DeploymentDownload({ request }: { request: RecommendationRequest }) {
  const [state, setState] = useState<DownloadState>({ kind: "idle" });

  const generate = useCallback(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    fetchDeploymentExport(request, controller.signal)
      .then((data) => {
        // Assemble + offer the .zip client-side from the validated contents.
        downloadZip(
          data.files.map((file) => ({ path: file.path, content: file.content })),
          zipName(data.workload_name),
        );
        setState({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : "Unknown error.";
        setState({ kind: "error", message });
      });
  }, [request]);

  return (
    <section className="deployment-download" aria-labelledby="deployment-download-heading">
      <h3 id="deployment-download-heading" className="section-heading">
        Download deployment scaffold
      </h3>
      <p className="muted">
        Get a portable, self-hostable scaffold (Docker Compose, <code>.env.example</code>, README)
        for this architecture. The server validates every file and returns only its contents — it
        stores nothing — and your browser builds the <code>.zip</code> locally.{" "}
        <strong>No secrets are ever included:</strong> <code>.env.example</code> holds placeholders
        only.
      </p>

      <button
        type="button"
        className="button button--primary"
        onClick={generate}
        disabled={state.kind === "loading"}
        aria-describedby="deployment-download-heading"
      >
        {state.kind === "loading" ? "Preparing download…" : "Download deployment (.zip)"}
      </button>

      {state.kind === "loading" ? (
        <p className="status status--loading" role="status">
          Validating and assembling your deployment bundle…
        </p>
      ) : null}

      {state.kind === "error" ? (
        <div className="status status--error" role="alert">
          <p>Unable to generate the deployment bundle: {state.message}</p>
          <p className="muted">Nothing was written on the server. Adjust your workload and retry.</p>
        </div>
      ) : null}

      {state.kind === "ready" ? (
        <div className="deployment-download__manifest" role="status">
          <p>
            Your <code>{zipName(state.data.workload_name)}</code> download has started
            {state.data.fully_zero_cost ? " ($0 architecture)." : "."}
          </p>
          <p className="muted">
            Validated server-side and secret-free; the server persisted nothing. Assembled in your
            browser.
          </p>
          <details>
            <summary>What’s in the bundle ({state.data.manifest.file_count} files)</summary>
            <ul className="deployment-download__files">
              {state.data.manifest.files.map((file) => (
                <li key={file.path}>
                  <code>{file.path}</code> — {file.size} bytes
                </li>
              ))}
            </ul>
            <p className="muted">
              Platforms asserted: {state.data.manifest.platforms.join(", ") || "Unknown"}.
            </p>
            <ul className="deployment-download__checks">
              {Object.entries(state.data.manifest.validation).map(([check, passed]) => (
                <li key={check}>
                  <span aria-hidden="true">{passed ? "✓" : "✕"}</span>{" "}
                  {check.replace(/_/g, " ")}: {passed ? "passed" : "failed"}
                </li>
              ))}
            </ul>
          </details>
        </div>
      ) : null}
    </section>
  );
}
