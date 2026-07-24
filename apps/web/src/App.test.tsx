import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "./App";
import { catalogueFetch } from "./catalogue/testFixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function stubFetch(impl: typeof fetch) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

async function renderLoaded() {
  stubFetch(catalogueFetch());
  render(<App />);
  await waitFor(() =>
    expect(screen.getByRole("heading", { level: 1, name: /cloudflare/i })).toBeInTheDocument(),
  );
}

describe("App (Cloudflare provider experience)", () => {
  it("renders the provider header with completeness and freshness (scope 6)", async () => {
    await renderLoaded();
    const header = screen.getByTestId("provider-header");
    expect(within(header).getByText(/completeness/i)).toBeInTheDocument();
    expect(within(header).getByText("92%")).toBeInTheDocument();
    expect(within(header).getByText(/freshness/i)).toBeInTheDocument();
    expect(within(header).getByText("80%")).toBeInTheDocument();
    expect(within(header).getByText(/cloudflare\.com/)).toBeInTheDocument();
  });

  it("renders category / service states with Z0 badges and offer links (scope 1)", async () => {
    await renderLoaded();
    expect(screen.getByRole("heading", { name: /service states/i })).toBeInTheDocument();
    expect(screen.getByText("Cloudflare Workers")).toBeInTheDocument();
    expect(screen.getByText("Cloudflare Pages")).toBeInTheDocument();
    // In-page anchor link to the full offer card.
    const link = screen.getAllByRole("link", { name: /free tier/i })[0];
    expect(link).toHaveAttribute("href", "#offer-1");
  });

  it("shows each offer's Z0 badge and plain-language reasons (scope 2)", async () => {
    await renderLoaded();
    const cards = screen.getAllByTestId("offer-card");
    const workers = cards.find((c) => c.id === "offer-1")!;
    // Z0 badge carries a visible text label, never colour alone.
    const badge = within(workers).getAllByTestId("z0-badge")[0];
    expect(badge).toHaveTextContent(/truly free/i);
    const reasons = within(workers).getByTestId("offer-reasons");
    expect(within(reasons).getByText(/no credit card is required/i)).toBeInTheDocument();
  });

  it("renders quota rows readably (scope 7)", async () => {
    await renderLoaded();
    const workers = screen.getAllByTestId("offer-card").find((c) => c.id === "offer-1")!;
    const table = within(workers).getByTestId("quota-table");
    expect(within(table).getByText(/requests per day/i)).toBeInTheDocument();
    expect(within(table).getByText("100000")).toBeInTheDocument();
    expect(within(table).getByText(/requests blocked/i)).toBeInTheDocument();
  });

  it("shows the confidence LABEL as primary and the numeric score only in advanced (scope 4)", async () => {
    await renderLoaded();
    const workers = screen.getAllByTestId("offer-card").find((c) => c.id === "offer-1")!;
    const badge = within(workers).getByTestId("confidence-badge");
    expect(badge).toHaveTextContent(/confidence: high/i);
    // The numeric score lives inside a collapsed <details>, not as a primary field.
    const advanced = within(workers).getByTestId("confidence-advanced");
    expect(advanced).not.toHaveAttribute("open");
    expect(within(advanced).getByTestId("confidence-score")).toHaveTextContent("0.91");
  });

  it("reveals the numeric score after opening the advanced disclosure (scope 4)", async () => {
    await renderLoaded();
    const workers = screen.getAllByTestId("offer-card").find((c) => c.id === "offer-1")!;
    const advanced = within(workers).getByTestId("confidence-advanced");
    const summary = within(workers).getByText(/advanced: confidence score/i);
    fireEvent.click(summary);
    expect(advanced).toHaveAttribute("open");
  });

  it("renders official evidence with a safe external link (scope 3)", async () => {
    await renderLoaded();
    const workers = screen.getAllByTestId("offer-card").find((c) => c.id === "offer-1")!;
    const evidence = within(workers).getByTestId("evidence");
    expect(within(evidence).getByTestId("evidence-official")).toHaveTextContent(/official/i);
    const link = within(evidence).getByRole("link", {
      name: /developers\.cloudflare\.com/i,
    });
    expect(link).toHaveAttribute("href", "https://developers.cloudflare.com/workers/platform/pricing/");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("renders version history and change events (scope 5)", async () => {
    await renderLoaded();
    const workers = screen.getAllByTestId("offer-card").find((c) => c.id === "offer-1")!;
    const history = within(workers).getByTestId("offer-history");
    expect(within(history).getByText("v1")).toBeInTheDocument();
    expect(within(history).getByText(/offer added/i)).toBeInTheDocument();
  });

  it("reports unknown values honestly instead of fabricating them", async () => {
    await renderLoaded();
    const pages = screen.getAllByTestId("offer-card").find((c) => c.id === "offer-2")!;
    // Unknown Z0 class shows an explicit "Unknown" badge label.
    expect(within(pages).getAllByTestId("z0-badge")[0]).toHaveTextContent(/unknown/i);
    // Null tri-states and signals render as "Unknown", never as a guessed value.
    expect(within(pages).getAllByText("Unknown").length).toBeGreaterThan(0);
    // No evidence and no quotas degrade to honest empty-state copy.
    expect(within(pages).getByText(/no official evidence/i)).toBeInTheDocument();
    expect(within(pages).getByText(/no quota limits/i)).toBeInTheDocument();
    expect(within(pages).getByText(/no versions recorded/i)).toBeInTheDocument();
  });

  it("exposes a single top-level heading and semantic landmarks (a11y)", async () => {
    await renderLoaded();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });

  it("shows an actionable, credential-free error with a retry that recovers", async () => {
    let calls = 0;
    stubFetch((async (input: RequestInfo | URL) => {
      calls += 1;
      if (calls <= 3) throw new TypeError("network error");
      return catalogueFetch()(input);
    }) as typeof fetch);

    render(<App />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/is the stack running/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: /cloudflare/i })).toBeInTheDocument(),
    );
  });
});
