import { useCallback, useEffect, useState } from "react";
import "./App.css";
import {
  fetchCategoryMatrix,
  fetchCompare,
  fetchProviders,
  fetchRecommendation,
  fetchSearch,
  type CategoryMatrixResponse,
  type CompareResponse,
  type ProviderSummary,
  type RecommendationRequest,
  type RecommendationResponse,
  type SearchQuery,
  type SearchResponse,
} from "./api";
import { loadCatalogue, type CatalogueView } from "./catalogue/load";
import { ProviderHeader } from "./catalogue/ProviderHeader";
import { CategoryStates } from "./catalogue/CategoryStates";
import { OfferCard } from "./catalogue/OfferCard";
import { SearchControls } from "./catalogue/SearchControls";
import { ResultsList } from "./catalogue/ResultsList";
import { CategoryMatrix } from "./catalogue/CategoryMatrix";
import { CompareView } from "./catalogue/CompareView";
import { AdviserForm } from "./adviser/AdviserForm";
import { RecommendationView } from "./adviser/RecommendationView";

/**
 * FreeTier Atlas — the provider-agnostic catalogue browser (F006 slice 2).
 *
 * A small hash-routed single-page app (no router dependency) that consumes ONLY
 * the read-only `/api/catalogue/*` GET surface over the same-origin `/api`
 * proxy. It offers four views:
 *
 * - **Browse** (`#/`): a keyword search + composable filters driving
 *   `/catalogue/search`, with a paged results list.
 * - **Categories** (`#/categories`): the 14-category × provider coverage matrix
 *   from `/catalogue/categories`.
 * - **Compare** (`#/compare`): a normalized side-by-side of the 2–3 offers the
 *   user selected, from `/catalogue/compare`.
 * - **Provider** (`#/provider/cloudflare`): the retained single-provider
 *   evidence-backed page (F005 slice 4).
 *
 * The UI never writes, never touches the database, and never re-derives Z0 or
 * confidence — it displays exactly what the API returns, honestly showing
 * "Unknown" for any null field.
 */

/** Max offers a user may line up for a side-by-side comparison (owner: 2–3). */
const MAX_COMPARE = 3;

type Route =
  | { name: "browse" }
  | { name: "categories" }
  | { name: "compare" }
  | { name: "adviser" }
  | { name: "provider"; slug: string };

function parseHash(hash: string): Route {
  const clean = hash.replace(/^#/, "").replace(/^\//, "");
  const [path] = clean.split("?");
  if (path === "categories") return { name: "categories" };
  if (path === "compare") return { name: "compare" };
  if (path === "adviser") return { name: "adviser" };
  const providerMatch = /^provider\/([a-z0-9][a-z0-9-]{0,63})$/.exec(path);
  if (providerMatch) return { name: "provider", slug: providerMatch[1] };
  return { name: "browse" };
}

function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export default function App() {
  const route = useHashRoute();
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  const toggleCompare = useCallback((offerId: number) => {
    setSelectedIds((prev) => {
      if (prev.includes(offerId)) return prev.filter((id) => id !== offerId);
      if (prev.length >= MAX_COMPARE) return prev;
      return [...prev, offerId];
    });
  }, []);

  const clearCompare = useCallback(() => setSelectedIds([]), []);

  return (
    <div className="app">
      <SiteHeader route={route} compareCount={selectedIds.length} />
      <main className="page">
        {route.name === "browse" ? (
          <BrowseView
            selectedIds={selectedIds}
            canSelectMore={selectedIds.length < MAX_COMPARE}
            onToggleCompare={toggleCompare}
          />
        ) : null}
        {route.name === "categories" ? <CategoriesView /> : null}
        {route.name === "compare" ? (
          <CompareContainer selectedIds={selectedIds} onClear={clearCompare} />
        ) : null}
        {route.name === "adviser" ? <AdviserView /> : null}
        {route.name === "provider" ? <ProviderPage slug={route.slug} /> : null}
      </main>
      <footer className="footer">
        <p>
          Read-only public catalogue. Every rating is derived from official evidence and shown with
          its confidence and provenance. Values we cannot verify are shown as “Unknown”.
        </p>
      </footer>
    </div>
  );
}

function SiteHeader({ route, compareCount }: { route: Route; compareCount: number }) {
  const links: { href: string; label: string; active: boolean }[] = [
    { href: "#/", label: "Browse", active: route.name === "browse" },
    { href: "#/categories", label: "Categories", active: route.name === "categories" },
    {
      href: "#/compare",
      label: compareCount > 0 ? `Compare (${compareCount})` : "Compare",
      active: route.name === "compare",
    },
    { href: "#/adviser", label: "Adviser", active: route.name === "adviser" },
    {
      href: "#/provider/cloudflare",
      label: "Cloudflare",
      active: route.name === "provider",
    },
  ];
  return (
    <header className="site-header">
      <span className="site-header__brand">FreeTier Atlas</span>
      <nav className="site-nav" aria-label="Primary">
        <ul>
          {links.map((link) => (
            <li key={link.href}>
              <a href={link.href} aria-current={link.active ? "page" : undefined}>
                {link.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  );
}

// --- Async view wrapper -------------------------------------------------------

type Async<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T }
  | { kind: "error"; message: string };

function useAsync<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): { state: Async<T>; reload: () => void } {
  const [state, setState] = useState<Async<T>>({ kind: "loading" });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    loader(controller.signal)
      .then((data) => setState({ kind: "ok", data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : "Unknown error.";
        setState({ kind: "error", message });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { state, reload: () => setNonce((n) => n + 1) };
}

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="status status--error" role="alert">
      <p>Unable to load the catalogue: {message}</p>
      <button type="button" className="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

// --- Browse view --------------------------------------------------------------

function BrowseView({
  selectedIds,
  canSelectMore,
  onToggleCompare,
}: {
  selectedIds: number[];
  canSelectMore: boolean;
  onToggleCompare: (offerId: number) => void;
}) {
  const [query, setQuery] = useState<SearchQuery>({ page: 1 });

  const { state: results, reload: reloadResults } = useAsync<SearchResponse>(
    (signal) => fetchSearch(query, signal),
    [JSON.stringify(query)],
  );
  const { state: providers } = useAsync<ProviderSummary[]>((signal) => fetchProviders(signal), []);
  const { state: matrix } = useAsync<CategoryMatrixResponse>(
    (signal) => fetchCategoryMatrix(signal),
    [],
  );

  const providerList = providers.kind === "ok" ? providers.data : [];
  const categoryList = matrix.kind === "ok" ? matrix.data.categories : [];

  return (
    <>
      <section className="card" aria-labelledby="browse-heading">
        <h1 id="browse-heading">Browse the free-tier catalogue</h1>
        <p className="tagline">
          Search and filter evidence-backed free-tier offers across providers. Every rating comes
          from official sources and is shown with its confidence.
        </p>
        <SearchControls
          value={query}
          providers={providerList}
          categories={categoryList}
          onSubmit={(next) => setQuery({ ...next, page: 1 })}
          onReset={() => setQuery({ page: 1 })}
        />
      </section>

      {results.kind === "loading" ? (
        <p className="status status--loading" role="status">
          Searching the catalogue…
        </p>
      ) : null}
      {results.kind === "error" ? (
        <ErrorPanel message={results.message} onRetry={reloadResults} />
      ) : null}
      {results.kind === "ok" ? (
        <ResultsList
          data={results.data}
          selectedIds={selectedIds}
          canSelectMore={canSelectMore}
          onToggleCompare={onToggleCompare}
          onPageChange={(page) => setQuery({ ...query, page })}
        />
      ) : null}
    </>
  );
}

// --- Categories view ----------------------------------------------------------

function CategoriesView() {
  const { state, reload } = useAsync<CategoryMatrixResponse>(
    (signal) => fetchCategoryMatrix(signal),
    [],
  );
  return (
    <>
      <h1 className="page-title">Free-tier coverage by category</h1>
      {state.kind === "loading" ? (
        <p className="status status--loading" role="status">
          Loading the category matrix…
        </p>
      ) : null}
      {state.kind === "error" ? <ErrorPanel message={state.message} onRetry={reload} /> : null}
      {state.kind === "ok" ? <CategoryMatrix data={state.data} /> : null}
    </>
  );
}

// --- Compare view -------------------------------------------------------------

function CompareContainer({
  selectedIds,
  onClear,
}: {
  selectedIds: number[];
  onClear: () => void;
}) {
  const enough = selectedIds.length >= 2;
  const { state, reload } = useAsync<CompareResponse>(
    (signal) =>
      enough ? fetchCompare(selectedIds, signal) : Promise.resolve({ offer_ids: [], offers: [] }),
    [selectedIds.join(",")],
  );

  return (
    <>
      <div className="page-title-row">
        <h1 className="page-title">Compare free-tier offers</h1>
        {selectedIds.length > 0 ? (
          <button type="button" className="button" onClick={onClear}>
            Clear selection
          </button>
        ) : null}
      </div>
      {!enough ? (
        <section className="card" aria-labelledby="compare-hint-heading">
          <h2 id="compare-hint-heading">Select offers to compare</h2>
          <p className="muted" data-testid="compare-hint">
            Pick two or three offers from the <a href="#/">Browse</a> view, then return here to
            compare them side by side.
          </p>
        </section>
      ) : null}
      {enough && state.kind === "loading" ? (
        <p className="status status--loading" role="status">
          Loading the comparison…
        </p>
      ) : null}
      {enough && state.kind === "error" ? (
        <ErrorPanel message={state.message} onRetry={reload} />
      ) : null}
      {enough && state.kind === "ok" ? <CompareView data={state.data} /> : null}
    </>
  );
}

// --- Adviser view (F006 slice 4) ----------------------------------------------

/**
 * The architecture adviser page (`#/adviser`).
 *
 * The user fills in an editable STRUCTURED requirements form; on submit we POST
 * it to the deterministic `/api/adviser/recommend` endpoint and render the
 * recommendation verbatim below the form. This is the ONLY page that writes to
 * the API, and even that call is stateless — it mutates nothing. There is no
 * natural-language input, no LLM, no consent flow, and no export here.
 *
 * The form stays visible at all times so the user can refine and resubmit; the
 * page owns the single `<h1>` and the POST state, while {@link RecommendationView}
 * renders its own `<h2>` region beneath it.
 */
function AdviserView() {
  type AdviserState =
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "ok"; data: RecommendationResponse }
    | { kind: "error"; message: string };
  const [state, setState] = useState<AdviserState>({ kind: "idle" });

  const submit = useCallback((request: RecommendationRequest) => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    fetchRecommendation(request, controller.signal)
      .then((data) => setState({ kind: "ok", data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : "Unknown error.";
        setState({ kind: "error", message });
      });
  }, []);

  return (
    <section aria-labelledby="adviser-heading">
      <h1 id="adviser-heading" className="page-title">
        Architecture adviser
      </h1>
      <p className="tagline">
        Describe your workload as structured requirements and get a deterministic, evidence-backed
        recommendation for a $0 (truly-free) architecture. Every rating comes from official sources;
        anything we cannot verify is shown as “Unknown”.
      </p>

      <section className="card" aria-labelledby="adviser-form-heading">
        <h2 id="adviser-form-heading" className="section-heading">
          Your workload
        </h2>
        <AdviserForm onSubmit={submit} disabled={state.kind === "loading"} />
      </section>

      {state.kind === "loading" ? (
        <p className="status status--loading" role="status">
          Computing your recommendation…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <div className="status status--error" role="alert">
          <p>Unable to compute a recommendation: {state.message}</p>
          <p className="muted">Adjust your requirements above and try again.</p>
        </div>
      ) : null}
      {state.kind === "ok" ? <RecommendationView data={state.data} /> : null}
    </section>
  );
}

// --- Provider page (retained F005 experience) ---------------------------------

function ProviderPage({ slug }: { slug: string }) {
  const { state, reload } = useAsync<CatalogueView>(
    (signal) => loadCatalogue(slug, signal),
    [slug],
  );

  if (state.kind === "loading") {
    return (
      <section className="card" aria-labelledby="provider-loading-heading">
        <h1 id="provider-loading-heading">FreeTier Atlas</h1>
        <p className="status status--loading" role="status">
          Loading the {slug} catalogue…
        </p>
      </section>
    );
  }
  if (state.kind === "error") {
    return (
      <section className="card" aria-labelledby="provider-error-heading">
        <h1 id="provider-error-heading">FreeTier Atlas</h1>
        <ErrorPanel message={state.message} onRetry={reload} />
      </section>
    );
  }

  const { data: view } = state;
  return (
    <>
      <ProviderHeader provider={view.provider} />
      <CategoryStates data={view.categoryStates} />
      <section aria-labelledby="offers-heading">
        <h2 id="offers-heading" className="section-heading">
          Offers
        </h2>
        {view.offers.length === 0 ? (
          <p className="muted">No published offers are available yet.</p>
        ) : (
          view.offers.map((bundle) => <OfferCard key={bundle.detail.offer_id} bundle={bundle} />)
        )}
      </section>
    </>
  );
}
