/**
 * Client for the private admin API (F007 slice 4).
 *
 * Separate from the read-only catalogue client (`../api`) because the admin
 * surface is stateful: requests carry the signed session cookie
 * (`credentials: "same-origin"`) and mutating calls send a per-session CSRF
 * token in the `X-CSRF-Token` header. Like the catalogue client every path is a
 * FIXED, internally-constructed string built only from internal identifiers —
 * never a caller-supplied URL — so there is no SSRF surface. The client never
 * stores or logs a secret; the session cookie is HttpOnly and unreadable to JS,
 * and the CSRF token lives only in memory for the lifetime of the page.
 */

import { API_BASE } from "../api";

const ADMIN_BASE = `${API_BASE}/admin`;

export interface AdminSession {
  login: string;
  csrf_token: string;
}

export interface KillSwitchState {
  enabled: boolean;
  env_override: boolean;
  effective: boolean;
}

export interface ReviewQueueItem {
  id: number;
  reason: string;
  recommended_action: string | null;
  admin_disposition: string;
  evidence_conflict: Record<string, unknown> | null;
  candidate_facts: Record<string, unknown> | null;
  offer_id: number | null;
  scan_run_id: number | null;
  created_at: string;
}

export interface ReviewQueueResponse {
  items: ReviewQueueItem[];
  valid_actions: string[];
}

export interface SourceHealthItem {
  source_id: number;
  slug: string | null;
  adapter_type: string;
  official: boolean;
  enabled: boolean;
  health: string | null;
  endpoint: string | null;
  last_scan_status: string | null;
  last_scan_finished_at: string | null;
  last_errors_count: number | null;
  last_snapshot_fetched_at: string | null;
}

export interface SourceHealthResponse {
  sources: SourceHealthItem[];
}

export interface ConfigDiffResponse {
  target: string;
  valid: boolean;
  problems: string[];
  diff: string[];
  committed_present: boolean;
}

/** Raised when the session is absent/invalid so the UI can show the login gate. */
export class AdminUnauthenticated extends Error {
  constructor() {
    super("Admin authentication required.");
    this.name = "AdminUnauthenticated";
  }
}

async function parseJson<T>(response: Response): Promise<T> {
  try {
    return (await response.json()) as T;
  } catch {
    throw new Error("The admin API response was not valid JSON.");
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${ADMIN_BASE}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      signal,
    });
  } catch {
    throw new Error("Unable to reach the admin API. Is the stack running?");
  }
  if (response.status === 401) {
    throw new AdminUnauthenticated();
  }
  if (!response.ok) {
    throw new Error(`The admin API returned HTTP ${response.status}.`);
  }
  return parseJson<T>(response);
}

async function postJson<T>(
  path: string,
  body: unknown,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${ADMIN_BASE}${path}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      credentials: "same-origin",
      body: JSON.stringify(body ?? {}),
      signal,
    });
  } catch {
    throw new Error("Unable to reach the admin API. Is the stack running?");
  }
  if (response.status === 401) {
    throw new AdminUnauthenticated();
  }
  if (response.status === 403) {
    throw new Error("The action was rejected (invalid or expired CSRF token). Reload and retry.");
  }
  if (!response.ok) {
    throw new Error(`The admin API returned HTTP ${response.status}.`);
  }
  return parseJson<T>(response);
}

/** The full-page URL that begins the GitHub OAuth login flow. */
export function loginUrl(): string {
  return `${ADMIN_BASE}/login`;
}

export function fetchSession(signal?: AbortSignal): Promise<AdminSession> {
  return getJson<AdminSession>("/session", signal);
}

export function logout(csrfToken: string, signal?: AbortSignal): Promise<{ ok: boolean }> {
  return postJson<{ ok: boolean }>("/logout", {}, csrfToken, signal);
}

export function fetchKillSwitch(signal?: AbortSignal): Promise<KillSwitchState> {
  return getJson<KillSwitchState>("/kill-switch", signal);
}

export function setKillSwitch(
  enabled: boolean,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<{ enabled: boolean }> {
  return postJson<{ enabled: boolean }>("/kill-switch", { enabled }, csrfToken, signal);
}

export function fetchReviewQueue(
  disposition?: string,
  signal?: AbortSignal,
): Promise<ReviewQueueResponse> {
  const query = disposition ? `?disposition=${encodeURIComponent(disposition)}` : "";
  return getJson<ReviewQueueResponse>(`/review-queue${query}`, signal);
}

export function actOnReviewItem(
  itemId: number,
  disposition: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<{ id: number; disposition: string }> {
  return postJson<{ id: number; disposition: string }>(
    `/review-queue/${itemId}/action`,
    { disposition },
    csrfToken,
    signal,
  );
}

export function fetchSourceHealth(signal?: AbortSignal): Promise<SourceHealthResponse> {
  return getJson<SourceHealthResponse>("/source-health", signal);
}

export function postConfigDiff(
  candidate: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<ConfigDiffResponse> {
  return postJson<ConfigDiffResponse>("/config-diff", { candidate }, csrfToken, signal);
}
