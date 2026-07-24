import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "./App";
import { catalogueFetch } from "./catalogue/testFixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

beforeEach(() => {
  window.location.hash = "#/";
});

function stubFetch(impl: typeof fetch) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

function goTo(hash: string) {
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new Event("hashchange"));
  });
}

async function renderBrowse() {
  stubFetch(catalogueFetch());
  render(<App />);
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { level: 1, name: /browse the free-tier catalogue/i }),
    ).toBeInTheDocument(),
  );
  await screen.findByTestId("results-list");
}

describe("App — catalogue browser routing + landmarks (F006 slice 2)", () => {
  it("landing shows the Browse view with a search form and multi-provider results", async () => {
    await renderBrowse();
    expect(screen.getByRole("form", { name: /search and filter offers/i })).toBeInTheDocument();
    // Results include offers from more than one provider — provider-agnostic.
    const list = screen.getByTestId("results-list");
    expect(within(list).getByText("Cloudflare Workers")).toBeInTheDocument();
    expect(within(list).getByText("Northwind Cloud")).toBeInTheDocument();
  });

  it("exposes a single h1, primary nav, main and contentinfo landmarks (a11y)", async () => {
    await renderBrowse();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
    // Active link is marked aria-current for assistive tech.
    const current = screen.getByRole("link", { current: "page" });
    expect(current).toHaveTextContent(/browse/i);
  });

  it("composes filters through the API and reflects the filtered result set", async () => {
    await renderBrowse();
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "acme-serverless" } });
    fireEvent.submit(screen.getByRole("form", { name: /search and filter offers/i }));
    await waitFor(() => {
      const rows = screen.getAllByTestId("result-row");
      // Only Acme Serverless offers remain after composing the provider filter.
      rows.forEach((row) => expect(within(row).getByText("Acme Serverless")).toBeInTheDocument());
    });
    expect(screen.queryByText("Cloudflare Workers")).not.toBeInTheDocument();
  });

  it("navigates to the category matrix showing all 14 categories across providers", async () => {
    await renderBrowse();
    goTo("#/categories");
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { level: 1, name: /free-tier coverage by category/i }),
      ).toBeInTheDocument(),
    );
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByTestId("matrix-row")).toHaveLength(14);
    expect(screen.getByRole("columnheader", { name: "Northwind Cloud" })).toBeInTheDocument();
  });

  it("selects offers to compare and renders a side-by-side of the picks", async () => {
    await renderBrowse();
    const rows = screen.getAllByTestId("result-row");
    fireEvent.click(within(rows[0]).getByRole("checkbox")); // offer 1 (Cloudflare)
    fireEvent.click(within(rows[2]).getByRole("checkbox")); // offer 3 (Northwind)
    // The nav badge reflects the selection count.
    expect(screen.getByRole("link", { name: /compare \(2\)/i })).toBeInTheDocument();

    goTo("#/compare");
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { level: 1, name: /compare free-tier offers/i }),
      ).toBeInTheDocument(),
    );
    const table = await screen.findByTestId("compare-table");
    expect(within(table).getByRole("columnheader", { name: /cloudflare/i })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: /northwind cloud/i })).toBeInTheDocument();
  });

  it("prompts to pick offers when fewer than two are selected", async () => {
    await renderBrowse();
    goTo("#/compare");
    await waitFor(() => expect(screen.getByTestId("compare-hint")).toBeInTheDocument());
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("retains the single-provider Cloudflare experience on the provider route", async () => {
    await renderBrowse();
    goTo("#/provider/cloudflare");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: /cloudflare/i })).toBeInTheDocument(),
    );
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("Cloudflare Workers")).toBeInTheDocument();
  });

  it("reports unknown values honestly rather than fabricating them", async () => {
    await renderBrowse();
    // Cloudflare Pages (offer 2) carries an UNKNOWN Z0 class in the search index.
    const rows = screen.getAllByTestId("result-row");
    const pages = rows.find((r) => within(r).queryByText("Cloudflare Pages"))!;
    expect(within(pages).getAllByTestId("z0-badge")[0]).toHaveTextContent(/unknown/i);
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
    await screen.findByTestId("results-list");
  });
});
