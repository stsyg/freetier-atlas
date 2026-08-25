import type { EvidenceCurrency } from "../api";
import { currencyMeaning, formatDate, formatDays } from "./format";

/**
 * The read-time evidence-currency note shown next to any repeated Z0 claim.
 *
 * Why this component exists
 * -------------------------
 * A `Z0_TRUE_FREE` badge asserts "Usage stays at $0 with no billing risk." in
 * the present tense. That assertion rests on official evidence which has a
 * refresh window, and the classification itself is frozen at publish time — so
 * the badge alone cannot tell the reader whether the claim is still supported.
 * Rendering the badge without this note is what let a five-year-expired claim
 * display as simply "Truly free".
 *
 * It renders nothing when the evidence is current: a note on every healthy row
 * is noise, and noise is how people learn to ignore the warning that matters.
 *
 * Accessibility: never signals by colour alone — the tone class is always paired
 * with a visible text label and a decorative glyph, matching `Z0Badge`.
 */
export function EvidenceCurrencyNote({
  currency,
  compact = false,
}: {
  currency: EvidenceCurrency | null | undefined;
  compact?: boolean;
}) {
  const meaning = currencyMeaning(currency);
  if (!meaning.underminesClaim) return null;

  return (
    <p
      className={`currency-note currency-note--${meaning.tone}`}
      data-testid="evidence-currency-note"
      data-currency-state={currency?.stale ? "stale" : "unchecked"}
    >
      <span className={`badge badge--${meaning.tone}`}>
        <span className="badge__icon" aria-hidden="true">
          {meaning.icon}
        </span>
        <span className="badge__label">Evidence: {meaning.label}</span>
      </span>{" "}
      <span className="currency-note__text">
        {currency?.reason ?? meaning.description}
        {!compact && currency?.stale && currency.age_days !== null ? (
          <>
            {" "}
            Last fetched {formatDays(currency.age_days)} ago
            {currency.oldest_fetched_at ? ` (${formatDate(currency.oldest_fetched_at)})` : ""}
            {currency.window_days !== null
              ? `, against a ${formatDays(currency.window_days)} refresh window.`
              : "."}
          </>
        ) : null}
      </span>
    </p>
  );
}
