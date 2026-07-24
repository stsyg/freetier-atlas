import type { ConfidenceAdvanced } from "../api";
import { confidenceMeaning, humanizeToken, orUnknown } from "./format";

/**
 * The confidence signal for an offer.
 *
 * Per decision D039 the PRIMARY confidence field is a plain-language label. The
 * raw numeric score and the deterministic signals appear ONLY inside the
 * collapsed `<details>` "advanced" disclosure, never as the primary field. The
 * `<details>`/`<summary>` element is natively keyboard-accessible.
 */
export function ConfidenceLabel({
  label,
  advanced,
}: {
  label: string;
  advanced?: ConfidenceAdvanced | null;
}) {
  const meaning = confidenceMeaning(label);
  const signals = advanced?.signals ?? null;
  const signalEntries = signals ? Object.entries(signals) : [];

  return (
    <div className="confidence">
      <p className="confidence__primary">
        <span className={`badge badge--${meaning.tone}`} data-testid="confidence-badge">
          <span className="badge__icon" aria-hidden="true">
            ◆
          </span>
          <span className="badge__label">Confidence: {meaning.label}</span>
        </span>
      </p>
      <p className="confidence__desc">{meaning.description}</p>

      {advanced ? (
        <details className="advanced" data-testid="confidence-advanced">
          <summary>Advanced: confidence score &amp; signals</summary>
          <dl className="kv">
            <div>
              <dt>Numeric score</dt>
              <dd data-testid="confidence-score">{orUnknown(advanced.score)}</dd>
            </div>
            {signalEntries.length === 0 ? (
              <div>
                <dt>Signals</dt>
                <dd>Unknown</dd>
              </div>
            ) : (
              signalEntries.map(([key, value]) => (
                <div key={key}>
                  <dt>{humanizeToken(key)}</dt>
                  <dd>{orUnknown(value)}</dd>
                </div>
              ))
            )}
          </dl>
        </details>
      ) : null}
    </div>
  );
}
