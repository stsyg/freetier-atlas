import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "./App";
import { catalogueFetch } from "./catalogue/testFixtures";
import { adviserFetch, assistedFetch } from "./adviser/testFixtures";

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
    expect(
      within(table).getByRole("columnheader", { name: /northwind cloud/i }),
    ).toBeInTheDocument();
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

describe("App — adviser recommendation experience (F006 slice 4)", () => {
  async function renderAdviser() {
    stubFetch(adviserFetch(catalogueFetch()));
    window.location.hash = "#/adviser";
    render(<App />);
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { level: 1, name: /architecture adviser/i }),
      ).toBeInTheDocument(),
    );
  }

  function fillFirstDemand(overrides?: { category?: string; metric?: string; amount?: string }) {
    const category = overrides?.category ?? "serverless-functions";
    fireEvent.change(screen.getByLabelText(/^category$/i), { target: { value: category } });
    fireEvent.change(screen.getByLabelText(/^metric$/i), {
      target: { value: overrides?.metric ?? "invocations" },
    });
    fireEvent.change(screen.getByLabelText(/^amount$/i), {
      target: { value: overrides?.amount ?? "1000" },
    });
    fireEvent.change(screen.getByLabelText(/^unit$/i), { target: { value: "count" } });
  }

  it("keeps a single h1 and exposes the structured form (no NL/URL input)", async () => {
    await renderAdviser();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("form", { name: /describe your workload/i })).toBeInTheDocument();
    // The active nav link is the Adviser link.
    expect(screen.getByRole("link", { current: "page" })).toHaveTextContent(/adviser/i);
    // No free-text "describe your app" textarea exists — structured input only.
    expect(screen.queryByRole("textbox", { name: /describe.*app|paste.*url/i })).toBeNull();
  });

  it("submits the structured form and renders a satisfiable $0 recommendation", async () => {
    await renderAdviser();
    fireEvent.change(screen.getByLabelText(/workload name/i), {
      target: { value: "Personal side project" },
    });
    fillFirstDemand();
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload/i }));

    await screen.findByTestId("zero-cost-proof");
    expect(screen.getByTestId("zero-cost-badge")).toHaveTextContent(/\$0 guaranteed/i);
    // Provider-agnostic: two distinct providers appear in the architecture.
    expect(screen.getByText("Northwind Functions")).toBeInTheDocument();
    expect(screen.getByText("Initech Buckets")).toBeInTheDocument();
    // Still exactly one h1 after the results render below the form.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("offers a browser-side deployment download that assembles a secret-free .zip", async () => {
    // jsdom lacks URL.createObjectURL / anchor download; add them (without
    // clobbering the URL constructor the fetch stub relies on) so the
    // client-side assembly + download path runs end-to-end without a real save.
    const urlCtor = URL as unknown as {
      createObjectURL?: (blob: Blob) => string;
      revokeObjectURL?: (url: string) => void;
    };
    const hadCreate = "createObjectURL" in urlCtor;
    const hadRevoke = "revokeObjectURL" in urlCtor;
    const createObjectURL = vi.fn(() => "blob:zip");
    const revokeObjectURL = vi.fn();
    urlCtor.createObjectURL = createObjectURL;
    urlCtor.revokeObjectURL = revokeObjectURL;
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    try {
      await renderAdviser();
      fireEvent.change(screen.getByLabelText(/workload name/i), {
        target: { value: "Personal side project" },
      });
      fillFirstDemand();
      fireEvent.submit(screen.getByRole("form", { name: /describe your workload/i }));
      await screen.findByTestId("zero-cost-proof");

      // The download control is present, labelled, and states it is secret-free.
      const button = screen.getByRole("button", { name: /download deployment/i });
      expect(screen.getByText(/no secrets are ever included/i)).toBeInTheDocument();

      fireEvent.click(button);

      // The browser assembled a .zip (Blob URL created) and triggered a download.
      await waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
      expect(createObjectURL).toHaveBeenCalledTimes(1);
      // Manifest summary renders verbatim, confirming nothing was persisted.
      expect(await screen.findByText(/download has started/i)).toBeInTheDocument();
      expect(screen.getByText(/persisted nothing/i)).toBeInTheDocument();
      // Exactly one h1 remains after the download panel appears.
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    } finally {
      if (!hadCreate) delete urlCtor.createObjectURL;
      if (!hadRevoke) delete urlCtor.revokeObjectURL;
    }
  });

  it("renders the impossible-workload flow with a separated not-$0 section", async () => {
    await renderAdviser();
    // Workload name containing "saas" drives the mixed/impossible fixture.
    fireEvent.change(screen.getByLabelText(/workload name/i), {
      target: { value: "Growing SaaS" },
    });
    fillFirstDemand({ category: "relational-databases", metric: "storage", amount: "100" });
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload/i }));

    await screen.findByTestId("impossible-step-blocking");
    expect(screen.getByTestId("impossible-step-reduction")).toBeInTheDocument();
    expect(screen.getByTestId("impossible-step-recalculation")).toBeInTheDocument();
    expect(screen.getByTestId("impossible-step-selfhosting")).toBeInTheDocument();
    // Paid (Z1) option is isolated in the not-$0 section.
    const notFree = screen.getByTestId("not-free-section");
    expect(within(notFree).getByText("Acme SQL")).toBeInTheDocument();
  });

  it("shows an actionable error when the adviser API rejects the request", async () => {
    stubFetch((async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/adviser/recommend")) {
        return new Response("nope", { status: 422 });
      }
      return catalogueFetch()(input, init);
    }) as typeof fetch);
    window.location.hash = "#/adviser";
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: /architecture adviser/i });

    fillFirstDemand();
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/rejected by the API/i)).toBeInTheDocument();
  });
});

describe("App — assisted natural-language adviser (F007 slice 1)", () => {
  async function renderAssisted() {
    stubFetch(assistedFetch(catalogueFetch()));
    window.location.hash = "#/adviser";
    render(<App />);
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { level: 1, name: /architecture adviser/i }),
      ).toBeInTheDocument(),
    );
    // Switch from the default structured form to the natural-language tab.
    fireEvent.click(screen.getByRole("tab", { name: /describe in words/i }));
  }

  it("switches to the assisted tab and keeps a single h1 with a plain-text input", async () => {
    await renderAssisted();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(
      screen.getByRole("form", { name: /describe your workload in plain words/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /describe in words/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("interprets a description and renders the deterministic recommendation + provenance", async () => {
    await renderAssisted();
    fireEvent.change(screen.getByTestId("assisted-description"), {
      target: { value: "a small api with about 50000 invocations a day" },
    });
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload in plain words/i }));

    await screen.findByTestId("zero-cost-proof");
    // Provenance is honest: interpreted deterministically, no LLM, no external use.
    expect(screen.getByTestId("assisted-llm-used")).toHaveTextContent(/deterministic/i);
    expect(screen.getByTestId("assisted-fallback-reason")).toHaveTextContent(
      /deterministic_parser/i,
    );
    // The SAME deterministic recommendation renders below.
    expect(screen.getByText("Northwind Functions")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("reports honestly when nothing could be interpreted (never guesses)", async () => {
    await renderAssisted();
    fireEvent.change(screen.getByTestId("assisted-description"), {
      target: { value: "gibberish with no requirements" },
    });
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload in plain words/i }));

    await screen.findByTestId("assisted-uninterpreted");
    expect(screen.getByTestId("assisted-uninterpreted")).toHaveTextContent(/nothing was guessed/i);
    expect(screen.queryByTestId("zero-cost-proof")).not.toBeInTheDocument();
  });

  it("surfaces the 422 rejection when the description carries a URL", async () => {
    await renderAssisted();
    fireEvent.change(screen.getByTestId("assisted-description"), {
      target: { value: "fetch data from https://evil.example" },
    });
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload in plain words/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/rejected by the API/i)).toBeInTheDocument();
  });
});
