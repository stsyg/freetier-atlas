import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { SearchControls } from "./SearchControls";
import { ResultsList } from "./ResultsList";
import { CategoryMatrix } from "./CategoryMatrix";
import { CompareView } from "./CompareView";
import {
  categoryMatrix,
  compareOffers,
  providerList,
  searchIndex,
} from "./testFixtures";
import type { CompareResponse, SearchResponse } from "../api";

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
    expect(within(providerSelect).getByRole("option", { name: "Northwind Cloud" })).toBeInTheDocument();
    expect(within(providerSelect).getByRole("option", { name: "Acme Serverless" })).toBeInTheDocument();
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
    expect(within(table).getByText("Free-tier coverage by category and provider")).toBeInTheDocument();
    // Column header per provider (+ the row-label column).
    const colHeaders = within(table).getAllByRole("columnheader");
    expect(colHeaders[0]).toHaveTextContent(/category/i);
    expect(within(table).getByRole("columnheader", { name: "Cloudflare" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Northwind Cloud" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Acme Serverless" })).toBeInTheDocument();
    expect(screen.getAllByTestId("matrix-row")).toHaveLength(14);
  });

  it("shows coverage as a colour+text+icon badge (never colour-only) taken from the API", () => {
    render(<CategoryMatrix data={categoryMatrix} />);
    const badges = screen.getAllByTestId("coverage-badge");
    const verified = badges.find((b) => b.getAttribute("data-state") === "verified_free")!;
    expect(verified).toHaveTextContent(/verified free/i);
    expect(within(verified).getByText("✓")).toHaveAttribute("aria-hidden", "true");
    const notOffered = badges.find((b) => b.getAttribute("data-state") === "not_offered")!;
    expect(notOffered).toHaveTextContent(/not offered/i);
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
    expect(within(table).getByText("Side-by-side comparison of the selected offers")).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: /cloudflare/i })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: /northwind cloud/i })).toBeInTheDocument();
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
