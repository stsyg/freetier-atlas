import type { JSX } from "react";
import type {
  AdviserComponent,
  AdviserImpossible,
  AdviserNotFreeOption,
  AdviserOfferRef,
  RecommendationResponse,
} from "../api";
import { confidenceMeaning, humanizeToken, orUnknown } from "../catalogue/format";
import { Z0Badge } from "../catalogue/Z0Badge";

/**
 * Renders a deterministic adviser {@link RecommendationResponse} verbatim
 * (F006 slice 4).
 *
 * The UI is DISPLAY-ONLY: it never re-derives the Z0 class, confidence, or
 * quota math — it shows exactly what the API returns, and any `null`/absent
 * field is rendered honestly as "Unknown" rather than guessed. It renders, in
 * order: an overall summary + the whole-architecture $0 proof, the recommended
 * $0 architecture components (each with its exact quota math, Z0-safety reasons,
 * portability/lock-in/exit-plan, and evidence), any blocking requirements in the
 * STRICT API order (1. blocking → 2. reduction → 3. recalculation →
 * 4. self-hosting), and — clearly separated — the "Not $0 / paid" options that
 * are never part of the recommendation.
 *
 * The single `<h1>` for the route is owned by the page container; this component
 * starts at `<h2>` so the heading order stays consistent.
 */
export function RecommendationView({ data }: { data: RecommendationResponse }) {
  const zeroCost = data.fully_zero_cost;
  const tone = zeroCost ? "free" : "warn";
  const icon = zeroCost ? "✓" : "!";
  const summary = zeroCost
    ? "Guaranteed $0: every requirement is met by a truly-free (Z0) offer."
    : "Not fully $0: one or more requirements have no fitting free (Z0) offer.";

  return (
    <section className="recommendation" aria-labelledby="recommendation-heading">
      <h2 id="recommendation-heading">
        Recommendation{data.workload_name ? `: ${data.workload_name}` : ""}
      </h2>

      <p className="recommendation__summary">
        <span className={`badge badge--${tone}`} data-testid="zero-cost-badge">
          <span className="badge__icon" aria-hidden="true">
            {icon}
          </span>
          <span className="badge__label">{zeroCost ? "$0 guaranteed" : "Not fully $0"}</span>
        </span>{" "}
        {summary}
      </p>

      <section className="recommendation__priorities" aria-labelledby="priorities-heading">
        <h3 id="priorities-heading" className="section-heading">
          How this was optimised
        </h3>
        <ol className="priority-list">
          {data.priorities.map((priority) => (
            <li key={priority}>{humanizeToken(priority)}</li>
          ))}
        </ol>
      </section>

      {data.zero_cost_proof.length > 0 ? (
        <section className="recommendation__proof" aria-labelledby="proof-heading">
          <h3 id="proof-heading" className="section-heading">
            {zeroCost ? "$0 proof" : "Why this is not fully $0"}
          </h3>
          <ul className="proof-list" data-testid="zero-cost-proof">
            {data.zero_cost_proof.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {data.architecture.length > 0 ? (
        <section className="recommendation__architecture" aria-labelledby="architecture-heading">
          <h3 id="architecture-heading" className="section-heading">
            {zeroCost ? "Recommended $0 architecture" : "Components that fit at $0"}
          </h3>
          {data.architecture.map((component) => (
            <ComponentCard
              key={`${component.requirement_index}-${component.offer.offer_id}`}
              component={component}
              level={4}
            />
          ))}
        </section>
      ) : null}

      {data.impossible.length > 0 ? (
        <section className="recommendation__impossible" aria-labelledby="impossible-heading">
          <h3 id="impossible-heading" className="section-heading">
            Requirements with no $0 option
          </h3>
          {data.impossible.map((item) => (
            <ImpossibleCard key={item.requirement_index} item={item} />
          ))}
        </section>
      ) : null}

      <NotFreeSection label={data.not_free_section.label} options={data.not_free_section.options} />
    </section>
  );
}

/** Render a heading at a numeric level (2–6) so nesting stays ordered. */
function Heading({
  level,
  className,
  children,
}: {
  level: number;
  className?: string;
  children: React.ReactNode;
}) {
  const clamped = Math.min(Math.max(level, 2), 6);
  const Tag = `h${clamped}` as keyof JSX.IntrinsicElements;
  return <Tag className={className}>{children}</Tag>;
}

// --- Offer reference ----------------------------------------------------------

function OfferRef({ offer }: { offer: AdviserOfferRef }) {
  const confidence = confidenceMeaning(offer.confidence_label);
  return (
    <div className="offer-ref" data-testid="offer-ref">
      <p className="offer-ref__name">
        <strong>{offer.service_name}</strong>{" "}
        <span className="muted">by {offer.provider_name}</span>
      </p>
      <p className="offer-ref__badges">
        <Z0Badge zeroCostClass={offer.zero_cost_class} />{" "}
        <span className={`badge badge--${confidence.tone}`} data-testid="confidence-badge">
          <span className="badge__icon" aria-hidden="true">
            ◆
          </span>
          <span className="badge__label">Confidence: {confidence.label}</span>
        </span>
      </p>
    </div>
  );
}

// --- Component card -----------------------------------------------------------

/**
 * One recommended component. `level` is the heading level of its title; its
 * sub-section headings sit one level deeper. When `showTitle` is false (used for
 * the recalculated component nested under an impossible step) the title is
 * omitted and sections render at `level`.
 */
function ComponentCard({
  component,
  level,
  showTitle = true,
}: {
  component: AdviserComponent;
  level: number;
  showTitle?: boolean;
}) {
  const sectionLevel = showTitle ? level + 1 : level;
  const title =
    component.label ?? CATEGORY_LABEL[component.category] ?? humanizeToken(component.category);
  return (
    <article className="component-card" data-testid="component-card">
      {showTitle ? (
        <div className="component-card__head">
          <Heading level={level} className="component-card__title">
            {title}
            {component.reduced ? (
              <span className="pill pill--warn" data-testid="reduced-pill">
                under reduced demand
              </span>
            ) : null}
          </Heading>
          <OfferRef offer={component.offer} />
        </div>
      ) : (
        <OfferRef offer={component.offer} />
      )}

      <QuotaMathTable component={component} level={sectionLevel} />

      <div className="component-card__section">
        <Heading level={sectionLevel}>Why this stays $0</Heading>
        <ul className="reasons">
          {component.z0_safety.map((reason, index) => (
            <li key={index}>{reason}</li>
          ))}
        </ul>
      </div>

      <PortabilityDetails component={component} level={sectionLevel} />

      <EvidenceLinks component={component} level={sectionLevel} />
    </article>
  );
}

function QuotaMathTable({ component, level }: { component: AdviserComponent; level: number }) {
  return (
    <div className="component-card__section">
      <Heading level={level}>Quota math</Heading>
      <table className="quota-math-table" data-testid="quota-math-table">
        <caption className="sr-only">
          Per-demand quota fit and headroom for {component.offer.service_name}
        </caption>
        <thead>
          <tr>
            <th scope="col">Demand</th>
            <th scope="col">You need</th>
            <th scope="col">Free quota</th>
            <th scope="col">Headroom</th>
            <th scope="col">Covered</th>
          </tr>
        </thead>
        <tbody>
          {component.demands.map((demand, index) => (
            <tr key={index} data-testid="quota-math-row">
              <th scope="row">{humanizeToken(demand.matched_metric ?? demand.metric)}</th>
              <td>
                {demand.demand_amount} {demand.demand_unit}
              </td>
              <td>
                {orUnknown(demand.quota_canonical)} {orUnknown(demand.canonical_unit)}
              </td>
              <td>
                {orUnknown(demand.headroom)} {orUnknown(demand.canonical_unit)}
              </td>
              <td>
                {demand.covered ? (
                  <span className="pill pill--ok">
                    {demand.boundary ? "Yes (exact)" : "Yes"}
                  </span>
                ) : (
                  <span className="pill pill--warn">No</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {component.quota_math.length > 0 ? (
        <ul className="quota-math-notes">
          {component.quota_math.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function PortabilityDetails({ component, level }: { component: AdviserComponent; level: number }) {
  const p = component.portability;
  return (
    <div className="component-card__section">
      <Heading level={level}>Portability &amp; lock-in</Heading>
      <p className="portability__badges">
        <span className="badge badge--info" data-testid="portability-badge">
          <span className="badge__icon" aria-hidden="true">
            ⇄
          </span>
          <span className="badge__label">Portability: {humanizeToken(p.label)}</span>
        </span>{" "}
        <span className="badge badge--info" data-testid="lockin-badge">
          <span className="badge__icon" aria-hidden="true">
            ⚿
          </span>
          <span className="badge__label">Lock-in: {humanizeToken(p.lock_in_label)}</span>
        </span>
      </p>

      {p.exit_plan.length > 0 ? (
        <>
          <p className="portability__subhead">
            <strong>Exit plan</strong>
          </p>
          <ul className="reasons">
            {p.exit_plan.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </>
      ) : null}

      <details className="advanced" data-testid="portability-advanced">
        <summary>Advanced: portability score &amp; basis</summary>
        <dl className="kv">
          <div>
            <dt>Numeric score (0–1)</dt>
            <dd data-testid="portability-score">{orUnknown(p.score)}</dd>
          </div>
          <div>
            <dt>Deployment model</dt>
            <dd>{humanizeToken(p.deployment_model)}</dd>
          </div>
        </dl>
        {p.basis.length > 0 ? (
          <ul className="reasons">
            {p.basis.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        ) : null}
      </details>
    </div>
  );
}

function EvidenceLinks({ component, level }: { component: AdviserComponent; level: number }) {
  if (component.evidence.length === 0) {
    return (
      <div className="component-card__section">
        <Heading level={level}>Evidence</Heading>
        <p className="muted">No linked evidence.</p>
      </div>
    );
  }
  return (
    <div className="component-card__section">
      <Heading level={level}>Evidence</Heading>
      <ul className="evidence-links">
        {component.evidence.map((ref, index) => (
          <li key={index}>
            {ref.url ? (
              <a href={ref.url} target="_blank" rel="noopener noreferrer">
                {orUnknown(ref.title)}
              </a>
            ) : (
              <span>{orUnknown(ref.title)}</span>
            )}
            {ref.official ? <span className="muted"> (official)</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- Impossible resolution (strict order) -------------------------------------

function ImpossibleCard({ item }: { item: AdviserImpossible }) {
  const title = item.label ?? CATEGORY_LABEL[item.category] ?? humanizeToken(item.category);
  return (
    <article className="impossible-card" data-testid="impossible-card">
      <h4 className="impossible-card__title">{title}</h4>

      <ol className="impossible-steps">
        <li data-testid="impossible-step-blocking">
          <h5>1. Blocking condition</h5>
          <p>{item.blocking_reason}</p>
          {item.closest ? (
            <div className="impossible-closest">
              <p className="muted">Closest free option:</p>
              <OfferRef offer={item.closest} />
            </div>
          ) : null}
        </li>

        <li data-testid="impossible-step-reduction">
          <h5>2. Reduction</h5>
          {item.reductions.length > 0 ? (
            <ul className="reasons">
              {item.reductions.map((reduction, index) => (
                <li key={index}>
                  <strong>{humanizeToken(reduction.metric)}:</strong>{" "}
                  {reduction.feasible && reduction.reduced_amount !== null
                    ? `reduce ${reduction.original_amount} → ${reduction.reduced_amount} ${reduction.original_unit}. `
                    : "cannot be reduced to fit a free quota. "}
                  {reduction.reason}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No reduction is possible for this requirement.</p>
          )}
        </li>

        <li data-testid="impossible-step-recalculation">
          <h5>3. Recalculation</h5>
          {item.recalculated ? (
            <ComponentCard component={item.recalculated} level={6} showTitle={false} />
          ) : (
            <p className="muted">
              Even under the reduced demand, no free (Z0) offer fits. See self-hosting below.
            </p>
          )}
        </li>

        <li data-testid="impossible-step-selfhosting">
          <h5>4. Self-hosting</h5>
          {item.self_hosting.length > 0 ? (
            <ul className="self-hosting-list">
              {item.self_hosting.map((option, index) => (
                <li key={index} className="self-hosting" data-testid="self-hosting">
                  <OfferRef offer={option.building_block} />
                  {option.host ? (
                    <p className="muted">
                      Suggested $0 host: {option.host.service_name} by {option.host.provider_name}
                    </p>
                  ) : null}
                  <p>{option.note}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              No self-hostable (Z3) building block is published for this category.
            </p>
          )}
        </li>
      </ol>
    </article>
  );
}

// --- Not-$0 / paid section (clearly separated) --------------------------------

function NotFreeSection({ label, options }: { label: string; options: AdviserNotFreeOption[] }) {
  if (options.length === 0) return null;
  return (
    <section className="not-free" aria-labelledby="not-free-heading" data-testid="not-free-section">
      <h3 id="not-free-heading" className="section-heading">
        Not $0 / paid options
      </h3>
      <p className="not-free__disclaimer">{label}</p>
      <ul className="not-free-list">
        {options.map((option, index) => (
          <li key={index} className="not-free-option" data-testid="not-free-option">
            <OfferRef offer={option.offer} />
            <p className="muted">{option.note}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

// --- Category label lookup (display only) -------------------------------------
//
// A local slug→name map so a component heading reads naturally even when the API
// echoes only the slug. It never affects any cost/Z0/quota decision.
const CATEGORY_LABEL: Record<string, string> = {
  "compute-vms": "Compute and virtual machines",
  "containers-app-hosting": "Containers and application hosting",
  "serverless-functions": "Serverless functions",
  "relational-databases": "Relational databases",
  "nosql-key-value": "NoSQL and key-value databases",
  "object-file-storage": "Object and file storage",
  "networking-cdn-dns": "Networking, CDN, and DNS",
  "queues-messaging-jobs": "Queues, messaging, and scheduled jobs",
  "auth-identity": "Authentication and identity",
  "cicd-source-control": "CI/CD and source control",
  "monitoring-logs-tracing": "Monitoring, logs, and tracing",
  "ai-inference-embeddings": "AI models, inference, and embeddings",
  "email-notifications-comms": "Email, notifications, and communications",
  "secrets-config-devtools": "Secrets, configuration, and developer tools",
};
