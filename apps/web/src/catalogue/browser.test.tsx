import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { SearchControls } from "./SearchControls";
import { ResultsList } from "./ResultsList";
import { CategoryMatrix } from "./CategoryMatrix";
import { CompareView } from "./CompareView";
import {
  allCoverageStatesMatrix,
  categoryMatrix,
  compareOffers,
  CURRENT_EVIDENCE,
  providerList,
  searchIndex,
  STALE_EVIDENCE,
  UNCHECKED_EVIDENCE,
} from "./testFixtures";
import { COVERAGE_MEANINGS, COVERAGE_STATE_ORDER } from "./vocab";
import type { CompareResponse, EvidenceCurrency, SearchResponse, SearchResultItem } from "../api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function searchResponse(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    filters: {
      q: null,
      provider: null,
      category: null,
      zero_cost_class: null,
      offer_type: null,
      commercial_use: null,
      status: null,
      evidence_current: null,
    },
    page: 1,
    page_size: 3,
    total_results: searchIndex.length,
    total_pages: 2,
    results: searchIndex.slice(0, 3),
    ...overrides,
  };
}

describe("SearchControls", () => {
  it("renders a labelled keyword input and every filter select", () => {
    render(
      <SearchControls
        value={{ page: 1 }}
        providers={providerList}
        categories={categoryMatrix.categories}
        onSubmit={() => {}}
        onReset={() => {}}
      />,
    );
    expect(screen.getByLabelText(/keyword/i)).toHaveAttribute("type", "search");
    expect(screen.getByLabelText(/provider/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/category/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/zero-cost class/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/offer type/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/commercial use/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/status/i)).toBeInTheDocument();
  });

  it("populates provider + category options from the API (never hard-coded)", () => {
    render(
      <SearchControls
        value={{ page: 1 }}
        providers={providerList}
        categories={categoryMatrix.categories}
        onSubmit={() => {}}
        onReset={() => {}}
      />,
    );
    const providerSelect = screen.getByLabelText(/provider/i);
    expect(
      within(providerSelect).getByRole("option", { name: "Northwind Cloud" }),
    ).toBeInTheDocument();
    expect(
      within(providerSelect).getByRole("option", { name: "Acme Serverless" }),
    ).toBeInTheDocument();
    const categorySelect = screen.getByLabelText(/category/i);
    expect(
      within(categorySelect).getByRole("option", { name: "Serverless functions" }),
    ).toBeInTheDocument();
  });

  it("emits a composed, typed query on submit", () => {
    const onSubmit = vi.fn();
    render(
      <SearchControls
        value={{ page: 1 }}
        providers={providerList}
        categories={categoryMatrix.categories}
        onSubmit={onSubmit}
        onReset={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/keyword/i), { target: { value: "store" } });
    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "acme-serverless" } });
    fireEvent.change(screen.getByLabelText(/zero-cost class/i), {
      target: { value: "Z1_BILLING_EXPOSURE" },
    });
    fireEvent.submit(screen.getByRole("form", { name: /search and filter offers/i }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        q: "store",
        provider: "acme-serverless",
        zero_cost_class: "Z1_BILLING_EXPOSURE",
        page: 1,
      }),
    );
  });
});

describe("ResultsList", () => {
  it("renders results from multiple providers with a Z0 badge and confidence label", () => {
    render(
      <ResultsList
        data={searchResponse()}
        selectedIds={[]}
        canSelectMore
        onToggleCompare={() => {}}
        onPageChange={() => {}}
      />,
    );
    const rows = screen.getAllByTestId("result-row");
    expect(rows.length).toBe(3);
    expect(screen.getByText("Cloudflare Workers")).toBeInTheDocument();
    expect(screen.getByText("Northwind Cloud")).toBeInTheDocument();
    const firstBadge = within(rows[0]).getAllByTestId("z0-badge")[0];
    expect(firstBadge).toHaveTextContent(/truly free/i);
    expect(within(rows[0]).getByText(/high confidence/i)).toBeInTheDocument();
  });

  it("has an accessible pagination nav that fires page changes", () => {
    const onPageChange = vi.fn();
    render(
      <ResultsList
        data={searchResponse()}
        selectedIds={[]}
        canSelectMore
        onToggleCompare={() => {}}
        onPageChange={onPageChange}
      />,
    );
    expect(screen.getByTestId("pagination-status")).toHaveTextContent("Page 1 of 2");
    const prev = screen.getByRole("button", { name: /previous/i });
    expect(prev).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("toggles compare selection with a keyboard-operable checkbox", () => {
    const onToggleCompare = vi.fn();
    render(
      <ResultsList
        data={searchResponse()}
        selectedIds={[]}
        canSelectMore
        onToggleCompare={onToggleCompare}
        onPageChange={() => {}}
      />,
    );
    const checkbox = within(screen.getAllByTestId("result-row")[0]).getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(onToggleCompare).toHaveBeenCalledWith(1);
  });

  it("disables further selection once the cap is reached", () => {
    render(
      <ResultsList
        data={searchResponse()}
        selectedIds={[99]}
        canSelectMore={false}
        onToggleCompare={() => {}}
        onPageChange={() => {}}
      />,
    );
    const checkbox = within(screen.getAllByTestId("result-row")[0]).getByRole("checkbox");
    expect(checkbox).toBeDisabled();
  });

  it("shows an honest empty state when nothing matches", () => {
    render(
      <ResultsList
        data={searchResponse({ results: [], total_results: 0, total_pages: 1 })}
        selectedIds={[]}
        canSelectMore
        onToggleCompare={() => {}}
        onPageChange={() => {}}
      />,
    );
    expect(screen.getByTestId("results-empty")).toBeInTheDocument();
  });
});

describe("CategoryMatrix", () => {
  it("renders an accessible table with all 14 categories and every provider column", () => {
    render(<CategoryMatrix data={categoryMatrix} />);
    const table = screen.getByTestId("matrix-table");
    expect(
      within(table).getByText("Free-tier coverage by category and provider"),
    ).toBeInTheDocument();
    // Column header per provider (+ the row-label column).
    const colHeaders = within(table).getAllByRole("columnheader");
    expect(colHeaders[0]).toHaveTextContent(/category/i);
    expect(within(table).getByRole("columnheader", { name: "Cloudflare" })).toBeInTheDocument();
    expect(
      within(table).getByRole("columnheader", { name: "Northwind Cloud" }),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole("columnheader", { name: "Acme Serverless" }),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("matrix-row")).toHaveLength(14);
  });

  it("shows coverage as a colour+text+icon badge (never colour-only) taken from the API", () => {
    render(<CategoryMatrix data={categoryMatrix} />);
    const badges = screen.getAllByTestId("coverage-badge");
    const verified = badges.find((b) => b.getAttribute("data-state") === "verified_free")!;
    expect(verified).toHaveTextContent(/verified free/i);
    expect(within(verified).getByText("✓")).toHaveAttribute("aria-hidden", "true");
    const unknown = badges.find((b) => b.getAttribute("data-state") === "unknown")!;
    expect(unknown).toHaveTextContent(/unknown/i);
  });

  it("never renders not_offered for a pair with no published offers", () => {
    render(<CategoryMatrix data={categoryMatrix} />);
    const badges = screen.getAllByTestId("coverage-badge");
    const empty = badges.filter((b) => b.getAttribute("data-derived-state") === "unknown");
    expect(empty.length).toBeGreaterThan(0);
    for (const badge of empty) {
      expect(badge.getAttribute("data-state")).not.toBe("not_offered");
      expect(badge).not.toHaveTextContent(/not offered/i);
    }
  });

  it("renders a distinct badge for every one of the seven coverage states", () => {
    render(<CategoryMatrix data={allCoverageStatesMatrix} />);
    const badges = screen.getAllByTestId("coverage-badge");
    const seen = new Map<string, string>();
    for (const badge of badges) {
      seen.set(badge.getAttribute("data-state")!, badge.textContent ?? "");
    }
    for (const state of COVERAGE_STATE_ORDER) {
      expect(seen.has(state)).toBe(true);
    }
    // Labels must be distinct so the states are never collapsed visually.
    expect(new Set(seen.values()).size).toBe(COVERAGE_STATE_ORDER.length);
  });

  it("distinguishes a declared not_offered from an unverified unknown", () => {
    render(<CategoryMatrix data={allCoverageStatesMatrix} />);
    const badges = screen.getAllByTestId("coverage-badge");
    const declined = badges.find((b) => b.getAttribute("data-state") === "not_offered")!;
    const unknown = badges.find((b) => b.getAttribute("data-state") === "unknown")!;
    expect(declined).toHaveTextContent(/not offered/i);
    expect(unknown).toHaveTextContent(/unknown/i);
    expect(declined.textContent).not.toBe(unknown.textContent);
    expect(declined.className).not.toBe(unknown.className);
    // The declared reason travels with the claim.
    expect(declined.getAttribute("title")).toMatch(/publishes no compute product line/i);
  });

  it("falls back to unknown (not not_offered) when the API omits a provider entry", () => {
    render(<CategoryMatrix data={allCoverageStatesMatrix} />);
    const row = screen.getAllByTestId("matrix-row")[0];
    const absent = within(row)
      .getAllByTestId("coverage-badge")
      .find((b) => b.getAttribute("data-declared-state") === "");
    expect(absent).toBeDefined();
    expect(absent!.getAttribute("data-state")).toBe("unknown");
  });

  it("surfaces a declared-vs-derived mismatch in the cell", () => {
    render(<CategoryMatrix data={allCoverageStatesMatrix} />);
    const conflicting = screen
      .getAllByTestId("coverage-badge")
      .find((b) => b.getAttribute("data-mismatch") === "true")!;
    expect(conflicting.getAttribute("data-state")).toBe("conflicting");
    expect(conflicting.getAttribute("title")).toMatch(/declared "unknown"/i);
    expect(conflicting.getAttribute("title")).toMatch(/verified_free/i);
  });

  it("renders an accessible legend explaining all seven states", () => {
    render(<CategoryMatrix data={categoryMatrix} />);
    const legend = screen.getByTestId("coverage-legend");
    expect(
      within(legend).getByRole("heading", { name: /what the states mean/i }),
    ).toBeInTheDocument();
    for (const state of COVERAGE_STATE_ORDER) {
      const item = legend.querySelector(`[data-legend-state="${state}"]`);
      expect(item).not.toBeNull();
      expect(item!.textContent).toContain(COVERAGE_MEANINGS[state].label);
      expect(item!.textContent).toContain(COVERAGE_MEANINGS[state].description);
    }
    expect(legend.querySelectorAll("[data-legend-state]")).toHaveLength(7);
  });

  it("labels every cell for the stacked narrow-viewport layout", () => {
    render(<CategoryMatrix data={categoryMatrix} />);
    const row = screen.getAllByTestId("matrix-row")[0];
    const cells = row.querySelectorAll("td");
    expect(cells.length).toBeGreaterThan(0);
    for (const cell of Array.from(cells)) {
      // The stacked layout renders this via `td::before { content: attr(data-label) }`,
      // so the provider name stays visible without a horizontal scroll.
      expect(cell.getAttribute("data-label")).toBeTruthy();
    }
  });

  it("surfaces uncategorized published offers honestly", () => {
    render(<CategoryMatrix data={categoryMatrix} />);
    const roll = screen.getByTestId("matrix-uncategorized");
    expect(within(roll).getByText(/northwind cloud/i)).toBeInTheDocument();
  });
});

describe("CompareView", () => {
  function compareResponse(ids: number[]): CompareResponse {
    return { offer_ids: ids, offers: ids.map((id) => compareOffers[id]) };
  }

  it("renders an accessible side-by-side table across providers", () => {
    render(<CompareView data={compareResponse([1, 3, 4])} />);
    const table = screen.getByTestId("compare-table");
    expect(
      within(table).getByText("Side-by-side comparison of the selected offers"),
    ).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: /cloudflare/i })).toBeInTheDocument();
    expect(
      within(table).getByRole("columnheader", { name: /northwind cloud/i }),
    ).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /zero-cost class/i })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /quotas/i })).toBeInTheDocument();
  });

  it("shows a normalized quota and honestly labels an un-normalizable one Unknown", () => {
    render(<CompareView data={compareResponse([1, 3, 4])} />);
    expect(screen.getByTestId("quota-normalized")).toHaveTextContent(/3000000 requests_per_month/i);
    const unnormalized = screen.getByTestId("quota-unnormalized");
    expect(unnormalized).toHaveTextContent(/normalized: unknown/i);
  });

  it("shows the confidence LABEL primary and the numeric score only in a closed details", () => {
    render(<CompareView data={compareResponse([1, 3, 4])} />);
    const badges = screen.getAllByTestId("confidence-badge");
    expect(badges[0]).toHaveTextContent(/confidence: high/i);
    const advanced = screen.getAllByTestId("confidence-advanced")[0];
    expect(advanced).not.toHaveAttribute("open");
    expect(within(advanced).getByTestId("confidence-score")).toHaveTextContent("0.91");
    fireEvent.click(within(advanced).getByText(/advanced: score/i));
    expect(advanced).toHaveAttribute("open");
  });

  it("renders null flags and absent signals as Unknown, never fabricated", () => {
    render(<CompareView data={compareResponse([3])} />);
    const table = screen.getByTestId("compare-table");
    // Northwind's commercial_use is null and freshness present; paid-deps null.
    expect(within(table).getAllByText("Unknown").length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// What a user ACTUALLY SEES for a stale / unverifiable / fresh free claim.
//
// This is the rendered page, not the API contract. The three arms are asserted
// against the real components, because the product rule -- no unsupported claim
// that a service is free may ever ship -- is a statement about what reaches the
// reader, and a correct API rendered by a component that ignores the new field
// would still ship the claim.
// ---------------------------------------------------------------------------

describe("Rendered page — an expired free claim", () => {
  function renderResults(currency: EvidenceCurrency) {
    const result: SearchResultItem = {
      ...searchIndex[0],
      zero_cost_class: "Z0_TRUE_FREE",
      evidence_currency: currency,
    };
    render(
      <ResultsList
        data={searchResponse({ results: [result], total_results: 1, total_pages: 1 })}
        selectedIds={[]}
        canSelectMore
        onToggleCompare={() => {}}
        onPageChange={() => {}}
      />,
    );
    return screen.getByTestId("result-row");
  }

  it("shows a FRESH free offer as truly free, with no warning at all", () => {
    const row = renderResults(CURRENT_EVIDENCE);
    const badge = within(row).getByTestId("z0-badge");
    expect(badge).toHaveTextContent(/truly free/i);
    expect(badge).not.toHaveTextContent(/not verified/i);
    expect(within(row).queryByTestId("evidence-currency-note")).toBeNull();
  });

  it("shows an EXPIRED free offer still listed, but visibly not verified", () => {
    const row = renderResults(STALE_EVIDENCE);
    // Still present: withdrawing a genuinely free offer is its own defect.
    const badge = within(row).getByTestId("z0-badge");
    expect(badge).toHaveTextContent(/truly free/i);
    // And visibly qualified, in TEXT rather than colour.
    expect(badge).toHaveTextContent(/not verified/i);
    const note = within(row).getByTestId("evidence-currency-note");
    expect(note).toHaveAttribute("data-currency-state", "stale");
    expect(note).toHaveTextContent(/no longer known to be current/i);
  });

  it("shows an UNVERIFIABLE free offer differently from an expired one", () => {
    const row = renderResults(UNCHECKED_EVIDENCE);
    expect(within(row).getByTestId("z0-badge")).toHaveTextContent(/not verified/i);
    const note = within(row).getByTestId("evidence-currency-note");
    expect(note).toHaveAttribute("data-currency-state", "unchecked");
    expect(note).toHaveTextContent(/cannot be established/i);
    // Absence of evidence is not evidence of expiry.
    expect(note).not.toHaveTextContent(/past its/i);
  });
});

describe("Rendered page — the currency filter is its own control", () => {
  it("submits evidence_current separately from zero_cost_class", () => {
    const onSubmit = vi.fn();
    render(
      <SearchControls
        value={{}}
        providers={providerList}
        categories={categoryMatrix.categories}
        onSubmit={onSubmit}
        onReset={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Zero-cost class"), {
      target: { value: "Z0_TRUE_FREE" },
    });
    fireEvent.change(screen.getByLabelText("Evidence"), { target: { value: "true" } });
    fireEvent.submit(screen.getByLabelText("Search and filter offers"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const submitted = onSubmit.mock.calls[0][0];
    expect(submitted.zero_cost_class).toBe("Z0_TRUE_FREE");
    expect(submitted.evidence_current).toBe(true);
  });

  it("defaults to ANY evidence state, so nothing is hidden unless asked", () => {
    const onSubmit = vi.fn();
    render(
      <SearchControls
        value={{}}
        providers={providerList}
        categories={[]}
        onSubmit={onSubmit}
        onReset={() => {}}
      />,
    );
    fireEvent.submit(screen.getByLabelText("Search and filter offers"));
    expect(onSubmit.mock.calls[0][0].evidence_current).toBeNull();
  });
});
