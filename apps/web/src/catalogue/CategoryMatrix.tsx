import type { CategoryMatrixResponse, ProviderCoverage, UncategorizedCoverage } from "../api";
import { EvidenceCurrencyNote } from "./EvidenceCurrencyNote";
import { describeFreeCount } from "./format";
import { COVERAGE_STATE_ORDER, coverageMeaning } from "./vocab";

/**
 * The 14-category × provider coverage matrix (scope: CATEGORY MATRIX view).
 *
 * Rendered as an accessible `<table>` with a caption, a column header per
 * provider, and a row header per canonical category. Every canonical category is
 * always present (even when no provider offers it). Each coverage cell is a
 * badge that pairs its colour with a visible text label and an aria-hidden icon
 * (never colour-only), and a legend explains all seven states.
 *
 * The coverage `state` is taken verbatim from the API and never re-derived here.
 * A cell the API returned no entry for is `unknown` — never `not_offered`, which
 * is a declared claim with a stated reason and cannot be inferred from missing
 * data. Published offers a provider has that are not mapped to a canonical
 * category are surfaced honestly in a per-provider uncategorized rollup below
 * the table.
 *
 * Narrow viewports (~390px) stack each row into a label/value list via CSS, so
 * the table never scrolls horizontally or clips a cell.
 */
export function CategoryMatrix({ data }: { data: CategoryMatrixResponse }) {
  const providerNames = new Map<string, string>();
  for (const row of data.categories) {
    for (const coverage of row.providers) {
      providerNames.set(coverage.provider_slug, coverage.provider_name);
    }
  }
  for (const u of data.uncategorized) {
    providerNames.set(u.provider_slug, u.provider_name);
  }
  const providerSlugs = data.provider_slugs.length
    ? data.provider_slugs
    : Array.from(providerNames.keys());

  if (providerSlugs.length === 0) {
    return (
      <section className="card" aria-labelledby="matrix-heading">
        <h2 id="matrix-heading">Category coverage</h2>
        <p className="muted">No providers are published yet.</p>
      </section>
    );
  }

  return (
    <section className="card" aria-labelledby="matrix-heading">
      <h2 id="matrix-heading">Category coverage</h2>
      <p className="muted">
        Every one of the fourteen canonical categories crossed with each provider’s coverage. Each
        state is either declared by the provider’s maintainers with a reason, or derived from
        published offers — never guessed. “Unknown” means nobody has checked yet; it is not the same
        as “not offered”.
      </p>

      <CoverageLegend />

      <div className="matrix__scroll">
        <table className="matrix-table" data-testid="matrix-table">
          <caption className="sr-only">Free-tier coverage by category and provider</caption>
          <thead>
            <tr>
              <th scope="col">Category</th>
              {providerSlugs.map((slug) => (
                <th scope="col" key={slug}>
                  {providerNames.get(slug) ?? slug}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.categories.map((row) => {
              const bySlug = new Map(row.providers.map((c) => [c.provider_slug, c]));
              return (
                <tr key={row.slug} data-testid="matrix-row">
                  <th scope="row">{row.name}</th>
                  {providerSlugs.map((slug) => (
                    <td key={slug} data-label={providerNames.get(slug) ?? slug}>
                      <CoverageBadge coverage={bySlug.get(slug)} />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {data.uncategorized.length > 0 ? (
        <div className="matrix__uncategorized" data-testid="matrix-uncategorized">
          <h3>Uncategorised published offers</h3>
          <p className="muted">
            Published offers not yet mapped to a canonical category, reported honestly rather than
            forced into one.
          </p>
          <ul className="uncategorized-list">
            {data.uncategorized.map((u) => (
              <UncategorizedRow key={u.provider_slug} rollup={u} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

/**
 * One provider's uncategorised rollup.
 *
 * This rollup deliberately carries an EVIDENCE signal and not a coverage
 * `state`. "Uncategorised" is not one of the fourteen canonical categories, so
 * giving it a coverage state would assert a claim about a bucket we cannot name
 * — the same guess F008 removed when it stopped inferring `not_offered`. The
 * note below is the one that already ships for every other repeated free claim;
 * no second visual language for staleness is introduced here.
 */
function UncategorizedRow({ rollup }: { rollup: UncategorizedCoverage }) {
  return (
    <li data-testid="uncategorized-row" data-provider={rollup.provider_slug}>
      <span className="uncategorized__provider">{rollup.provider_name}</span>:{" "}
      {rollup.published_offer_count} published ({describeFreeCount(rollup)})
      <EvidenceCurrencyNote currency={rollup.evidence_currency} compact />
    </li>
  );
}

/** Explains all seven coverage states. Each entry shows the same badge the cells use. */
function CoverageLegend() {
  return (
    <div className="matrix__legend" data-testid="coverage-legend">
      <h3 className="matrix__legend-heading" id="coverage-legend-heading">
        What the states mean
      </h3>
      <dl className="coverage-legend" aria-labelledby="coverage-legend-heading">
        {COVERAGE_STATE_ORDER.map((state) => {
          const meaning = coverageMeaning(state);
          return (
            <div className="coverage-legend__item" key={state} data-legend-state={state}>
              <dt>
                <span className={`badge badge--${meaning.tone}`}>
                  <span className="badge__icon" aria-hidden="true">
                    {meaning.icon}
                  </span>
                  <span className="badge__label">{meaning.label}</span>
                </span>
              </dt>
              <dd>{meaning.description}</dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

function CoverageBadge({ coverage }: { coverage: ProviderCoverage | undefined }) {
  // A pair the API returned no entry for is "unknown". It is emphatically NOT
  // "not offered": absence of data is not evidence of absence.
  const state = coverage ? coverage.state : "unknown";
  const meaning = coverageMeaning(state);
  const details: string[] = [meaning.description];
  if (coverage) {
    if (coverage.published_offer_count > 0) {
      details.push(`${coverage.published_offer_count} published, ${describeFreeCount(coverage)}`);
    }
    if (coverage.rationale) {
      details.push(coverage.rationale);
    }
    if (coverage.mismatch) {
      details.push(
        `Declared "${coverage.declared_state ?? "unknown"}" but the published catalogue shows ` +
          `"${coverage.derived_state ?? "unknown"}".`,
      );
    }
  }
  return (
    <span
      className={`badge badge--${meaning.tone}`}
      title={details.join(" — ")}
      data-testid="coverage-badge"
      data-state={state}
      data-declared-state={coverage?.declared_state ?? ""}
      data-derived-state={coverage?.derived_state ?? ""}
      data-mismatch={coverage?.mismatch ? "true" : "false"}
      data-free-count={coverage?.free_offer_count ?? ""}
      data-current-free-count={coverage?.current_free_offer_count ?? ""}
    >
      <span className="badge__icon" aria-hidden="true">
        {meaning.icon}
      </span>
      <span className="badge__label">{meaning.label}</span>
    </span>
  );
}
