import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { Z0Badge } from "./Z0Badge";
import { ConfidenceLabel } from "./ConfidenceLabel";
import { EvidenceCurrencyNote } from "./EvidenceCurrencyNote";
import { QuotaTable } from "./QuotaTable";
import { EvidenceList } from "./EvidenceList";
import { formatSignal } from "./format";
import {
  CURRENT_EVIDENCE,
  offerEvidence1,
  STALE_EVIDENCE,
  UNCHECKED_EVIDENCE,
} from "./testFixtures";

afterEach(cleanup);

describe("Z0Badge", () => {
  it("pairs colour with a visible text label and a decorative icon", () => {
    render(<Z0Badge zeroCostClass="Z0_TRUE_FREE" currency={CURRENT_EVIDENCE} />);
    const badge = screen.getByTestId("z0-badge");
    expect(badge).toHaveClass("badge--free");
    // Visible label text — meaning is not conveyed by colour alone.
    expect(badge).toHaveTextContent(/truly free/i);
    // The icon glyph is decorative and hidden from assistive tech.
    const icon = badge.querySelector(".badge__icon");
    expect(icon).toHaveAttribute("aria-hidden", "true");
    expect(badge).toHaveAttribute("data-undermined", "false");
  });

  it("renders an honest Unknown badge for a null class", () => {
    render(<Z0Badge zeroCostClass={null} currency={CURRENT_EVIDENCE} />);
    expect(screen.getByTestId("z0-badge")).toHaveTextContent(/unknown/i);
  });

  it("qualifies a free claim in VISIBLE TEXT when the evidence has expired", () => {
    render(<Z0Badge zeroCostClass="Z0_TRUE_FREE" currency={STALE_EVIDENCE} />);
    const badge = screen.getByTestId("z0-badge");
    // The classification is still shown — hiding a genuinely free offer is its
    // own defect — but it no longer reads as a present-tense promise.
    expect(badge).toHaveTextContent(/truly free/i);
    expect(badge).toHaveTextContent(/not verified/i);
    // The qualifier is TEXT, not just a colour change: colour alone is not an
    // accessible signal.
    expect(badge).not.toHaveClass("badge--free");
    expect(badge).toHaveAttribute("data-undermined", "true");
  });

  it("qualifies a free claim when currency could not be checked at all", () => {
    render(<Z0Badge zeroCostClass="Z0_TRUE_FREE" currency={UNCHECKED_EVIDENCE} />);
    expect(screen.getByTestId("z0-badge")).toHaveTextContent(/not verified/i);
  });

  it("treats an ABSENT currency as not-checked rather than as permission", () => {
    // Fail-closed by default: a caller that forgets to pass currency must not
    // accidentally re-acquire the old unqualified "Truly free" badge.
    render(<Z0Badge zeroCostClass="Z0_TRUE_FREE" />);
    expect(screen.getByTestId("z0-badge")).toHaveAttribute("data-undermined", "true");
  });

  it("leaves a non-free class alone — only a FREE claim can be undermined", () => {
    render(<Z0Badge zeroCostClass="Z1_BILLING_EXPOSURE" currency={STALE_EVIDENCE} />);
    const badge = screen.getByTestId("z0-badge");
    expect(badge).toHaveTextContent(/billing risk/i);
    expect(badge).not.toHaveTextContent(/not verified/i);
  });
});

describe("EvidenceCurrencyNote", () => {
  it("renders nothing at all when the evidence is current", () => {
    // A warning on every healthy row is noise, and noise is how people learn to
    // ignore the warning that matters.
    const { container } = render(<EvidenceCurrencyNote currency={CURRENT_EVIDENCE} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("states the age, the window and the reason for an expired claim", () => {
    render(<EvidenceCurrencyNote currency={STALE_EVIDENCE} />);
    const note = screen.getByTestId("evidence-currency-note");
    expect(note).toHaveAttribute("data-currency-state", "stale");
    expect(note).toHaveTextContent(/no longer known to be current/i);
    expect(note).toHaveTextContent(/1824 days ago/i);
    expect(note).toHaveTextContent(/7 days refresh window/i);
  });

  it("distinguishes 'could not check' from 'expired'", () => {
    render(<EvidenceCurrencyNote currency={UNCHECKED_EVIDENCE} />);
    const note = screen.getByTestId("evidence-currency-note");
    expect(note).toHaveAttribute("data-currency-state", "unchecked");
    expect(note).toHaveTextContent(/cannot be established/i);
    // Absence of evidence is not evidence of expiry.
    expect(note).not.toHaveTextContent(/refresh window/i);
  });
});

describe("formatSignal — null is not zero", () => {
  it("renders an absent measurement as Unknown and a real zero as 0%", () => {
    // This is why the API must send null rather than 0.0 for an unchecked
    // claim: the two render as completely different statements.
    expect(formatSignal(null)).toBe("Unknown");
    expect(formatSignal(0)).toBe("0%");
    expect(formatSignal(undefined)).toBe("Unknown");
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
          evidence_currency: UNCHECKED_EVIDENCE,
        }}
      />,
    );
    expect(screen.getByText(/no official evidence/i)).toBeInTheDocument();
  });
});
