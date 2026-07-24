import type { CategoryStatesResponse } from "../api";
import { humanizeToken } from "./format";
import { Z0Badge } from "./Z0Badge";
import { confidenceMeaning } from "./format";

/**
 * Category / service states (scope item 1).
 *
 * Renders the provider's published offers grouped by category → service, each
 * offer showing its current Z0 state as a badge (colour paired with text, never
 * colour-only) plus its plain-language confidence label. Each offer links to its
 * full card below via an in-page anchor.
 */
export function CategoryStates({ data }: { data: CategoryStatesResponse }) {
  if (data.categories.length === 0) {
    return (
      <section className="card" aria-labelledby="category-states-heading">
        <h2 id="category-states-heading">Service states</h2>
        <p className="muted">No published offers yet for {data.provider_name}.</p>
      </section>
    );
  }

  return (
    <section className="card" aria-labelledby="category-states-heading">
      <h2 id="category-states-heading">Service states</h2>
      <p className="muted">
        Cloudflare services grouped by category, each with its current zero-cost state.
      </p>

      {data.categories.map((group, index) => {
        const categoryName = group.category ? group.category.name : "Uncategorised";
        const categoryKey = group.category ? group.category.slug : `uncategorised-${index}`;
        return (
          <div className="category-group" key={categoryKey}>
            <h3 className="category-group__name">{categoryName}</h3>
            <ul className="service-list">
              {group.services.map((service) => (
                <li className="service" key={service.service_id}>
                  <div className="service__head">
                    <span className="service__name">{service.canonical_name}</span>
                    <span className="service__model">
                      {humanizeToken(service.deployment_model)}
                    </span>
                  </div>
                  <ul className="offer-state-list">
                    {service.offers.map((offer) => (
                      <li className="offer-state" key={offer.offer_id}>
                        <a className="offer-state__link" href={`#offer-${offer.offer_id}`}>
                          {humanizeToken(offer.offer_type)}
                        </a>
                        <Z0Badge zeroCostClass={offer.zero_cost_class} />
                        <span className="offer-state__confidence">
                          {confidenceMeaning(offer.confidence_label).label} confidence
                        </span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
