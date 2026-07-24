import { useCallback, useEffect, useState } from "react";
import "./App.css";
import { loadCatalogue, type CatalogueView } from "./catalogue/load";
import { ProviderHeader } from "./catalogue/ProviderHeader";
import { CategoryStates } from "./catalogue/CategoryStates";
import { OfferCard } from "./catalogue/OfferCard";

/**
 * The public Cloudflare provider experience (F005 slice 4).
 *
 * A single provider-focused page that consumes ONLY the read-only catalogue API
 * over the same-origin `/api` proxy and renders the real published data:
 * category/service states, offers with their Z0 rating + plain-language reasons,
 * quotas, confidence, official evidence, history, and completeness/freshness.
 *
 * Catalogue-wide search, cross-provider comparison, and the adviser are
 * deferred to a later increment (F006).
 */
const PROVIDER_SLUG = "cloudflare";

type State =
  | { kind: "loading" }
  | { kind: "ok"; view: CatalogueView }
  | { kind: "error"; message: string };

export default function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback((signal?: AbortSignal) => {
    setState({ kind: "loading" });
    loadCatalogue(PROVIDER_SLUG, signal)
      .then((view) => setState({ kind: "ok", view }))
      .catch((error: unknown) => {
        if (signal?.aborted) return;
        const message = error instanceof Error ? error.message : "Unknown error.";
        setState({ kind: "error", message });
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
    <main className="page">
      <CatalogueBody state={state} onRetry={() => load()} />
      <footer className="footer">
        <p>
          Read-only public catalogue. Every rating is derived from official evidence and shown
          with its confidence and provenance. Values we cannot verify are shown as “Unknown”.
        </p>
      </footer>
    </main>
  );
}

function CatalogueBody({ state, onRetry }: { state: State; onRetry: () => void }) {
  if (state.kind === "loading") {
    return (
      <section className="card" aria-labelledby="loading-heading">
        <h1 id="loading-heading">FreeTier Atlas</h1>
        <p className="status status--loading" role="status">
          Loading the Cloudflare catalogue…
        </p>
      </section>
    );
  }

  if (state.kind === "error") {
    return (
      <section className="card" aria-labelledby="error-heading">
        <h1 id="error-heading">FreeTier Atlas</h1>
        <div className="status status--error" role="alert">
          <p>Unable to load the catalogue: {state.message}</p>
          <button type="button" onClick={onRetry}>
            Retry
          </button>
        </div>
      </section>
    );
  }

  const { view } = state;
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
