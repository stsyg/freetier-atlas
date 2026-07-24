import type { OfferDetail, OfferEvidenceResponse, OfferHistoryResponse } from "../api";
import { formatDate, formatSignal, formatTriState, humanizeToken, z0Meaning } from "./format";
import { Z0Badge } from "./Z0Badge";
import { ConfidenceLabel } from "./ConfidenceLabel";
import { QuotaTable } from "./QuotaTable";
import { EvidenceList } from "./EvidenceList";
import { OfferHistory } from "./OfferHistory";

export interface OfferBundle {
  detail: OfferDetail;
  evidence: OfferEvidenceResponse;
  history: OfferHistoryResponse;
}

/**
 * A single published offer, rendering scope items 2–7:
 *
 * 2. Z0 badge + plain-language WHY (reasons) + blocking conditions
 * 3. official evidence (via EvidenceList)
 * 4. confidence LABEL primary; numeric only in advanced (via ConfidenceLabel)
 * 5. version history + change events (via OfferHistory)
 * 6. completeness + freshness signals
 * 7. quota rows (via QuotaTable)
 */
export function OfferCard({ bundle }: { bundle: OfferBundle }) {
  const { detail, evidence, history } = bundle;
  const headingId = `offer-heading-${detail.offer_id}`;
  const meaning = z0Meaning(detail.zero_cost_class);

  return (
    <article
      className="card offer"
      id={`offer-${detail.offer_id}`}
      aria-labelledby={headingId}
      data-testid="offer-card"
    >
      <div className="offer__head">
        <h3 id={headingId}>
          {detail.service_name} — {humanizeToken(detail.offer_type)}
        </h3>
        <Z0Badge zeroCostClass={detail.zero_cost_class} />
      </div>
      <p className="offer__z0desc">{meaning.description}</p>

      <section className="offer__section" aria-label="Why this rating">
        <h4>Why this rating</h4>
        {detail.reasons.length === 0 ? (
          <p className="muted">No reasons were recorded for this classification.</p>
        ) : (
          <ul className="reasons" data-testid="offer-reasons">
            {detail.reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
        )}
        {detail.blocking_conditions.length > 0 ? (
          <>
            <h4>Blocking conditions</h4>
            <ul className="reasons reasons--blocking" data-testid="offer-blocking">
              {detail.blocking_conditions.map((condition, index) => (
                <li key={index}>{condition}</li>
              ))}
            </ul>
          </>
        ) : null}
      </section>

      <section className="offer__section" aria-label="Quota limits">
        <h4>Quota limits</h4>
        <QuotaTable quotas={detail.quotas} />
      </section>

      <section className="offer__section" aria-label="Confidence">
        <h4>Confidence</h4>
        <ConfidenceLabel label={detail.confidence_label} advanced={detail.advanced} />
      </section>

      <section className="offer__section" aria-label="Data signals">
        <h4>Data signals</h4>
        <dl className="kv">
          <div>
            <dt>Completeness</dt>
            <dd>{formatSignal(detail.completeness)}</dd>
          </div>
          <div>
            <dt>Freshness</dt>
            <dd>{formatSignal(detail.freshness)}</dd>
          </div>
          <div>
            <dt>Requires a card</dt>
            <dd>{formatTriState(detail.requires_card)}</dd>
          </div>
          <div>
            <dt>Paid dependencies</dt>
            <dd>{formatTriState(detail.has_paid_dependencies)}</dd>
          </div>
          <div>
            <dt>Commercial use</dt>
            <dd>{formatTriState(detail.commercial_use_allowed)}</dd>
          </div>
          <div>
            <dt>Last verified</dt>
            <dd>{formatDate(detail.last_verified_at)}</dd>
          </div>
        </dl>
      </section>

      <section className="offer__section" aria-label="Official evidence">
        <h4>Official evidence</h4>
        <EvidenceList data={evidence} />
      </section>

      <section className="offer__section" aria-label="History">
        <h4>History</h4>
        <OfferHistory data={history} />
      </section>
    </article>
  );
}
