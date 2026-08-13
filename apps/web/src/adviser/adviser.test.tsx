import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AdviserForm } from "./AdviserForm";
import { AssistedForm } from "./AssistedForm";
import { RecommendationView } from "./RecommendationView";
import { mixedRecommendation, satisfiableRecommendation } from "./testFixtures";
import type { AssistedRequest, RecommendationRequest } from "../api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AdviserForm — structured requirements input (F006 slice 4)", () => {
  it("emits a typed structured request (no natural language, no URL)", () => {
    const onSubmit = vi.fn();
    render(<AdviserForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText(/workload name/i), {
      target: { value: "My project" },
    });
    fireEvent.change(screen.getByLabelText(/^category$/i), {
      target: { value: "serverless-functions" },
    });
    fireEvent.change(screen.getByLabelText(/^metric$/i), { target: { value: "invocations" } });
    fireEvent.change(screen.getByLabelText(/^amount$/i), { target: { value: "1000" } });
    fireEvent.change(screen.getByLabelText(/^unit$/i), { target: { value: "count" } });

    fireEvent.submit(screen.getByRole("form", { name: /describe your workload/i }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const request = onSubmit.mock.calls[0][0] as RecommendationRequest;
    expect(request.workload_name).toBe("My project");
    expect(request.requirements).toHaveLength(1);
    expect(request.requirements[0].category).toBe("serverless-functions");
    // Amount is preserved as an exact string, never a float round-trip.
    expect(request.requirements[0].demands[0]).toMatchObject({
      metric: "invocations",
      amount: "1000",
      unit: "count",
    });
  });

  it("blocks submission and lists errors when a demand is incomplete", () => {
    const onSubmit = vi.fn();
    render(<AdviserForm onSubmit={onSubmit} />);
    // Leave the single demand empty and submit.
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("form-errors")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/metric, amount, and unit/i);
  });

  it("supports repeatable requirements and demands (keyboard-operable buttons)", () => {
    const onSubmit = vi.fn();
    render(<AdviserForm onSubmit={onSubmit} />);
    expect(screen.getAllByTestId("requirement")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /add requirement/i }));
    expect(screen.getAllByTestId("requirement")).toHaveLength(2);

    const firstReq = screen.getAllByTestId("requirement")[0];
    expect(within(firstReq).getAllByTestId("demand-row")).toHaveLength(1);
    fireEvent.click(within(firstReq).getByRole("button", { name: /add demand/i }));
    expect(within(firstReq).getAllByTestId("demand-row")).toHaveLength(2);
  });

  it("shows a busy submit label while a recommendation is in flight", () => {
    render(<AdviserForm onSubmit={vi.fn()} disabled />);
    const submit = screen.getByRole("button", { name: /getting recommendation/i });
    expect(submit).toBeDisabled();
  });
});

describe("RecommendationView — satisfiable $0 architecture (F006 slice 4)", () => {
  it("renders the $0 proof and a provider-agnostic architecture verbatim", () => {
    render(<RecommendationView data={satisfiableRecommendation} />);

    // Whole-architecture $0 proof, exactly as returned.
    const proof = screen.getByTestId("zero-cost-proof");
    expect(proof).toHaveTextContent(/truly-free \(z0\) offer with a hard quota/i);

    // Multi-provider proof: two different vendors render side by side.
    const cards = screen.getAllByTestId("component-card");
    expect(cards).toHaveLength(2);
    expect(screen.getByText("Northwind Functions")).toBeInTheDocument();
    expect(screen.getByText("Initech Buckets")).toBeInTheDocument();

    // The $0 guarantee badge pairs colour + visible text + aria-hidden icon.
    const badge = screen.getByTestId("zero-cost-badge");
    expect(badge.className).toMatch(/badge--free/);
    expect(within(badge).getByText(/\$0 guaranteed/i)).toBeInTheDocument();
    expect(badge.querySelector(".badge__icon")).toHaveAttribute("aria-hidden", "true");
  });

  it("renders an accessible quota-math table (caption + column/row headers)", () => {
    render(<RecommendationView data={satisfiableRecommendation} />);
    const table = screen.getAllByTestId("quota-math-table")[0];
    expect(table.querySelector("caption")).toBeTruthy();
    expect(within(table).getByRole("columnheader", { name: /headroom/i })).toBeInTheDocument();
    // The per-demand row exposes the exact headroom returned by the API.
    const rows = within(table).getAllByTestId("quota-math-row");
    expect(within(rows[0]).getByRole("rowheader")).toBeInTheDocument();
  });

  it("keeps the numeric portability score inside a CLOSED advanced disclosure", () => {
    render(<RecommendationView data={satisfiableRecommendation} />);
    const advanced = screen.getAllByTestId("portability-advanced")[0];
    expect(advanced.tagName.toLowerCase()).toBe("details");
    expect(advanced.hasAttribute("open")).toBe(false);
    // The score lives only inside the disclosure, not in the primary summary.
    expect(within(advanced).getByTestId("portability-score")).toHaveTextContent("0.80");
  });

  it("reports Unknown honestly for null evidence fields (never guesses)", () => {
    render(<RecommendationView data={satisfiableRecommendation} />);
    // Initech Buckets evidence has null title + url → rendered as "Unknown", no link.
    expect(screen.getAllByText("Unknown").length).toBeGreaterThanOrEqual(1);
  });

  it("marks external evidence links rel=noopener noreferrer", () => {
    render(<RecommendationView data={satisfiableRecommendation} />);
    const link = screen.getByRole("link", { name: /northwind functions free plan/i });
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("starts its heading region at h2 (the page owns the single h1)", () => {
    render(<RecommendationView data={satisfiableRecommendation} />);
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/recommendation/i);
  });
});

describe("RecommendationView — impossible workload flow (F006 slice 4)", () => {
  it("renders the strict resolution order: blocking → reduction → recalculation → self-hosting", () => {
    render(<RecommendationView data={mixedRecommendation} />);

    const blocking = screen.getByTestId("impossible-step-blocking");
    const reduction = screen.getByTestId("impossible-step-reduction");
    const recalculation = screen.getByTestId("impossible-step-recalculation");
    const selfhosting = screen.getByTestId("impossible-step-selfhosting");

    // Document order is exactly 1 → 2 → 3 → 4.
    const order = [blocking, reduction, recalculation, selfhosting];
    for (let i = 0; i < order.length - 1; i += 1) {
      expect(order[i].compareDocumentPosition(order[i + 1])).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    }

    expect(blocking).toHaveTextContent(/no free \(z0\) relational database/i);
    expect(reduction).toHaveTextContent(/100 → 5 GB/);
    // Recalculation nests the recalculated component under reduced demand.
    expect(within(recalculation).getByTestId("component-card")).toBeInTheDocument();
    expect(within(recalculation).getByText("Globex Postgres")).toBeInTheDocument();
    expect(within(selfhosting).getByText(/PostgreSQL \(self-hosted\)/i)).toBeInTheDocument();
  });

  it("still renders fitting components even when the workload is not fully $0", () => {
    render(<RecommendationView data={mixedRecommendation} />);
    expect(
      screen.getByRole("heading", { name: /components that fit at \$0/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Northwind Functions")).toBeInTheDocument();
    const badge = screen.getByTestId("zero-cost-badge");
    expect(badge.className).toMatch(/badge--warn/);
  });

  it("isolates Z1/Z2 options in a clearly separated not-$0 section", () => {
    render(<RecommendationView data={mixedRecommendation} />);
    const notFree = screen.getByTestId("not-free-section");
    expect(notFree).toHaveTextContent(/not \$0/i);
    // The paid option lives ONLY inside the not-free section, never in the architecture.
    expect(within(notFree).getByText("Acme SQL")).toBeInTheDocument();
    const architecture = screen.getByRole("heading", {
      name: /components that fit at \$0/i,
    }).parentElement!;
    expect(within(architecture).queryByText("Acme SQL")).not.toBeInTheDocument();
  });

  it("keeps every heading level ordered (no skipped levels) across the deep tree", () => {
    render(<RecommendationView data={mixedRecommendation} />);
    const headings = screen.getAllByRole("heading");
    const levels = headings.map((h) => Number(h.tagName.slice(1)));
    // No heading skips more than one level from the previous one.
    for (let i = 1; i < levels.length; i += 1) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
    // Deepest nesting (recalculated component sections) never exceeds h6.
    expect(Math.max(...levels)).toBeLessThanOrEqual(6);
  });
});

describe("AssistedForm — natural-language intake + consent (F007 slice 1)", () => {
  it("emits a plain-text description and, by default, NO consent (deterministic path)", () => {
    const onSubmit = vi.fn();
    render(<AssistedForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByTestId("assisted-description"), {
      target: { value: "a small api with a postgres database" },
    });
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload in plain words/i }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const request = onSubmit.mock.calls[0][0] as AssistedRequest;
    expect(request.description).toBe("a small api with a postgres database");
    // No opt-in → consent omitted entirely (external providers stay skipped).
    expect(request.consent).toBeUndefined();
  });

  it("blocks submission of an empty description without calling the API", () => {
    const onSubmit = vi.fn();
    render(<AssistedForm onSubmit={onSubmit} />);
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload in plain words/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("assisted-error")).toHaveTextContent(/describe your workload/i);
  });

  it("shows a live character counter bounded by the configured limit", () => {
    render(<AssistedForm onSubmit={vi.fn()} maxCharacters={50} />);
    const textarea = screen.getByTestId("assisted-description") as HTMLTextAreaElement;
    expect(textarea).toHaveAttribute("maxLength", "50");
    fireEvent.change(textarea, { target: { value: "hello" } });
    expect(screen.getByTestId("assisted-counter")).toHaveTextContent("45 characters remaining");
  });

  it("only sends external-processing consent after an explicit, checkbox-gated opt-in", () => {
    const onSubmit = vi.fn();
    render(<AssistedForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByTestId("assisted-description"), {
      target: { value: "an api" },
    });

    // Open the consent dialog; the confirm button is disabled until acknowledged.
    fireEvent.click(screen.getByRole("button", { name: /enable external ai processing/i }));
    const modal = screen.getByTestId("consent-modal");
    expect(modal).toHaveAttribute("role", "dialog");
    expect(modal).toHaveAttribute("aria-modal", "true");
    // Warns against secrets/PII and links the provider policy safely.
    expect(within(modal).getByText(/do not include secrets/i)).toBeInTheDocument();
    const policy = within(modal).getByRole("link", { name: /provider.*policy/i });
    expect(policy).toHaveAttribute("rel", "noopener noreferrer");
    expect(policy).toHaveAttribute("target", "_blank");

    const confirm = screen.getByTestId("consent-confirm");
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByTestId("consent-checkbox"));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    // Consent state is reflected and the request now carries the explicit opt-in.
    expect(screen.getByTestId("consent-state")).toHaveTextContent(/enabled for your next request/i);
    fireEvent.submit(screen.getByRole("form", { name: /describe your workload in plain words/i }));
    const request = onSubmit.mock.calls[0][0] as AssistedRequest;
    expect(request.consent).toEqual({ external_processing: true });
  });

  it("lets the user cancel the consent dialog and stay on the deterministic path", () => {
    const onSubmit = vi.fn();
    render(<AssistedForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByTestId("assisted-description"), { target: { value: "an api" } });
    fireEvent.click(screen.getByRole("button", { name: /enable external ai processing/i }));
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(screen.queryByTestId("consent-modal")).not.toBeInTheDocument();

    fireEvent.submit(screen.getByRole("form", { name: /describe your workload in plain words/i }));
    const request = onSubmit.mock.calls[0][0] as AssistedRequest;
    expect(request.consent).toBeUndefined();
  });
});
