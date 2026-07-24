import {
  fetchCategoryStates,
  fetchOffer,
  fetchOfferEvidence,
  fetchOfferHistory,
  fetchProvider,
  fetchProviderOffers,
} from "../api";
import type { CategoryStatesResponse, ProviderDetail } from "../api";
import type { OfferBundle } from "./OfferCard";

/** The full, assembled view model for one provider page. */
export interface CatalogueView {
  provider: ProviderDetail;
  categoryStates: CategoryStatesResponse;
  offers: OfferBundle[];
}

/**
 * Load a provider's complete public view from the read-only catalogue API.
 *
 * Fetches the provider detail, category states, and offer list in parallel,
 * then loads each offer's detail + evidence + history. All calls go through the
 * same-origin `/api` client; nothing here writes or touches the database.
 */
export async function loadCatalogue(
  slug: string,
  signal?: AbortSignal,
): Promise<CatalogueView> {
  const [provider, categoryStates, offerList] = await Promise.all([
    fetchProvider(slug, signal),
    fetchCategoryStates(slug, signal),
    fetchProviderOffers(slug, signal),
  ]);

  const offers = await Promise.all(
    offerList.map(async (summary): Promise<OfferBundle> => {
      const [detail, evidence, history] = await Promise.all([
        fetchOffer(summary.offer_id, signal),
        fetchOfferEvidence(summary.offer_id, signal),
        fetchOfferHistory(summary.offer_id, signal),
      ]);
      return { detail, evidence, history };
    }),
  );

  offers.sort((a, b) => a.detail.offer_id - b.detail.offer_id);
  return { provider, categoryStates, offers };
}
