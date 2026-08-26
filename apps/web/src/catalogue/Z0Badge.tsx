import type { EvidenceCurrency } from "../api";
import { currencyMeaning, z0Meaning } from "./format";

/**
 * A plain-language Z0 (zero-cost class) badge.
 *
 * Accessibility: the badge NEVER signals meaning by colour alone. The colour
 * (via the `badge--<tone>` class) is always paired with a visible text label
 * and a decorative icon glyph. The raw class code is exposed as a `title` for
 * users who want the underlying identifier.
 *
 * Evidence currency
 * -----------------
 * The class is FROZEN at publish time; the evidence under it is not. When that
 * evidence is no longer known to be current, this badge must stop reading as a
 * present-tense promise — `Z0_TRUE_FREE` otherwise renders as "Truly free" with
 * a ✓ and the description "Usage stays at $0 with no billing risk.", which is
 * exactly the unsupported claim the product forbids.
 *
 * The classification is not hidden or rewritten: withdrawing a genuinely free
 * offer from view is its own defect. Instead the badge keeps the class, drops
 * the affirmative tone, and carries a VISIBLE qualifier — visible text, not
 * colour, because colour alone is not an accessible signal. The reason belongs
 * to the accompanying `EvidenceCurrencyNote`.
 */
export function Z0Badge({
  zeroCostClass,
  currency,
}: {
  zeroCostClass: string | null;
  currency?: EvidenceCurrency | null;
}) {
  const meaning = z0Meaning(zeroCostClass);
  const currencyMeta = currencyMeaning(currency);
  // Only a claim of being FREE can be undermined by expired evidence. A "billing
  // risk" or "unknown" badge is not a promise, so it is left alone. `currency`
  // is optional, and an omitted one is treated as "not checked" rather than as
  // permission — the same fail-closed default the API uses.
  const undermined = meaning.tone === "free" && currencyMeta.underminesClaim;
  const tone = undermined ? "warn" : meaning.tone;
  const icon = undermined ? currencyMeta.icon : meaning.icon;

  return (
    <span
      className={`badge badge--${tone}`}
      title={zeroCostClass ?? "UNKNOWN"}
      data-testid="z0-badge"
      data-undermined={undermined ? "true" : "false"}
    >
      <span className="badge__icon" aria-hidden="true">
        {icon}
      </span>
      <span className="badge__label">
        {meaning.label}
        {undermined ? " — not verified" : ""}
      </span>
    </span>
  );
}
