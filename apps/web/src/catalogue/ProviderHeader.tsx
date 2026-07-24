import type { ProviderDetail } from "../api";
import { formatSignal, humanizeToken } from "./format";

/**
 * The provider header: identity + completeness/freshness + official domains.
 *
 * Completeness and freshness are surfaced here (scope item 6) so the reader can
 * judge how complete and fresh the underlying data is. Absent values render as
 * an honest "Unknown".
 */
export function ProviderHeader({ provider }: { provider: ProviderDetail }) {
  return (
    <header className="hero" data-testid="provider-header">
      <p className="eyebrow">Provider</p>
      <h1>{provider.name}</h1>
      <p className="tagline">
        Evidence-backed free-tier catalogue for {provider.name}. Every claim below is drawn from
        official sources and shown with its confidence and provenance.
      </p>

      <dl className="details" aria-label="Provider summary">
        <div>
          <dt>Type</dt>
          <dd>{humanizeToken(provider.type)}</dd>
        </div>
        <div>
          <dt>Published offers</dt>
          <dd>{provider.published_offer_count}</dd>
        </div>
        <div>
          <dt>Services tracked</dt>
          <dd>{provider.service_count}</dd>
        </div>
        <div>
          <dt>Data completeness</dt>
          <dd>{formatSignal(provider.completeness)}</dd>
        </div>
        <div>
          <dt>Data freshness</dt>
          <dd>{formatSignal(provider.freshness)}</dd>
        </div>
        <div>
          <dt>Source health</dt>
          <dd>{humanizeToken(provider.source_health)}</dd>
        </div>
      </dl>

      {provider.official_domains.length > 0 ? (
        <p className="domains">
          <span className="domains__label">Official domains:</span>{" "}
          {provider.official_domains.join(", ")}
        </p>
      ) : null}
    </header>
  );
}
