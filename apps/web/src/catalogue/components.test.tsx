import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { OfferDetail } from "../api";
import { Z0Badge } from "./Z0Badge";
import { ConfidenceLabel } from "./ConfidenceLabel";
import { EvidenceCurrencyNote } from "./EvidenceCurrencyNote";
import { OfferCard } from "./OfferCard";
import { QuotaTable } from "./QuotaTable";
import { EvidenceList } from "./EvidenceList";
import { CategoryMatrix } from "./CategoryMatrix";
import { describeFreeCount, formatDays, formatSignal } from "./format";
import {
  allCoverageStatesMatrix,
  CURRENT_EVIDENCE,
  offerDetail1,
  offerEvidence1,
  offerHistory1,
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

describe("OfferCard — the $0 promise is gated by ONE shared rule", () => {
  // The promise text lives in format.ts as the Z0_TRUE_FREE description. It is
  // an unconditional, present-tense claim, so it is the exact string that must
  // not appear beside a claim we cannot vouch for.
  const PROMISE = "Usage stays at $0 with no billing risk.";

  function renderCard(detail: OfferDetail) {
    render(<OfferCard bundle={{ detail, evidence: offerEvidence1, history: offerHistory1 }} />);
    return screen.getByTestId("offer-card");
  }

  // The card renders the head badge PLUS one badge per version row inside
  // OfferHistory, so a lookup has to say which it means. Index 0 is the head
  // badge -- the one sitting beside the description gate under test.
  const headBadge = (card: HTMLElement) => within(card).getAllByTestId("z0-badge")[0];

  it("PAIRED CONTROL: renders the promise when the evidence IS current", () => {
    // Without this arm, the two assertions below could equally be satisfied by
    // a gate that suppresses the promise unconditionally -- i.e. by a component
    // that had simply stopped working.
    const card = renderCard({ ...offerDetail1, evidence_currency: CURRENT_EVIDENCE });
    expect(card).toHaveTextContent(PROMISE);
    expect(headBadge(card)).not.toHaveTextContent(/not verified/i);
  });

  it("withholds the promise when the evidence has EXPIRED", () => {
    const card = renderCard({ ...offerDetail1, evidence_currency: STALE_EVIDENCE });
    expect(card).not.toHaveTextContent(PROMISE);
    expect(headBadge(card)).toHaveTextContent(/not verified/i);
  });

  it("withholds the promise when the currency block is ABSENT (L2 blocker)", () => {
    // Reachability, so this is not mistaken for defensive decoration: the api
    // and web images are built and deployed SEPARATELY (docker-compose.yml). An
    // api image predating evidence_currency omits the field from its catalogue
    // responses entirely, while a newer web bundle still renders this component.
    // A required TypeScript field constrains what we COMPILE against, never what
    // an older server actually SENDS -- so the cast below simulates a real
    // runtime population, not an impossible one.
    const legacy = { ...offerDetail1 } as Partial<OfferDetail>;
    delete legacy.evidence_currency;

    const card = renderCard(legacy as OfferDetail);

    expect(card).not.toHaveTextContent(PROMISE);
    // And the two gates must now AGREE: previously the badge said "not verified"
    // while the description simultaneously promised $0, in the same card.
    expect(headBadge(card)).toHaveTextContent(/not verified/i);
    expect(within(card).getByTestId("evidence-currency-note")).toHaveAttribute(
      "data-currency-state",
      "unchecked",
    );
  });

  it("the badge gate and the description gate share ONE definition", () => {
    // The defect was not that a gate was wrong; it was that the rule existed
    // TWICE, in two forms, and the two forms disagreed on the absent case. Pin
    // agreement across all three currency states rather than re-asserting the
    // rule a third time here.
    for (const currency of [CURRENT_EVIDENCE, STALE_EVIDENCE, UNCHECKED_EVIDENCE]) {
      cleanup();
      const card = renderCard({ ...offerDetail1, evidence_currency: currency });
      const badgeSaysUnverified = /not verified/i.test(headBadge(card).textContent ?? "");
      const promiseShown = (card.textContent ?? "").includes(PROMISE);
      expect(promiseShown).toBe(!badgeSaysUnverified);
    }
  });
});

describe("CategoryMatrix — a free-offer COUNT is a claim (F008 S7)", () => {
  // "12 truly free" asserts something about 12 offers in the present tense,
  // exactly as a badge does. These pin BOTH directions: the count must stop
  // asserting more than the evidence supports, AND it must not silently shrink,
  // because a wrongly-withheld free offer is a defect of equal severity and an
  // omission is invisible to a reader in a way a label is not.

  it("says how many are still evidenced when some are not", () => {
    expect(describeFreeCount({ free_offer_count: 12, current_free_offer_count: 9 })).toBe(
      "12 truly free, 9 still evidenced",
    );
  });

  it("does not qualify a count whose evidence is entirely current", () => {
    // No note on a healthy row: noise is how people learn to ignore the warning
    // that matters.
    expect(describeFreeCount({ free_offer_count: 12, current_free_offer_count: 12 })).toBe(
      "12 truly free",
    );
  });

  it("says plainly when NONE of the counted offers are still evidenced", () => {
    expect(describeFreeCount({ free_offer_count: 3, current_free_offer_count: 0 })).toBe(
      "3 truly free, none still evidenced",
    );
  });

  it("never reduces the total to the still-evidenced subset", () => {
    // The regression this whole slice exists to avoid re-introducing: the "12"
    // must survive, or three genuinely free offers vanish from the page.
    const text = describeFreeCount({ free_offer_count: 12, current_free_offer_count: 9 });
    expect(text).toContain("12");
    expect(text).not.toBe("9 truly free");
  });

  it("qualifies the count in a stale cell's tooltip rather than asserting it", () => {
    render(<CategoryMatrix data={allCoverageStatesMatrix} />);
    const badges = screen.getAllByTestId("coverage-badge");
    const stale = badges.find((b) => b.getAttribute("data-state") === "stale");
    expect(stale).toBeDefined();
    // The fixture cell counts one free offer whose evidence has expired.
    expect(stale).toHaveAttribute("data-free-count", "1");
    expect(stale).toHaveAttribute("data-current-free-count", "0");
    expect(stale?.getAttribute("title")).toContain("none still evidenced");
  });

  it("PAIRED CONTROL: a current cell's tooltip still asserts its free count", () => {
    render(<CategoryMatrix data={allCoverageStatesMatrix} />);
    const badges = screen.getAllByTestId("coverage-badge");
    const free = badges.find((b) => b.getAttribute("data-state") === "verified_free");
    expect(free).toBeDefined();
    expect(free).toHaveAttribute("data-free-count", "1");
    expect(free).toHaveAttribute("data-current-free-count", "1");
    expect(free?.getAttribute("title")).toContain("1 truly free");
    expect(free?.getAttribute("title")).not.toContain("still evidenced");
  });
});

describe("CategoryMatrix — the uncategorised rollup carries its own currency", () => {
  const rollupMatrix = (
    current_free_offer_count: number,
    evidence_currency: typeof CURRENT_EVIDENCE,
  ) => ({
    provider_slugs: ["northwind-cloud"],
    categories: [],
    uncategorized: [
      {
        provider_slug: "northwind-cloud",
        provider_name: "Northwind Cloud",
        published_offer_count: 4,
        free_offer_count: 2,
        current_free_offer_count,
        evidence_currency,
      },
    ],
  });

  it("qualifies the rollup count and shows the shipped currency note when stale", () => {
    render(<CategoryMatrix data={rollupMatrix(0, STALE_EVIDENCE)} />);
    const row = screen.getByTestId("uncategorized-row");
    expect(row).toHaveTextContent("4 published");
    expect(row).toHaveTextContent("2 truly free, none still evidenced");
    // The SAME note every other repeated free claim uses — not a second visual
    // language invented for this surface.
    expect(within(row).getByTestId("evidence-currency-note")).toHaveAttribute(
      "data-currency-state",
      "stale",
    );
  });

  it("PAIRED CONTROL: a current rollup asserts its count with no note at all", () => {
    render(<CategoryMatrix data={rollupMatrix(2, CURRENT_EVIDENCE)} />);
    const row = screen.getByTestId("uncategorized-row");
    expect(row).toHaveTextContent("2 truly free");
    expect(row).not.toHaveTextContent("still evidenced");
    expect(within(row).queryByTestId("evidence-currency-note")).toBeNull();
  });

  it("treats an UNCHECKED rollup as unsupported, not as fresh", () => {
    // "We could not look" is not permission. It must not read as current.
    render(<CategoryMatrix data={rollupMatrix(0, UNCHECKED_EVIDENCE)} />);
    const row = screen.getByTestId("uncategorized-row");
    expect(row).toHaveTextContent("none still evidenced");
    expect(within(row).getByTestId("evidence-currency-note")).toHaveAttribute(
      "data-currency-state",
      "unchecked",
    );
  });
});

describe("describeFreeCount — an ABSENT count is not a number (L2 blocker)", () => {
  // Reachability, so this is not mistaken for defensive decoration: the api and
  // web images are built and deployed SEPARATELY (docker-compose.yml). An api
  // image predating this slice serves a coverage cell carrying
  // `free_offer_count` and NOT `current_free_offer_count`, while a newer web
  // bundle renders it. `getJson` ends in `as T` with no runtime normaliser, so a
  // required TypeScript field constrains what we COMPILE against and never what
  // an older server actually SENDS.
  //
  // This is the SAME payload and the SAME skew route the shipped sibling test
  // "withholds the promise when the currency block is ABSENT (L2 blocker)"
  // already guards for `evidence_currency`. Guarding one field on a payload and
  // not its neighbour is the two-gates-disagreeing shape that failed PR #95.

  it("withholds the evidenced count when the field is ABSENT (L2 blocker)", () => {
    const legacy = { free_offer_count: 1 } as {
      free_offer_count: number;
      current_free_offer_count: number;
    };

    const text = describeFreeCount(legacy);

    // The defect: "1 truly free, undefined still evidenced" reached visible body
    // text and the badge tooltip in real Chromium at 1400px and 390px.
    expect(text).not.toContain("undefined");
    expect(text).not.toContain("NaN");
    // "We could not look" is not permission, so it must not read as an
    // unqualified free claim either.
    expect(text).not.toBe("1 truly free");
    // ...and it reuses the SHIPPED "not checked" wording rather than a new one.
    expect(text).toBe("1 truly free, evidence not checked");
  });

  it("withholds it for null, and for a missing total, and for NaN", () => {
    expect(describeFreeCount({ free_offer_count: 3, current_free_offer_count: null })).toBe(
      "3 truly free, evidence not checked",
    );
    expect(describeFreeCount({ free_offer_count: 3, current_free_offer_count: NaN })).toBe(
      "3 truly free, evidence not checked",
    );
    expect(describeFreeCount({ current_free_offer_count: 2 })).toBe("truly free count unknown");
    expect(describeFreeCount({})).toBe("truly free count unknown");
  });

  it("matches its neighbours in format.ts, which all guard null and undefined", () => {
    // formatDays, orUnknown and formatTriState each handle null|undefined
    // explicitly. describeFreeCount was the only formatter in the file without
    // the guard; that asymmetry is the finding.
    expect(formatDays(null)).toBe("Unknown");
    expect(formatDays(undefined)).toBe("Unknown");
    expect(describeFreeCount({ free_offer_count: 1, current_free_offer_count: undefined })).toBe(
      "1 truly free, evidence not checked",
    );
  });

  it("PAIRED CONTROL: a complete payload is still asserted in full", () => {
    // A guard that cannot be shown to PERMIT is indistinguishable from one that
    // broke the product.
    expect(describeFreeCount({ free_offer_count: 2, current_free_offer_count: 2 })).toBe(
      "2 truly free",
    );
    expect(describeFreeCount({ free_offer_count: 2, current_free_offer_count: 1 })).toBe(
      "2 truly free, 1 still evidenced",
    );
    expect(describeFreeCount({ free_offer_count: 0, current_free_offer_count: 0 })).toBe(
      "0 truly free",
    );
  });

  it("renders no 'undefined' anywhere on a base-vintage matrix payload", () => {
    // End to end through the real component, not just the formatter: strip the
    // two head-only fields from every cell and rollup, exactly as an older api
    // image would serve them, then assert on the rendered DOM.
    const legacy = JSON.parse(JSON.stringify(allCoverageStatesMatrix));
    for (const row of legacy.categories) {
      for (const cell of row.providers) {
        delete cell.current_free_offer_count;
        delete cell.evidence_currency;
      }
    }
    legacy.uncategorized = [
      {
        provider_slug: "legacy-co",
        provider_name: "Legacy Co",
        published_offer_count: 4,
        free_offer_count: 2,
      },
    ];

    render(<CategoryMatrix data={legacy} />);

    const rendered = document.body.textContent ?? "";
    expect(rendered).not.toContain("undefined");
    expect(rendered).not.toContain("NaN");

    const row = screen.getByTestId("uncategorized-row");
    expect(row).toHaveTextContent("2 truly free, evidence not checked");
    // The rollup's sibling gate agrees: an absent currency block is "not checked".
    expect(within(row).getByTestId("evidence-currency-note")).toHaveAttribute(
      "data-currency-state",
      "unchecked",
    );

    for (const badge of screen.getAllByTestId("coverage-badge")) {
      expect(badge.getAttribute("title") ?? "").not.toContain("undefined");
    }
  });
});
