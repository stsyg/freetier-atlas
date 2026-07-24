import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { Z0Badge } from "./Z0Badge";
import { ConfidenceLabel } from "./ConfidenceLabel";
import { QuotaTable } from "./QuotaTable";
import { EvidenceList } from "./EvidenceList";
import { offerEvidence1 } from "./testFixtures";

afterEach(cleanup);

describe("Z0Badge", () => {
  it("pairs colour with a visible text label and a decorative icon", () => {
    render(<Z0Badge zeroCostClass="Z0_TRUE_FREE" />);
    const badge = screen.getByTestId("z0-badge");
    expect(badge).toHaveClass("badge--free");
    // Visible label text — meaning is not conveyed by colour alone.
    expect(badge).toHaveTextContent(/truly free/i);
    // The icon glyph is decorative and hidden from assistive tech.
    const icon = badge.querySelector(".badge__icon");
    expect(icon).toHaveAttribute("aria-hidden", "true");
  });

  it("renders an honest Unknown badge for a null class", () => {
    render(<Z0Badge zeroCostClass={null} />);
    expect(screen.getByTestId("z0-badge")).toHaveTextContent(/unknown/i);
  });
});

describe("ConfidenceLabel", () => {
  it("shows the label as primary and hides the numeric score in a closed disclosure", () => {
    render(
      <ConfidenceLabel label="high" advanced={{ score: 0.91, signals: { source_trust: 1 } }} />,
    );
    expect(screen.getByTestId("confidence-badge")).toHaveTextContent(/confidence: high/i);
    const advanced = screen.getByTestId("confidence-advanced");
    expect(advanced).not.toHaveAttribute("open");
    expect(within(advanced).getByTestId("confidence-score")).toHaveTextContent("0.91");
  });

  it("shows Unknown for a null numeric score", () => {
    render(<ConfidenceLabel label="unknown" advanced={{ score: null, signals: null }} />);
    expect(screen.getByTestId("confidence-score")).toHaveTextContent("Unknown");
  });
});

describe("QuotaTable", () => {
  it("renders an accessible table with a caption and row headers", () => {
    render(
      <QuotaTable
        quotas={[
          {
            metric: "requests_per_day",
            amount: 100000,
            unit: "requests",
            reset_period: "daily",
            scope: "account",
            region_scope: null,
            behaviour: "hard_limit",
            exhaustion_behaviour: "requests_blocked",
            retention_policy: null,
          },
        ]}
      />,
    );
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: /metric/i })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: /requests per day/i })).toBeInTheDocument();
  });

  it("degrades to an honest empty state when there are no quotas", () => {
    render(<QuotaTable quotas={[]} />);
    expect(screen.getByText(/no quota limits/i)).toBeInTheDocument();
  });
});

describe("EvidenceList", () => {
  it("marks evidence as official and links out safely", () => {
    render(<EvidenceList data={offerEvidence1} />);
    expect(screen.getByTestId("evidence-official")).toHaveTextContent(/official/i);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("shows an honest empty state when no evidence is attached", () => {
    render(
      <EvidenceList
        data={{
          offer_id: 9,
          offer_version_id: null,
          confidence_label: "unknown",
          advanced: { score: null, signals: null },
          evidence: [],
        }}
      />,
    );
    expect(screen.getByText(/no official evidence/i)).toBeInTheDocument();
  });
});
