/**
 * Plain-language formatting helpers for the catalogue UI.
 *
 * These pure functions map the API's internal vocabulary (Z0 classes,
 * confidence labels, 0..1 signal scores, ISO timestamps, tri-state booleans)
 * onto human-readable text. They embody two product rules:
 *
 * - **Simple labels by default (D039).** Codes become plain language.
 * - **Unknown is better than guessed.** A `null`/absent value becomes the
 *   honest string `"Unknown"` — never a fabricated value.
 */

/** A visual/semantic tone shared by badges and CSS. Not a colour on its own. */
export type Tone = "free" | "warn" | "info" | "unknown";

export interface Z0Meaning {
  /** Short plain-language label shown inside the badge. */
  label: string;
  /** A one-line explanation of what the class means for cost. */
  description: string;
  /** Semantic tone (drives colour AND is paired with the visible label). */
  tone: Tone;
  /** A short text glyph so the badge never relies on colour alone. */
  icon: string;
}

const Z0_MEANINGS: Record<string, Z0Meaning> = {
  Z0_TRUE_FREE: {
    label: "Truly free",
    description: "Usage stays at $0 with no billing risk.",
    tone: "free",
    icon: "✓",
  },
  Z1_BILLING_EXPOSURE: {
    label: "Billing risk",
    description: "Free to start, but usage can incur charges.",
    tone: "warn",
    icon: "!",
  },
  Z2_TEMPORARY_OR_CONDITIONAL: {
    label: "Temporary or conditional",
    description: "Free only for a limited time or under conditions.",
    tone: "warn",
    icon: "~",
  },
  Z3_SELF_HOSTED_BUILDING_BLOCK: {
    label: "Self-hosted building block",
    description: "Free software you host yourself; infrastructure costs may apply.",
    tone: "info",
    icon: "⚙",
  },
  UNKNOWN: {
    label: "Unknown",
    description: "Not enough verified evidence to classify the cost.",
    tone: "unknown",
    icon: "?",
  },
};

const Z0_FALLBACK: Z0Meaning = {
  label: "Unknown",
  description: "Not enough verified evidence to classify the cost.",
  tone: "unknown",
  icon: "?",
};

/** Map a raw Z0 class code onto its plain-language meaning (honest fallback). */
export function z0Meaning(zeroCostClass: string | null | undefined): Z0Meaning {
  if (!zeroCostClass) return Z0_FALLBACK;
  return Z0_MEANINGS[zeroCostClass] ?? Z0_FALLBACK;
}

export interface ConfidenceMeaning {
  label: string;
  description: string;
  tone: Tone;
}

const CONFIDENCE_MEANINGS: Record<string, ConfidenceMeaning> = {
  high: {
    label: "High",
    description: "Strong, fresh official evidence backs this offer.",
    tone: "free",
  },
  medium: {
    label: "Medium",
    description: "Reasonable official evidence, with some gaps.",
    tone: "info",
  },
  low: {
    label: "Low",
    description: "Limited or ageing evidence; treat with caution.",
    tone: "warn",
  },
  unknown: {
    label: "Unknown",
    description: "Not enough signal to judge confidence.",
    tone: "unknown",
  },
};

const CONFIDENCE_FALLBACK: ConfidenceMeaning = CONFIDENCE_MEANINGS.unknown;

/** Map a raw confidence label onto its plain-language meaning. */
export function confidenceMeaning(label: string | null | undefined): ConfidenceMeaning {
  if (!label) return CONFIDENCE_FALLBACK;
  return CONFIDENCE_MEANINGS[label.toLowerCase()] ?? CONFIDENCE_FALLBACK;
}

/** Format a 0..1 signal score as a percentage, or "Unknown" when absent. */
export function formatSignal(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Unknown";
  }
  return `${Math.round(value * 100)}%`;
}

export interface CurrencyMeaning {
  /** Short label shown in the badge. */
  label: string;
  /** One-line explanation of what it means for the claim above it. */
  description: string;
  tone: Tone;
  icon: string;
  /** Whether this state undermines a "free" claim rendered alongside it. */
  underminesClaim: boolean;
}

/**
 * Map an API `evidence_currency` block onto plain language.
 *
 * Reuses the vocabulary the category matrix already ships for the same concept
 * (`COVERAGE_MEANINGS.stale`: "Stale", ⏳, "past its refresh window and needs
 * re-checking") rather than inventing a second visual language for staleness.
 *
 * The three states are kept distinct on purpose:
 *
 * - **current** — checked, and inside its window.
 * - **stale** — we looked, and it expired.
 * - **unchecked** — we could not look at all. Deliberately NOT reported as
 *   stale: absence of evidence is not evidence of expiry. It still undermines a
 *   free claim, because "we cannot check" is not permission.
 */
export function currencyMeaning(
  currency: { current: boolean; checked: boolean; stale: boolean } | null | undefined,
): CurrencyMeaning {
  if (!currency || (!currency.checked && !currency.stale)) {
    return {
      label: "Not checked",
      description:
        "No official evidence with a checkable fetch time backs this, so whether it is still current cannot be established.",
      tone: "unknown",
      icon: "?",
      underminesClaim: true,
    };
  }
  if (currency.stale) {
    return {
      label: "Stale",
      description: "The supporting evidence is past its refresh window and needs re-checking.",
      tone: "warn",
      icon: "⏳",
      underminesClaim: true,
    };
  }
  return {
    label: "Current",
    description: "The supporting official evidence is still inside its refresh window.",
    tone: "free",
    icon: "✓",
    underminesClaim: false,
  };
}

/** Format a whole number of days, or "Unknown" when absent. */
export function formatDays(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "Unknown";
  const days = Math.floor(value);
  if (days < 1) return "less than a day";
  return `${days} day${days === 1 ? "" : "s"}`;
}

/** Format an arbitrary value as text, or "Unknown" when absent. */
export function orUnknown(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "Unknown";
  return String(value);
}

/** Format a tri-state boolean (true/false/unknown) as plain language. */
export function formatTriState(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return "Unknown";
  return value ? "Yes" : "No";
}

/** Format an ISO timestamp as a readable date, or "Unknown" when absent. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return orUnknown(value);
  return parsed.toISOString().slice(0, 10);
}

/** Turn a snake_case / kebab token into readable Title Case. */
export function humanizeToken(token: string | null | undefined): string {
  if (!token) return "Unknown";
  const spaced = token.replace(/[_-]+/g, " ").trim();
  if (!spaced) return "Unknown";
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
