import type { CategoryMatrixResponse, ProviderCoverage } from "../api";
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
              <li key={u.provider_slug}>
                <span className="uncategorized__provider">{u.provider_name}</span>:{" "}
                {u.published_offer_count} published ({u.free_offer_count} truly free)
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
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
      details.push(
        `${coverage.published_offer_count} published, ${coverage.free_offer_count} truly free`,
      );
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
    >
      <span className="badge__icon" aria-hidden="true">
        {meaning.icon}
      </span>
      <span className="badge__label">{meaning.label}</span>
    </span>
  );
}
