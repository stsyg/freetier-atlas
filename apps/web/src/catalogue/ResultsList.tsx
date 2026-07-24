import type { SearchResponse, SearchResultItem } from "../api";
import { confidenceMeaning, humanizeToken } from "./format";
import { Z0Badge } from "./Z0Badge";

/**
 * The paged catalogue search results (scope: RESULTS list).
 *
 * Each result is a semantic list item showing the offer's provider, service,
 * category, Z0 class (as a colour+text+icon badge, never colour-only) and its
 * plain-language confidence label. A keyboard-operable checkbox adds/removes the
 * offer from the compare selection. Pagination is stable (prev/next + a
 * "page X of Y" indicator). Absent fields render honestly as "Unknown".
 */
export interface ResultsListProps {
  data: SearchResponse;
  selectedIds: number[];
  canSelectMore: boolean;
  onToggleCompare: (offerId: number) => void;
  onPageChange: (page: number) => void;
}

export function ResultsList({
  data,
  selectedIds,
  canSelectMore,
  onToggleCompare,
  onPageChange,
}: ResultsListProps) {
  const { results, page, total_pages, total_results } = data;

  return (
    <section className="card" aria-labelledby="results-heading">
      <div className="results__head">
        <h2 id="results-heading">Results</h2>
        <p className="muted" role="status" data-testid="results-count">
          {total_results === 0
            ? "No matching offers"
            : `${total_results} matching offer${total_results === 1 ? "" : "s"}`}
        </p>
      </div>

      {results.length === 0 ? (
        <p className="muted" data-testid="results-empty">
          No published offers match your search. Try clearing a filter.
        </p>
      ) : (
        <ul className="results-list" data-testid="results-list">
          {results.map((result) => (
            <ResultRow
              key={result.offer_id}
              result={result}
              selected={selectedIds.includes(result.offer_id)}
              disabled={!selectedIds.includes(result.offer_id) && !canSelectMore}
              onToggleCompare={onToggleCompare}
            />
          ))}
        </ul>
      )}

      {total_pages > 1 ? (
        <nav className="pagination" aria-label="Results pages">
          <button
            type="button"
            className="button"
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
          >
            Previous
          </button>
          <span className="pagination__status" data-testid="pagination-status">
            Page {page} of {total_pages}
          </span>
          <button
            type="button"
            className="button"
            onClick={() => onPageChange(page + 1)}
            disabled={page >= total_pages}
          >
            Next
          </button>
        </nav>
      ) : null}
    </section>
  );
}

function ResultRow({
  result,
  selected,
  disabled,
  onToggleCompare,
}: {
  result: SearchResultItem;
  selected: boolean;
  disabled: boolean;
  onToggleCompare: (offerId: number) => void;
}) {
  const categoryName = result.category ? result.category.name : "Uncategorised";
  const checkboxId = `compare-select-${result.offer_id}`;
  return (
    <li className="result" data-testid="result-row">
      <div className="result__main">
        <div className="result__head">
          <span className="result__service">{result.service_name}</span>
          <Z0Badge zeroCostClass={result.zero_cost_class} />
        </div>
        <p className="result__meta">
          <span className="result__provider">{result.provider_name}</span>
          {" · "}
          <span className="result__category">{categoryName}</span>
          {" · "}
          <span className="result__type">{humanizeToken(result.offer_type)}</span>
        </p>
        <p className="result__confidence muted">
          {confidenceMeaning(result.confidence_label).label} confidence
        </p>
      </div>
      <label className="result__compare" htmlFor={checkboxId}>
        <input
          id={checkboxId}
          type="checkbox"
          checked={selected}
          disabled={disabled}
          onChange={() => onToggleCompare(result.offer_id)}
        />
        <span>Compare</span>
      </label>
    </li>
  );
}
