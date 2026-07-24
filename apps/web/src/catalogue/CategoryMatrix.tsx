import type { CategoryMatrixResponse, ProviderCoverage } from "../api";
import { coverageMeaning } from "./vocab";

/**
 * The 14-category × provider coverage matrix (scope: CATEGORY MATRIX view).
 *
 * Rendered as an accessible `<table>` with a caption, a column header per
 * provider, and a row header per canonical category. Every canonical category
 * is always present (even when no provider offers it). Each coverage cell is a
 * badge that pairs its colour with a visible text label and an aria-hidden icon
 * (never colour-only). The coverage `state` is taken verbatim from the API and
 * never re-derived here. Published offers a provider has that are not mapped to
 * a canonical category are surfaced honestly in a per-provider uncategorized
 * rollup below the table.
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
        Every one of the fourteen canonical categories crossed with each provider’s published
        coverage. States are derived from published offers — never guessed.
      </p>

      <div className="matrix__scroll">
        <table className="matrix-table" data-testid="matrix-table">
          <caption className="sr-only">
            Free-tier coverage by category and provider
          </caption>
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
                    <td key={slug}>
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

function CoverageBadge({ coverage }: { coverage: ProviderCoverage | undefined }) {
  // A category the API returned no coverage entry for is honestly "not offered".
  const state = coverage ? coverage.state : "not_offered";
  const meaning = coverageMeaning(state);
  const title =
    coverage && coverage.published_offer_count > 0
      ? `${coverage.published_offer_count} published, ${coverage.free_offer_count} truly free`
      : undefined;
  return (
    <span
      className={`badge badge--${meaning.tone}`}
      title={title}
      data-testid="coverage-badge"
      data-state={state}
    >
      <span className="badge__icon" aria-hidden="true">
        {meaning.icon}
      </span>
      <span className="badge__label">{meaning.label}</span>
    </span>
  );
}
