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
 */
export interface CoverageMeaning {
  label: string;
  tone: "free" | "warn" | "info" | "unknown";
  icon: string;
}

const COVERAGE_MEANINGS: Record<string, CoverageMeaning> = {
  verified_free: { label: "Verified free", tone: "free", icon: "✓" },
  no_free_tier: { label: "No free tier", tone: "warn", icon: "✕" },
  not_offered: { label: "Not offered", tone: "unknown", icon: "–" },
};

const COVERAGE_FALLBACK: CoverageMeaning = { label: "Unknown", tone: "unknown", icon: "?" };

/** Map a coverage-state code onto its plain-language meaning (honest fallback). */
export function coverageMeaning(state: string | null | undefined): CoverageMeaning {
  if (!state) return COVERAGE_FALLBACK;
  return COVERAGE_MEANINGS[state] ?? COVERAGE_FALLBACK;
}
