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

/**
 * Read-time evidence currency for one claim.
 *
 * Every other confidence-shaped field on these types is FROZEN at publish time
 * and cannot know that the evidence beneath it later expired. This one is
 * recomputed by the API on every read against the request's clock.
 *
 * `freshness` is `null` -- never `0` -- when `checked` is false. The two
 * render very differently (`formatSignal` shows `null` as "Unknown" and
 * `0` as "0%"), and showing a percentage where no measurement exists would
 * reproduce the defect this field was added to expose.
 */
export interface EvidenceCurrency {
  /** The single predicate to gate a FREE claim on. */
  current: boolean;
  /** Was a currency check possible at all (did a fetch time exist)? */
  checked: boolean;
  /** True only when we checked AND the evidence is past its window. */
  stale: boolean;
  /** Read-time freshness in [0,1]; null when no check was possible. */
  freshness: number | null;
  age_days: number | null;
  window_days: number | null;
  oldest_fetched_at: string | null;
  /** Plain-language explanation; null when current. */
  reason: string | null;
}
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
  evidence_currency: EvidenceCurrency;
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
  evidence_currency: EvidenceCurrency;
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
  evidence_currency: EvidenceCurrency;
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
  evidence_currency: EvidenceCurrency;
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
  evidence_currency: EvidenceCurrency;
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
  evidence_currency: EvidenceCurrency;
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

// --- Catalogue-wide search (F006) --------------------------------------------

export interface SearchResultItem {
  offer_id: number;
  provider_slug: string;
  provider_name: string;
  service_id: number;
  service_name: string;
  category: CategoryRef | null;
  offer_type: string;
  zero_cost_class: string;
  status: string;
  confidence_label: string;
  current_version_number: number | null;
  evidence_currency: EvidenceCurrency;
}

export interface SearchFilters {
  q: string | null;
  provider: string | null;
  category: string | null;
  zero_cost_class: string | null;
  offer_type: string | null;
  commercial_use: boolean | null;
  status: string | null;
  /**
   * Evidence currency — a filter dimension SEPARATE from `zero_cost_class`.
   * The class classifies the offer's terms; this filters on the state of the
   * evidence behind it. `null` means "any", which is the default: an offer whose
   * evidence has expired is returned LABELLED rather than hidden, because a
   * wrongly-omitted free offer is its own defect and an omission is invisible in
   * a way a label is not.
   */
  evidence_current: boolean | null;
}

export interface SearchResponse {
  filters: SearchFilters;
  page: number;
  page_size: number;
  total_results: number;
  total_pages: number;
  results: SearchResultItem[];
}

/**
 * The filter/query inputs a caller may supply to {@link fetchSearch}.
 *
 * Every field is an internal identifier, enum, or bounded keyword — never a URL
 * or host. Values are appended as query-string parameters onto the FIXED
 * `/catalogue/search` path, so there is no way to redirect the request.
 */
export interface SearchQuery {
  q?: string | null;
  provider?: string | null;
  category?: string | null;
  zero_cost_class?: string | null;
  offer_type?: string | null;
  commercial_use?: boolean | null;
  status?: string | null;
  evidence_current?: boolean | null;
  page?: number;
}

// --- Category coverage matrix (F006) -----------------------------------------

export interface ProviderCoverage {
  provider_slug: string;
  provider_name: string;
  /**
   * What to display: `unknown` when nothing is declared, `conflicting` when the
   * declaration and the derivation materially disagree, otherwise the
   * declaration. Produced by the API and never re-derived here.
   */
  state: string;
  /** The human declaration, or `null` when the pair has none. */
  declared_state?: string | null;
  /**
   * What the published catalogue supports right now, recomputed per request and
   * never stored. Never `not_offered` — an empty catalogue means "not verified".
   */
  derived_state?: string;
  /** True when the declaration and the derivation materially disagree. */
  mismatch?: boolean;
  rationale?: string | null;
  evidence_url?: string | null;
  published_offer_count: number;
  free_offer_count: number;
}

export interface CategoryMatrixRow {
  ordinal: number;
  slug: string;
  name: string;
  providers: ProviderCoverage[];
}

export interface UncategorizedCoverage {
  provider_slug: string;
  provider_name: string;
  published_offer_count: number;
  free_offer_count: number;
}

export interface CategoryMatrixResponse {
  provider_slugs: string[];
  categories: CategoryMatrixRow[];
  uncategorized: UncategorizedCoverage[];
}

// --- Compare (F006) ----------------------------------------------------------

export interface NormalizedQuota extends Quota {
  normalized: boolean;
  canonical_amount: number | null;
  canonical_unit: string | null;
  dimension: string | null;
  normalization_note: string | null;
}

export interface CompareOffer {
  offer_id: number;
  provider_slug: string;
  provider_name: string;
  service_id: number;
  service_name: string;
  category: CategoryRef | null;
  offer_type: string;
  zero_cost_class: string;
  status: string;
  requires_card: boolean | null;
  has_paid_dependencies: boolean | null;
  commercial_use_allowed: boolean | null;
  personal_use_allowed: boolean | null;
  reasons: string[];
  blocking_conditions: string[];
  quotas: NormalizedQuota[];
  confidence_label: string;
  completeness: number | null;
  freshness: number | null;
  evidence_count: number;
  advanced: ConfidenceAdvanced;
  evidence_currency: EvidenceCurrency;
}

export interface CompareResponse {
  offer_ids: number[];
  offers: CompareOffer[];
}

// --- Adviser recommendation (F006 slice 4) -----------------------------------
//
// Request/response shapes mirror the backend adviser contract
// (`apps/api/app/adviser/schema.py` for the request and
// `apps/api/app/adviser/schemas.py` for the response). The request is a plain
// STRUCTURED workload — never natural language, never a URL — and the response
// is the deterministic, evidence-backed recommendation the UI renders verbatim.
// Every amount that participates in a fit/headroom decision is a string so the
// exact Decimal survives without a float round-trip; fields that may genuinely
// be unknown are `... | null` so the UI must render "Unknown" rather than guess.

/** One quantified demand within a requirement (metric + exact amount + unit). */
export interface AdviserDemand {
  metric: string;
  /** Exact amount as a string (preserves the Decimal; e.g. "5", "3000000"). */
  amount: string;
  unit: string;
  period?: string | null;
}

/** Non-quantitative constraints an offer must satisfy to be recommended. */
export interface AdviserConstraints {
  commercial_use?: boolean;
  personal_use_ok?: boolean;
  region?: string | null;
  residency?: string | null;
}

/** A single component the workload needs, in one canonical category. */
export interface AdviserRequirement {
  category: string;
  capabilities?: string[];
  demands: AdviserDemand[];
  constraints?: AdviserConstraints;
  label?: string | null;
}

/** The full structured workload — the POST body for the adviser. */
export interface RecommendationRequest {
  workload_name?: string | null;
  requirements: AdviserRequirement[];
}

/** A reference to the selected offer/provider for a component. */
export interface AdviserOfferRef {
  provider_slug: string;
  provider_name: string;
  service_name: string;
  offer_id: number;
  zero_cost_class: string;
  confidence_label: string;
}

export interface AdviserEvidenceRef {
  title: string | null;
  url: string | null;
  official: boolean;
}

export interface AdviserPortability {
  /** Deterministic portability score in [0,1] as a string (e.g. "0.60"). */
  score: string;
  label: string;
  lock_in_label: string;
  deployment_model: string;
  positive_traits: string[];
  negative_traits: string[];
  unknown_traits: string[];
  basis: string[];
  exit_plan: string[];
}

/** Per-demand fit/headroom math for a component (exact, as the API returns). */
export interface AdviserDemandFit {
  metric: string;
  covered: boolean;
  boundary: boolean;
  demand_amount: string;
  demand_unit: string;
  demand_period: string | null;
  matched_metric: string | null;
  canonical_unit: string | null;
  demand_canonical: string | null;
  quota_canonical: string | null;
  headroom: string | null;
  reason: string;
}

/** One recommended Z0 component satisfying a requirement (possibly reduced). */
export interface AdviserComponent {
  requirement_index: number;
  category: string;
  label: string | null;
  offer: AdviserOfferRef;
  reduced: boolean;
  demands: AdviserDemandFit[];
  quota_math: string[];
  z0_safety: string[];
  portability: AdviserPortability;
  evidence: AdviserEvidenceRef[];
  explanation: string[];
}

export interface AdviserReduction {
  metric: string;
  original_amount: string;
  original_unit: string;
  reduced_amount: string | null;
  feasible: boolean;
  reason: string;
}

export interface AdviserSelfHosting {
  building_block: AdviserOfferRef;
  host: AdviserOfferRef | null;
  note: string;
}

/** The ordered resolution of a blocking requirement (no fitting Z0 offer). */
export interface AdviserImpossible {
  requirement_index: number;
  category: string;
  label: string | null;
  blocking_reason: string;
  closest: AdviserOfferRef | null;
  reductions: AdviserReduction[];
  recalculated: AdviserComponent | null;
  self_hosting: AdviserSelfHosting[];
  steps: string[];
}

export interface AdviserNotFreeOption {
  requirement_index: number;
  category: string;
  offer: AdviserOfferRef;
  fits: boolean;
  note: string;
}

export interface AdviserNotFreeSection {
  label: string;
  options: AdviserNotFreeOption[];
}

/** The complete deterministic recommendation the adviser returns. */
export interface RecommendationResponse {
  workload_name: string | null;
  priorities: string[];
  fully_zero_cost: boolean;
  zero_cost_proof: string[];
  architecture: AdviserComponent[];
  impossible: AdviserImpossible[];
  not_free_section: AdviserNotFreeSection;
}

/** One validated file in a deployment bundle (contents returned as text). */
export interface ExportFile {
  path: string;
  content: string;
  sha256: string;
  size: number;
}

/** The server-produced generation manifest describing the validated bundle. */
export interface ExportManifest {
  schema_version: number;
  generator: string;
  workload_name: string | null;
  fully_zero_cost: boolean;
  platforms: string[];
  files: { path: string; sha256: string; size: number }[];
  total_bytes: number;
  file_count: number;
  validation: Record<string, boolean>;
  architecture: Record<string, unknown>[];
  self_hosting_required: Record<string, unknown>[];
  notes: string[];
}

/**
 * The deployment export the server returns for a recommendation.
 *
 * The contents are produced in-memory and streamed transiently — the server
 * persists **nothing** to disk or the database; the browser assembles the
 * `.zip` client-side from `files`.
 */
export interface DeploymentExport {
  workload_name: string | null;
  fully_zero_cost: boolean;
  files: ExportFile[];
  manifest: ExportManifest;
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

/**
 * Issue a `POST` of a JSON `body` to `${API_BASE}${path}` and parse the reply.
 *
 * `path` is always a FIXED, internally-constructed API path — never a
 * caller-supplied URL — and `body` is a structured request object (never a
 * free-text description or a URL), so the request presents no SSRF surface. A
 * `422` (the API rejected the structured request) is surfaced as an actionable,
 * credential-free message; the raw server body is never echoed back.
 */
async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    throw new Error("Unable to reach the API. Is the stack running?");
  }

  if (response.status === 422) {
    throw new Error(
      "The requirements were rejected by the API. Please review the values and retry.",
    );
  }
  if (!response.ok) {
    throw new Error(`The adviser API returned HTTP ${response.status}.`);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new Error("The adviser API response was not valid JSON.");
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

export function fetchProviderOffers(slug: string, signal?: AbortSignal): Promise<OfferSummary[]> {
  return getJson<OfferSummary[]>(`/catalogue/providers/${encodeURIComponent(slug)}/offers`, signal);
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

/** List every provider (used to populate the provider filter option list). */
export function fetchProviders(signal?: AbortSignal): Promise<ProviderSummary[]> {
  return getJson<ProviderSummary[]>("/catalogue/providers", signal);
}

/**
 * Run a catalogue-wide search + filter over the published catalogue.
 *
 * The request is always a `GET` against the FIXED `/catalogue/search` path; the
 * caller's inputs are appended as query-string parameters (keyword, internal
 * slugs, enum filters, page). No value is ever treated as a URL/host or fetched,
 * so there is no SSRF surface. Empty/absent inputs are simply omitted.
 */
export function fetchSearch(
  query: SearchQuery = {},
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams();
  const add = (key: string, value: string | null | undefined) => {
    if (value !== null && value !== undefined && value !== "") params.set(key, value);
  };
  add("q", query.q);
  add("provider", query.provider);
  add("category", query.category);
  add("zero_cost_class", query.zero_cost_class);
  add("offer_type", query.offer_type);
  if (query.commercial_use === true || query.commercial_use === false) {
    params.set("commercial_use", String(query.commercial_use));
  }
  add("status", query.status);
  if (query.evidence_current === true || query.evidence_current === false) {
    params.set("evidence_current", String(query.evidence_current));
  }
  if (query.page && query.page > 1) params.set("page", String(query.page));

  const suffix = params.toString();
  return getJson<SearchResponse>(`/catalogue/search${suffix ? `?${suffix}` : ""}`, signal);
}

/** Fetch the 14-category coverage matrix crossed with every provider. */
export function fetchCategoryMatrix(signal?: AbortSignal): Promise<CategoryMatrixResponse> {
  return getJson<CategoryMatrixResponse>("/catalogue/categories", signal);
}

/**
 * Fetch a normalized side-by-side comparison of a bounded set of offers.
 *
 * `offerIds` are internal integer identifiers; they are joined into the fixed
 * `/catalogue/compare?offers=1,2,3` path. Nothing caller-supplied is ever used
 * as a URL/host.
 */
export function fetchCompare(offerIds: number[], signal?: AbortSignal): Promise<CompareResponse> {
  const ids = offerIds.map((id) => String(id)).join(",");
  return getJson<CompareResponse>(`/catalogue/compare?offers=${encodeURIComponent(ids)}`, signal);
}

/**
 * Ask the deterministic adviser for a $0 architecture recommendation.
 *
 * The `request` is a STRUCTURED workload (never natural language, never a URL);
 * it is POSTed to the FIXED same-origin `/adviser/recommend` path. Nothing
 * caller-supplied is ever used as a URL/host, so there is no SSRF surface. The
 * endpoint is stateless and read-only — this call neither writes nor mutates
 * anything. The response is rendered verbatim by the UI (which never re-derives
 * the Z0 class, confidence, or quota math).
 */
export function fetchRecommendation(
  request: RecommendationRequest,
  signal?: AbortSignal,
): Promise<RecommendationResponse> {
  return postJson<RecommendationResponse>("/adviser/recommend", request, signal);
}

// --- Assisted (natural-language) intake (F007 slice 1) ------------------------
//
// Mirrors apps/api/app/adviser/assist_schema.py. The assisted endpoint turns a
// free-text description into a *candidate* structured request via a routing
// ladder (deterministic parser -> optional, consent-gated LLM tiers ->
// deterministic fallback), validates it through the SAME strict schema, and — on
// success — returns the SAME deterministic recommendation. The UI never
// re-derives the Z0 class, confidence, or quota math; it renders what the API
// returns and honestly reports "couldn't interpret" when nothing was parsed.

/** An explicit, per-request consent to external LLM processing (ephemeral). */
export interface ConsentAssertion {
  external_processing: boolean;
}

/** The POST body for the assisted intake endpoint. */
export interface AssistedRequest {
  description: string;
  consent?: ConsentAssertion | null;
}

/** How the request was routed (interpreter provenance, not a Z0 decision). */
export interface RoutingInfo {
  llm_used: boolean;
  llm_provider: string | null;
  tier: string;
  routing_path: string[];
  fallback_reason: string | null;
}

/** Echo of the ephemeral consent decision for this request only. */
export interface ConsentEcho {
  external_processing_requested: boolean;
  external_processing_used: boolean;
}

/** The assisted-intake response: interpretation + deterministic recommendation. */
export interface AssistedRecommendationResponse {
  interpreted: boolean;
  interpretation: RecommendationRequest | null;
  recommendation: RecommendationResponse | null;
  routing: RoutingInfo;
  consent: ConsentEcho;
  notice: string;
}

/**
 * Ask the adviser to interpret a free-text description, then recommend.
 *
 * The `request` carries a plain natural-language `description` (never a URL) and
 * an optional ephemeral `consent` assertion; it is POSTed to the FIXED
 * same-origin `/adviser/recommend/assisted` path. Nothing caller-supplied is
 * ever used as a URL/host, so there is no SSRF surface. The description is not
 * persisted or logged by the API. The response is rendered verbatim by the UI.
 */
export function fetchAssistedRecommendation(
  request: AssistedRequest,
  signal?: AbortSignal,
): Promise<AssistedRecommendationResponse> {
  return postJson<AssistedRecommendationResponse>("/adviser/recommend/assisted", request, signal);
}

/**
 * Ask the server for the validated, secret-free deployment bundle for a
 * recommendation.
 *
 * The same STRUCTURED `request` (never natural language, never a URL) is POSTed
 * to the FIXED same-origin `/adviser/export` path — no SSRF surface. The server
 * recomputes the recommendation, generates the deployment files, validates them
 * fail-closed, and returns their CONTENTS plus a manifest **without persisting
 * anything**. The browser assembles the `.zip` from `files` client-side.
 */
export function fetchDeploymentExport(
  request: RecommendationRequest,
  signal?: AbortSignal,
): Promise<DeploymentExport> {
  return postJson<DeploymentExport>("/adviser/export", request, signal);
}
