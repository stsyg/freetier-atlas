/**
 * Minimal client for the FreeTier Atlas API.
 *
 * The frontend talks to the API through the relative `/api` prefix. In the
 * container an nginx reverse proxy forwards `/api/` to the api service; during
 * local development Vite proxies it to the API on localhost. This keeps the
 * app same-origin and free of hard-coded hosts or CORS configuration.
 */

export interface ApiHealth {
  status: string;
  service: string;
  version: string;
  environment: string;
}

/** The base path for API calls; overridable via VITE_API_BASE at build time. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

/**
 * Fetch the API liveness health payload from `${API_BASE}/health`.
 *
 * Throws an Error with an actionable, credential-free message when the request
 * fails or the response is not a 200 with the expected shape.
 */
export async function fetchApiHealth(signal?: AbortSignal): Promise<ApiHealth> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/health`, {
      headers: { Accept: "application/json" },
      signal,
    });
  } catch {
    throw new Error("Unable to reach the API. Is the stack running?");
  }

  if (!response.ok) {
    throw new Error(`API health check returned HTTP ${response.status}.`);
  }

  const data = (await response.json()) as Partial<ApiHealth>;
  if (!data || typeof data.status !== "string") {
    throw new Error("API health response was not in the expected format.");
  }

  return {
    status: data.status,
    service: data.service ?? "unknown",
    version: data.version ?? "unknown",
    environment: data.environment ?? "unknown",
  };
}
