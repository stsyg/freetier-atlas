import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import App from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function stubFetch(impl: typeof fetch) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("App", () => {
  it("renders the product title and tagline", async () => {
    stubFetch(async () =>
      Response.json({
        status: "ok",
        service: "FreeTier Atlas API",
        version: "0.1.0.dev0",
        environment: "development",
      }),
    );
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: /freetier atlas/i })).toBeInTheDocument();
    expect(screen.getByText(/catalogue and architecture adviser/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/API is/i)).toBeInTheDocument());
  });

  it("shows the API status once the health call resolves", async () => {
    stubFetch(async () =>
      Response.json({
        status: "ok",
        service: "FreeTier Atlas API",
        version: "0.1.0.dev0",
        environment: "development",
      }),
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/API is/i)).toBeInTheDocument();
    });
    expect(screen.getByText("0.1.0.dev0")).toBeInTheDocument();
    expect(screen.getByText("development")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/health", expect.objectContaining({ signal: expect.anything() }));
  });

  it("shows an actionable error when the API is unreachable", async () => {
    stubFetch(async () => {
      throw new TypeError("network error");
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/Is the stack running/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows an error when the API responds with a non-200 status", async () => {
    stubFetch(async () => new Response("boom", { status: 503 }));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/HTTP 503/i)).toBeInTheDocument();
  });
});
