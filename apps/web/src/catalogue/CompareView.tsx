import type { CompareOffer, CompareResponse, NormalizedQuota } from "../api";
import {
  confidenceMeaning,
  formatSignal,
  formatTriState,
  humanizeToken,
  orUnknown,
} from "./format";
import { Z0Badge } from "./Z0Badge";

/**
 * Side-by-side comparison of the selected 2–3 offers (scope: COMPARE view).
 *
 * Rendered as an accessible `<table>` with a caption, a column header per offer
 * (provider · service) and a row header per attribute. Rows cover the Z0 class
 * (as a colour+text+icon badge), normalized quotas (shown exactly as the API
 * reports them — honest "Unknown" when it could not normalize a unit, never a
 * guessed conversion), the requires-card / paid-dependency / commercial /
 * personal-use flags, the plain-language confidence LABEL (numeric only inside a
 * closed advanced `<details>`), completeness/freshness, and an evidence-count
 * linkage. Absent fields render honestly as "Unknown".
 */
export function CompareView({ data }: { data: CompareResponse }) {
  if (data.offers.length === 0) {
    return (
      <section className="card" aria-labelledby="compare-heading">
        <h2 id="compare-heading">Compare offers</h2>
        <p className="muted" data-testid="compare-empty">
          Select two or three offers from the results to compare them side by side.
        </p>
      </section>
    );
  }

  const offers = data.offers;

  return (
    <section className="card" aria-labelledby="compare-heading">
      <h2 id="compare-heading">Compare offers</h2>
      <p className="muted">
        A normalized side-by-side of the offers you selected. Values we cannot verify or normalize
        are shown honestly as “Unknown”.
      </p>

      <div className="compare__scroll">
        <table className="compare-table" data-testid="compare-table">
          <caption className="sr-only">Side-by-side comparison of the selected offers</caption>
          <thead>
            <tr>
              <th scope="col">Attribute</th>
              {offers.map((offer) => (
                <th scope="col" key={offer.offer_id}>
                  {offer.provider_name}
                  <span className="compare__service">{offer.service_name}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Zero-cost class</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>
                  <Z0Badge zeroCostClass={offer.zero_cost_class} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Category</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>
                  {offer.category ? offer.category.name : "Uncategorised"}
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Offer type</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>{humanizeToken(offer.offer_type)}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Requires a card</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>{formatTriState(offer.requires_card)}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Paid dependencies</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>{formatTriState(offer.has_paid_dependencies)}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Commercial use</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>{formatTriState(offer.commercial_use_allowed)}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Personal use</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>{formatTriState(offer.personal_use_allowed)}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Quotas</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>
                  <QuotaCell offer={offer} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Confidence</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>
                  <ConfidenceCell offer={offer} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Completeness</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>{formatSignal(offer.completeness)}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Freshness</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>{formatSignal(offer.freshness)}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Official evidence</th>
              {offers.map((offer) => (
                <td key={offer.offer_id}>
                  {offer.evidence_count} evidence item{offer.evidence_count === 1 ? "" : "s"}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function QuotaCell({ offer }: { offer: CompareOffer }) {
  if (offer.quotas.length === 0) {
    return <span className="muted">No quota limits recorded</span>;
  }
  return (
    <ul className="compare-quotas">
      {offer.quotas.map((quota, index) => (
        <li key={`${quota.metric}-${index}`}>
          <span className="compare-quotas__metric">{humanizeToken(quota.metric)}</span>:{" "}
          <QuotaValue quota={quota} />
        </li>
      ))}
    </ul>
  );
}

function QuotaValue({ quota }: { quota: NormalizedQuota }) {
  if (quota.normalized && quota.canonical_amount !== null) {
    return (
      <span data-testid="quota-normalized">
        {quota.canonical_amount} {orUnknown(quota.canonical_unit)}
      </span>
    );
  }
  // Fail closed: show the raw amount/unit as reported, and label the normalization
  // honestly as "Unknown" rather than guessing a conversion.
  return (
    <span data-testid="quota-unnormalized">
      {orUnknown(quota.amount)} {orUnknown(quota.unit)}{" "}
      <span className="muted">(normalized: Unknown)</span>
    </span>
  );
}

function ConfidenceCell({ offer }: { offer: CompareOffer }) {
  const meaning = confidenceMeaning(offer.confidence_label);
  return (
    <div className="confidence confidence--compact">
      <span className={`badge badge--${meaning.tone}`} data-testid="confidence-badge">
        <span className="badge__icon" aria-hidden="true">
          ◆
        </span>
        <span className="badge__label">Confidence: {meaning.label}</span>
      </span>
      <details className="advanced" data-testid="confidence-advanced">
        <summary>Advanced: score</summary>
        <p data-testid="confidence-score">{orUnknown(offer.advanced.score)}</p>
      </details>
    </div>
  );
}
