/**
 * Read-only client for the FreeTier Atlas catalogue API.
 *
 * The frontend talks to the API through the relative `/api` prefix. In the
 * container an nginx reverse proxy forwards `/api/` to the api service; during
 * local development Vite proxies it to the API on localhost. This keeps the app
 * same-origin and free of hard-coded hosts or CORS configuration.
 *
 * Every function here issues a plain `GET` against a FIXED path built only from
 * internal identifiers (a provider slug, an offer id). No URL, host, or endpoint
 * is ever accepted from the caller and fetched, so there is no SSRF surface. The
 * client only reads the *published* catalogue exposed by the S3 read API — it
 * never writes, mutates, or touches the database directly.
 */

/** The base path for API calls; overridable via VITE_API_BASE at build time. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

// --- Health (retained from the F002 scaffold; still used by the footer) -------

export interface ApiHealth {
  status: string;
  service: string;
  version: string;
  environment: string;
}

// --- Catalogue response shapes (mirror apps/api/app/read_api/schemas.py) ------
//
// Fields that the API may return as `null` are typed as `... | null` so the UI
// is forced to handle "unknown" honestly rather than assume a value.

export interface CategoryRef {
  slug: string;
  name: string;
}

export interface ProviderSummary {
  slug: string;
  name: string;
  type: string;
  source_health: string | null;
  completeness: number | null;
  freshness: number | null;
  service_count: number;
  published_offer_count: number;
}

export interface ProviderDetail extends ProviderSummary {
  official_domains: string[];
}

export interface ConfidenceAdvanced {
  score: number | null;
  signals: Record<string, number | null> | null;
}

export interface OfferVersion {
  id: number;
  version_number: number;
  zero_cost_class: string;
  confidence_label: string;
  reasons: string[];
  content_hash: string;
  created_at: string | null;
}

export interface Quota {
  metric: string;
  amount: number | null;
  unit: string | null;
  reset_period: string | null;
  scope: string | null;
  region_scope: string | null;
  behaviour: string;
  exhaustion_behaviour: string;
  retention_policy: string | null;
}

export interface OfferState {
  offer_id: number;
  offer_type: string;
  zero_cost_class: string;
  confidence_label: string;
  status: string;
}

export interface ServiceState {
  service_id: number;
  canonical_name: string;
  deployment_model: string;
  category: CategoryRef | null;
  offers: OfferState[];
}

export interface CategoryGroup {
  category: CategoryRef | null;
  services: ServiceState[];
}

export interface CategoryStatesResponse {
  provider_slug: string;
  provider_name: string;
  categories: CategoryGroup[];
}

export interface OfferSummary {
  offer_id: number;
  service_id: number;
  service_name: string;
  category: CategoryRef | null;
  offer_type: string;
  zero_cost_class: string;
  status: string;
  confidence_label: string;
  current_version_number: number | null;
}

export interface OfferDetail {
  offer_id: number;
  provider_slug: string;
  provider_name: string;
  service_id: number;
  service_name: string;
  category: CategoryRef | null;
  deployment_model: string;
  offer_type: string;
  zero_cost_class: string;
  status: string;
  eligibility: string | null;
  requires_card: boolean | null;
  has_paid_dependencies: boolean | null;
  commercial_use_allowed: boolean | null;
  personal_use_allowed: boolean | null;
  first_seen_at: string | null;
  last_verified_at: string | null;
  current_version: OfferVersion | null;
  reasons: string[];
  blocking_conditions: string[];
  quotas: Quota[];
  confidence_label: string;
  completeness: number | null;
  freshness: number | null;
  advanced: ConfidenceAdvanced;
}

export interface EvidenceSource {
  id: number;
  slug: string | null;
  adapter_type: string;
  trust_level: string;
  official: boolean;
  endpoint: string | null;
}

export interface EvidenceSnapshot {
  id: number;
  content_location: string;
  mime_type: string | null;
  content_hash: string;
  fetched_at: string | null;
}

export interface Evidence {
  id: number;
  official: boolean;
  url: string | null;
  title: string | null;
  excerpt: string | null;
  content_hash: string;
  retrieved_at: string | null;
  effective_at: string | null;
  selector: string | null;
  offer_version_id: number | null;
  source: EvidenceSource;
  snapshot: EvidenceSnapshot;
}

export interface OfferEvidenceResponse {
  offer_id: number;
  offer_version_id: number | null;
  confidence_label: string;
  advanced: ConfidenceAdvanced;
  evidence: Evidence[];
}

export interface ChangeEvent {
  id: number;
  change_type: string;
  materiality: string;
  publication_status: string;
  previous_version_id: number | null;
  new_version_id: number | null;
  occurred_at: string | null;
}

export interface OfferHistoryResponse {
  offer_id: number;
  versions: OfferVersion[];
  change_events: ChangeEvent[];
}

// --- Fetch helper -------------------------------------------------------------

/**
 * Issue a `GET` against `${API_BASE}${path}` and parse a JSON body.
 *
 * `path` is always a fixed, internally-constructed catalogue path — never a
 * caller-supplied URL. Errors are surfaced with actionable, credential-free
 * messages so the UI can show them safely.
 */
async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      signal,
    });
  } catch {
    throw new Error("Unable to reach the API. Is the stack running?");
  }

  if (response.status === 404) {
    throw new Error("Not found in the published catalogue.");
  }
  if (!response.ok) {
    throw new Error(`The catalogue API returned HTTP ${response.status}.`);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new Error("The catalogue API response was not valid JSON.");
  }
}

// --- Endpoint functions -------------------------------------------------------

export function fetchApiHealth(signal?: AbortSignal): Promise<ApiHealth> {
  return getJson<ApiHealth>("/health", signal);
}

export function fetchProvider(slug: string, signal?: AbortSignal): Promise<ProviderDetail> {
  return getJson<ProviderDetail>(`/catalogue/providers/${encodeURIComponent(slug)}`, signal);
}

export function fetchCategoryStates(
  slug: string,
  signal?: AbortSignal,
): Promise<CategoryStatesResponse> {
  return getJson<CategoryStatesResponse>(
    `/catalogue/providers/${encodeURIComponent(slug)}/category-states`,
    signal,
  );
}

export function fetchProviderOffers(
  slug: string,
  signal?: AbortSignal,
): Promise<OfferSummary[]> {
  return getJson<OfferSummary[]>(
    `/catalogue/providers/${encodeURIComponent(slug)}/offers`,
    signal,
  );
}

export function fetchOffer(offerId: number, signal?: AbortSignal): Promise<OfferDetail> {
  return getJson<OfferDetail>(`/catalogue/offers/${offerId}`, signal);
}

export function fetchOfferEvidence(
  offerId: number,
  signal?: AbortSignal,
): Promise<OfferEvidenceResponse> {
  return getJson<OfferEvidenceResponse>(`/catalogue/offers/${offerId}/evidence`, signal);
}

export function fetchOfferHistory(
  offerId: number,
  signal?: AbortSignal,
): Promise<OfferHistoryResponse> {
  return getJson<OfferHistoryResponse>(`/catalogue/offers/${offerId}/history`, signal);
}
