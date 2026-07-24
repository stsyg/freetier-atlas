import { z0Meaning } from "./format";

/**
 * A plain-language Z0 (zero-cost class) badge.
 *
 * Accessibility: the badge NEVER signals meaning by colour alone. The colour
 * (via the `badge--<tone>` class) is always paired with a visible text label
 * and a decorative icon glyph. The raw class code is exposed as a `title` for
 * users who want the underlying identifier.
 */
export function Z0Badge({ zeroCostClass }: { zeroCostClass: string | null }) {
  const meaning = z0Meaning(zeroCostClass);
  return (
    <span
      className={`badge badge--${meaning.tone}`}
      title={zeroCostClass ?? "UNKNOWN"}
      data-testid="z0-badge"
    >
      <span className="badge__icon" aria-hidden="true">
        {meaning.icon}
      </span>
      <span className="badge__label">{meaning.label}</span>
    </span>
  );
}
