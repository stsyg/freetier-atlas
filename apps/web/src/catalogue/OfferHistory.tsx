import type { OfferHistoryResponse } from "../api";
import { formatDate, humanizeToken } from "./format";
import { Z0Badge } from "./Z0Badge";

/**
 * Offer version history + change events (scope item 5).
 *
 * The version history is append-only (immutable), so it reads newest-first as a
 * record of what the offer looked like over time. The change events explain what
 * changed and when (added / modified) and their materiality + publication state.
 */
export function OfferHistory({ data }: { data: OfferHistoryResponse }) {
  const versions = [...data.versions].sort((a, b) => b.version_number - a.version_number);
  const events = [...data.change_events];

  return (
    <div className="history" data-testid="offer-history">
      <div className="history__block">
        <h5 className="history__heading">Version history</h5>
        {versions.length === 0 ? (
          <p className="muted">No versions recorded.</p>
        ) : (
          <ol className="version-list">
            {versions.map((version) => (
              <li className="version" key={version.id}>
                <span className="version__num">v{version.version_number}</span>
                <Z0Badge zeroCostClass={version.zero_cost_class} />
                <span className="version__date">{formatDate(version.created_at)}</span>
                <span className="version__hash mono">{version.content_hash}</span>
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="history__block">
        <h5 className="history__heading">Change events</h5>
        {events.length === 0 ? (
          <p className="muted">No change events recorded.</p>
        ) : (
          <ul className="change-list">
            {events.map((event) => (
              <li className="change" key={event.id}>
                <span className="change__type">{humanizeToken(event.change_type)}</span>
                <span className="change__meta">
                  {humanizeToken(event.materiality)} · {humanizeToken(event.publication_status)}
                </span>
                <span className="change__date">{formatDate(event.occurred_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
