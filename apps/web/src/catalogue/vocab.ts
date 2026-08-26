/**
 * Closed filter vocabularies + plain-language labels for the catalogue browser.
 *
 * These option lists mirror the API's closed vocabularies
 * (`apps/api/app/models/vocab.py`) exactly, so a filter value the UI submits is
 * always one the API will accept. They exist only to render labels and
 * populate `<select>` controls — the UI never derives cost/Z0 behaviour from
 * them, and the category-matrix coverage `state` comes straight from the API.
 */

export interface Option {
  value: string;
  label: string;
}

/** Zero-cost classes (docs/DATA_MODEL.md → Zero-cost classes). */
export const ZERO_COST_CLASS_OPTIONS: Option[] = [
  { value: "Z0_TRUE_FREE", label: "Truly free (Z0)" },
  { value: "Z1_BILLING_EXPOSURE", label: "Billing risk (Z1)" },
  { value: "Z2_TEMPORARY_OR_CONDITIONAL", label: "Temporary or conditional (Z2)" },
  { value: "Z3_SELF_HOSTED_BUILDING_BLOCK", label: "Self-hosted building block (Z3)" },
  { value: "UNKNOWN", label: "Unknown" },
];

/**
 * Evidence-currency filter values.
 *
 * A dimension SEPARATE from the zero-cost class: the class classifies the
 * offer's terms, this filters on whether the official evidence behind them is
 * still inside its refresh window. The blank option ("any") is the default, so
 * an offer with expired evidence is shown and labelled rather than hidden.
 */
export const EVIDENCE_CURRENCY_OPTIONS: Option[] = [
  { value: "true", label: "Current evidence only" },
  { value: "false", label: "Expired or unverifiable" },
];

/** Offer types (docs/DATA_MODEL.md → Offer types). */
export const OFFER_TYPE_OPTIONS: Option[] = [
  { value: "always_free", label: "Always free" },
  { value: "recurring_quota", label: "Recurring quota" },
  { value: "new_customer_credit", label: "New-customer credit" },
  { value: "trial", label: "Trial" },
  { value: "startup_program", label: "Startup program" },
  { value: "student_program", label: "Student program" },
  { value: "open_source_program", label: "Open-source program" },
  { value: "hackathon_promotion", label: "Hackathon promotion" },
  { value: "personal_use_free", label: "Personal use free" },
  { value: "self_hosted_open_source", label: "Self-hosted open source" },
  { value: "other", label: "Other" },
];

/** Offer lifecycle status (docs/DATA_MODEL.md → Offer lifecycle status). */
export const STATUS_OPTIONS: Option[] = [
  { value: "active", label: "Active" },
  { value: "withdrawn", label: "Withdrawn" },
  { value: "deprecated", label: "Deprecated" },
  { value: "unknown", label: "Unknown" },
];

/** Commercial-use tri-state, rendered as a select whose blank option means "any". */
export const COMMERCIAL_USE_OPTIONS: Option[] = [
  { value: "true", label: "Allowed" },
  { value: "false", label: "Not allowed" },
];

/**
 * Coverage-state meaning for the category matrix. The `state` string is produced
 * by the API (never re-derived here); this maps it to a label, a colour tone
 * (paired with the label + an icon, never colour-only), and an icon glyph.
 *
 * All seven states in `apps/api/app/models/vocab.py::COVERAGE_STATES` are
 * mapped. `not_offered` and `unknown` are deliberately distinct: "the provider
 * does not offer this" is a declared claim with a stated reason, whereas
 * "unknown" means nobody has checked. Collapsing them would re-introduce exactly
 * the guess F008 removed.
 */
export interface CoverageMeaning {
  label: string;
  tone: "free" | "warn" | "info" | "unknown";
  icon: string;
  /** Plain-language explanation, surfaced in the legend and cell tooltips. */
  description: string;
}

export const COVERAGE_MEANINGS: Record<string, CoverageMeaning> = {
  verified_free: {
    label: "Verified free",
    tone: "free",
    icon: "✓",
    description: "An official source backs at least one truly free (Z0) offer here.",
  },
  offered_no_z0: {
    label: "Offered, not free",
    tone: "warn",
    icon: "✕",
    description: "The provider offers this category, but nothing in it is truly free.",
  },
  not_offered: {
    label: "Not offered",
    tone: "info",
    icon: "–",
    description: "The provider states it does not offer this category, with a reason.",
  },
  incomplete: {
    label: "Incomplete",
    tone: "warn",
    icon: "◐",
    description: "Something is published here, but the facts are not complete enough to classify.",
  },
  stale: {
    label: "Stale",
    tone: "warn",
    icon: "⏳",
    description: "The supporting evidence is past its refresh window and needs re-checking.",
  },
  conflicting: {
    label: "Conflicting",
    tone: "warn",
    icon: "⚠",
    description: "Sources or declarations disagree; a human review is pending.",
  },
  unknown: {
    label: "Unknown",
    tone: "unknown",
    icon: "?",
    description: "Nobody has verified this yet. Unknown is not the same as not offered.",
  },
};

/** The seven states in taxonomy-independent display order (used by the legend). */
export const COVERAGE_STATE_ORDER: string[] = [
  "verified_free",
  "offered_no_z0",
  "incomplete",
  "stale",
  "conflicting",
  "not_offered",
  "unknown",
];

const COVERAGE_FALLBACK: CoverageMeaning = COVERAGE_MEANINGS.unknown;

/** Map a coverage-state code onto its plain-language meaning (honest fallback). */
export function coverageMeaning(state: string | null | undefined): CoverageMeaning {
  if (!state) return COVERAGE_FALLBACK;
  return COVERAGE_MEANINGS[state] ?? COVERAGE_FALLBACK;
}
