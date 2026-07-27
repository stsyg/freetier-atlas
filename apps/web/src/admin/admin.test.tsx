import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AdminApp } from "./AdminApp";

// A tiny fetch router: match on method + path suffix and return a JSON Response.
// Every call is recorded so tests can assert the CSRF header / body / credentials
// of mutating requests.
interface Recorded {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
  credentials: string | undefined;
}

const calls: Recorded[] = [];

function json(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Handler = (init: RequestInit) => Response;

function installFetch(routes: Array<[string, Handler]>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const headers = (init?.headers ?? {}) as Record<string, string>;
      calls.push({
        url,
        method,
        headers,
        body: init?.body ? JSON.parse(init.body as string) : undefined,
        credentials: init?.credentials,
      });
      for (const [key, handler] of routes) {
        const [routeMethod, suffix] = key.split(" ");
        if (method === routeMethod && url.includes(suffix)) {
          return Promise.resolve(handler(init ?? {}));
        }
      }
      return Promise.resolve(json(500, { detail: `unrouted ${method} ${url}` }));
    }),
  );
}

const SESSION = { login: "stsyg", csrf_token: "csrf-token-abc" };

beforeEach(() => {
  calls.length = 0;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AdminApp — private admin surface (F007 slice 4)", () => {
  it("shows the GitHub login gate when the session is unauthenticated (401)", async () => {
    installFetch([["GET /admin/session", () => json(401, { detail: "auth required" })]]);

    render(<AdminApp />);

    const link = await screen.findByRole("link", { name: /sign in with github/i });
    expect(link).toHaveAttribute("href", "/api/admin/login");
    // The session probe was sent with the cookie (same-origin credentials).
    expect(calls[0].credentials).toBe("same-origin");
  });

  it("renders the four admin panels and toggles the kill switch with a CSRF header", async () => {
    let persisted = false;
    installFetch([
      ["GET /admin/session", () => json(200, SESSION)],
      [
        "GET /admin/kill-switch",
        () => json(200, { enabled: persisted, env_override: false, effective: persisted }),
      ],
      ["GET /admin/review-queue", () => json(200, { items: [], valid_actions: ["approved"] })],
      ["GET /admin/source-health", () => json(200, { sources: [] })],
      [
        "POST /admin/kill-switch",
        () => {
          persisted = true;
          return json(200, { enabled: true });
        },
      ],
    ]);

    render(<AdminApp />);

    expect(await screen.findByText(/signed in as/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /ai kill switch/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /review .* queue/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /source health/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /config diff/i })).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /enable kill switch/i }));

    await waitFor(() => {
      const toggle = calls.find((c) => c.method === "POST" && c.url.includes("/admin/kill-switch"));
      expect(toggle).toBeDefined();
      expect(toggle?.headers["X-CSRF-Token"]).toBe(SESSION.csrf_token);
      expect(toggle?.body).toEqual({ enabled: true });
      expect(toggle?.credentials).toBe("same-origin");
    });
  });

  it("validates a candidate config (validate-only) and sends the CSRF token", async () => {
    installFetch([
      ["GET /admin/session", () => json(200, SESSION)],
      ["GET /admin/kill-switch", () => json(200, { enabled: false, env_override: false, effective: false })],
      ["GET /admin/review-queue", () => json(200, { items: [], valid_actions: ["approved"] })],
      ["GET /admin/source-health", () => json(200, { sources: [] })],
      [
        "POST /admin/config-diff",
        () =>
          json(200, {
            target: "/app/config/llm-providers.yaml",
            valid: true,
            problems: [],
            diff: ["--- committed", "+++ candidate", "+llm: {}"],
            committed_present: true,
          }),
      ],
    ]);

    render(<AdminApp />);
    await screen.findByText(/signed in as/i);

    fireEvent.change(screen.getByLabelText(/candidate yaml/i), {
      target: { value: "llm:\n  providers: []\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: /validate .* diff/i }));

    expect(await screen.findByText(/VALID/)).toBeInTheDocument();
    const diff = calls.find((c) => c.method === "POST" && c.url.includes("/admin/config-diff"));
    expect(diff?.headers["X-CSRF-Token"]).toBe(SESSION.csrf_token);
    expect(diff?.body).toEqual({ candidate: "llm:\n  providers: []\n" });
  });
});
