import type { Evidence, OfferEvidenceResponse } from "../api";
import { formatDate, humanizeToken, orUnknown } from "./format";

/**
 * Official evidence backing an offer (scope item 3).
 *
 * Each row makes clear the claim is official-evidence-backed: it shows the
 * source (adapter, trust level, official flag), a link to the official page,
 * and the captured snapshot provenance (content hash + fetch time). Links open
 * in a new tab with `rel="noopener noreferrer"`. URLs originate from the API's
 * published evidence rows — they are never entered by the user.
 */
export function EvidenceList({ data }: { data: OfferEvidenceResponse }) {
  if (data.evidence.length === 0) {
    return (
      <div className="evidence" data-testid="evidence">
        <p className="muted">No official evidence is attached to this offer yet.</p>
      </div>
    );
  }

  return (
    <ul className="evidence" data-testid="evidence">
      {data.evidence.map((row) => (
        <EvidenceRow key={row.id} row={row} />
      ))}
    </ul>
  );
}

function EvidenceRow({ row }: { row: Evidence }) {
  return (
    <li className="evidence__row">
      <div className="evidence__head">
        <span
          className={`badge ${row.official ? "badge--free" : "badge--warn"}`}
          data-testid="evidence-official"
        >
          <span className="badge__icon" aria-hidden="true">
            {row.official ? "✓" : "?"}
          </span>
          <span className="badge__label">
            {row.official ? "Official evidence" : "Unofficial"}
          </span>
        </span>
        <span className="evidence__title">{orUnknown(row.title)}</span>
      </div>

      {row.excerpt ? <p className="evidence__excerpt">“{row.excerpt}”</p> : null}

      <dl className="kv kv--compact">
        <div>
          <dt>Source</dt>
          <dd>
            {humanizeToken(row.source.adapter_type)} · {humanizeToken(row.source.trust_level)}
          </dd>
        </div>
        <div>
          <dt>Official page</dt>
          <dd>
            {row.url ? (
              <a href={row.url} target="_blank" rel="noopener noreferrer">
                {row.url}
              </a>
            ) : (
              "Unknown"
            )}
          </dd>
        </div>
        <div>
          <dt>Retrieved</dt>
          <dd>{formatDate(row.retrieved_at)}</dd>
        </div>
        <div>
          <dt>Snapshot captured</dt>
          <dd>{formatDate(row.snapshot.fetched_at)}</dd>
        </div>
        <div>
          <dt>Content hash</dt>
          <dd className="mono">{orUnknown(row.snapshot.content_hash)}</dd>
        </div>
      </dl>
    </li>
  );
}
