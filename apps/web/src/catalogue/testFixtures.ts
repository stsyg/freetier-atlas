import type {
  CategoryMatrixResponse,
  CompareOffer,
  CompareResponse,
  CategoryStatesResponse,
  OfferDetail,
  OfferEvidenceResponse,
  OfferHistoryResponse,
  OfferSummary,
  ProviderDetail,
  ProviderSummary,
  SearchResponse,
  SearchResultItem,
} from "../api";

/**
 * Deterministic, offline fixtures mirroring the S3 read API responses for the
 * Cloudflare provider. Offer 1 (Workers) is a fully-populated Z0_TRUE_FREE
 * offer; offer 2 (Pages) deliberately carries unknown/null fields so tests can
 * assert the UI reports "Unknown" honestly instead of fabricating values.
 */

export const provider: ProviderDetail = {
  slug: "cloudflare",
  name: "Cloudflare",
  type: "cloud_platform",
  source_health: "healthy",
  completeness: 0.92,
  freshness: 0.8,
  service_count: 2,
  published_offer_count: 2,
  official_domains: ["cloudflare.com", "developers.cloudflare.com"],
};

export const categoryStates: CategoryStatesResponse = {
  provider_slug: "cloudflare",
  provider_name: "Cloudflare",
  categories: [
    {
      category: { slug: "compute", name: "Compute" },
      services: [
        {
          service_id: 1,
          canonical_name: "Cloudflare Workers",
          deployment_model: "serverless",
          category: { slug: "compute", name: "Compute" },
          offers: [
            {
              offer_id: 1,
              offer_type: "free_tier",
              zero_cost_class: "Z0_TRUE_FREE",
              confidence_label: "high",
              status: "published",
            },
          ],
        },
      ],
    },
    {
      category: { slug: "hosting", name: "Hosting" },
      services: [
        {
          service_id: 2,
          canonical_name: "Cloudflare Pages",
          deployment_model: "managed",
          category: { slug: "hosting", name: "Hosting" },
          offers: [
            {
              offer_id: 2,
              offer_type: "free_tier",
              zero_cost_class: "UNKNOWN",
              confidence_label: "unknown",
              status: "published",
            },
          ],
        },
      ],
    },
  ],
};

export const offerSummaries: OfferSummary[] = [
  {
    offer_id: 1,
    service_id: 1,
    service_name: "Cloudflare Workers",
    category: { slug: "compute", name: "Compute" },
    offer_type: "free_tier",
    zero_cost_class: "Z0_TRUE_FREE",
    status: "published",
    confidence_label: "high",
    current_version_number: 1,
  },
  {
    offer_id: 2,
    service_id: 2,
    service_name: "Cloudflare Pages",
    category: { slug: "hosting", name: "Hosting" },
    offer_type: "free_tier",
    zero_cost_class: "UNKNOWN",
    status: "published",
    confidence_label: "unknown",
    current_version_number: null,
  },
];

export const offerDetail1: OfferDetail = {
  offer_id: 1,
  provider_slug: "cloudflare",
  provider_name: "Cloudflare",
  service_id: 1,
  service_name: "Cloudflare Workers",
  category: { slug: "compute", name: "Compute" },
  deployment_model: "serverless",
  offer_type: "free_tier",
  zero_cost_class: "Z0_TRUE_FREE",
  status: "published",
  eligibility: "all_users",
  requires_card: false,
  has_paid_dependencies: false,
  commercial_use_allowed: true,
  personal_use_allowed: true,
  first_seen_at: "2024-01-01T00:00:00Z",
  last_verified_at: "2024-06-01T00:00:00Z",
  current_version: {
    id: 11,
    version_number: 1,
    zero_cost_class: "Z0_TRUE_FREE",
    confidence_label: "high",
    reasons: ["No credit card is required to start."],
    content_hash: "sha256:workers-v1",
    created_at: "2024-06-01T00:00:00Z",
  },
  reasons: [
    "No credit card is required to start.",
    "Free requests reset daily and never incur charges.",
  ],
  blocking_conditions: [],
  quotas: [
    {
      metric: "requests_per_day",
      amount: 100000,
      unit: "requests",
      reset_period: "daily",
      scope: "account",
      region_scope: null,
      behaviour: "hard_limit",
      exhaustion_behaviour: "requests_blocked",
      retention_policy: null,
    },
  ],
  confidence_label: "high",
  completeness: 0.95,
  freshness: 0.9,
  advanced: {
    score: 0.91,
    signals: { evidence_freshness: 0.9, source_trust: 1.0 },
  },
};

export const offerDetail2: OfferDetail = {
  offer_id: 2,
  provider_slug: "cloudflare",
  provider_name: "Cloudflare",
  service_id: 2,
  service_name: "Cloudflare Pages",
  category: { slug: "hosting", name: "Hosting" },
  deployment_model: "managed",
  offer_type: "free_tier",
  zero_cost_class: "UNKNOWN",
  status: "published",
  eligibility: null,
  requires_card: null,
  has_paid_dependencies: null,
  commercial_use_allowed: null,
  personal_use_allowed: null,
  first_seen_at: null,
  last_verified_at: null,
  current_version: null,
  reasons: [],
  blocking_conditions: ["Billing details could not be verified from official sources."],
  quotas: [],
  confidence_label: "unknown",
  completeness: null,
  freshness: null,
  advanced: { score: null, signals: null },
};

export const offerEvidence1: OfferEvidenceResponse = {
  offer_id: 1,
  offer_version_id: 11,
  confidence_label: "high",
  advanced: { score: 0.91, signals: { evidence_freshness: 0.9 } },
  evidence: [
    {
      id: 101,
      official: true,
      url: "https://developers.cloudflare.com/workers/platform/pricing/",
      title: "Workers pricing — free plan",
      excerpt: "The Free plan includes 100,000 requests per day.",
      content_hash: "sha256:evidence-101",
      retrieved_at: "2024-06-01T00:00:00Z",
      effective_at: "2024-06-01T00:00:00Z",
      selector: "main",
      offer_version_id: 11,
      source: {
        id: 5,
        slug: "cloudflare-docs",
        adapter_type: "http_docs",
        trust_level: "official_docs",
        official: true,
        endpoint: "https://developers.cloudflare.com",
      },
      snapshot: {
        id: 201,
        content_location: "snapshots/201.html",
        mime_type: "text/html",
        content_hash: "sha256:snapshot-201",
        fetched_at: "2024-06-01T00:00:00Z",
      },
    },
  ],
};

export const offerEvidence2: OfferEvidenceResponse = {
  offer_id: 2,
  offer_version_id: null,
  confidence_label: "unknown",
  advanced: { score: null, signals: null },
  evidence: [],
};

export const offerHistory1: OfferHistoryResponse = {
  offer_id: 1,
  versions: [
    {
      id: 11,
      version_number: 1,
      zero_cost_class: "Z0_TRUE_FREE",
      confidence_label: "high",
      reasons: ["No credit card is required to start."],
      content_hash: "sha256:workers-v1",
      created_at: "2024-06-01T00:00:00Z",
    },
  ],
  change_events: [
    {
      id: 301,
      change_type: "offer_added",
      materiality: "material",
      publication_status: "published",
      previous_version_id: null,
      new_version_id: 11,
      occurred_at: "2024-06-01T00:00:00Z",
    },
  ],
};

export const offerHistory2: OfferHistoryResponse = {
  offer_id: 2,
  versions: [],
  change_events: [],
};

// --- F006 slice 2: provider-agnostic search / matrix / compare fixtures ------
//
// These fixtures deliberately span MULTIPLE synthetic providers (cloudflare plus
// two invented providers) so the tests prove the catalogue browser renders any
// provider from the API, not a hard-coded Cloudflare view. The synthetic data is
// clearly fictional and only ever reached through a mocked `fetch`; it never
// leaks a real-world free-tier claim.

/** Provider list used to populate the provider filter (GET /catalogue/providers). */
export const providerList: ProviderSummary[] = [
  {
    slug: "cloudflare",
    name: "Cloudflare",
    type: "cloud_platform",
    source_health: "healthy",
    completeness: 0.92,
    freshness: 0.8,
    service_count: 2,
    published_offer_count: 2,
  },
  {
    slug: "northwind-cloud",
    name: "Northwind Cloud",
    type: "cloud_platform",
    source_health: "healthy",
    completeness: 0.7,
    freshness: 0.6,
    service_count: 1,
    published_offer_count: 1,
  },
  {
    slug: "acme-serverless",
    name: "Acme Serverless",
    type: "cloud_platform",
    source_health: "degraded",
    completeness: 0.5,
    freshness: 0.4,
    service_count: 2,
    published_offer_count: 2,
  },
];

/** The full synthetic search index; the mock filters/paginates over this. */
export const searchIndex: SearchResultItem[] = [
  {
    offer_id: 1,
    provider_slug: "cloudflare",
    provider_name: "Cloudflare",
    service_id: 1,
    service_name: "Cloudflare Workers",
    category: { slug: "serverless-functions", name: "Serverless functions" },
    offer_type: "always_free",
    zero_cost_class: "Z0_TRUE_FREE",
    status: "active",
    confidence_label: "high",
    current_version_number: 1,
  },
  {
    offer_id: 2,
    provider_slug: "cloudflare",
    provider_name: "Cloudflare",
    service_id: 2,
    service_name: "Cloudflare Pages",
    category: { slug: "containers-app-hosting", name: "Containers & app hosting" },
    offer_type: "recurring_quota",
    zero_cost_class: "UNKNOWN",
    status: "active",
    confidence_label: "unknown",
    current_version_number: null,
  },
  {
    offer_id: 3,
    provider_slug: "northwind-cloud",
    provider_name: "Northwind Cloud",
    service_id: 3,
    service_name: "Northwind Postgres",
    category: { slug: "relational-databases", name: "Relational databases" },
    offer_type: "trial",
    zero_cost_class: "Z2_TEMPORARY_OR_CONDITIONAL",
    status: "active",
    confidence_label: "medium",
    current_version_number: 2,
  },
  {
    offer_id: 4,
    provider_slug: "acme-serverless",
    provider_name: "Acme Serverless",
    service_id: 4,
    service_name: "Acme Functions",
    category: { slug: "serverless-functions", name: "Serverless functions" },
    offer_type: "always_free",
    zero_cost_class: "Z0_TRUE_FREE",
    status: "active",
    confidence_label: "high",
    current_version_number: 1,
  },
  {
    offer_id: 5,
    provider_slug: "acme-serverless",
    provider_name: "Acme Serverless",
    service_id: 5,
    service_name: "Acme Object Store",
    category: { slug: "object-file-storage", name: "Object & file storage" },
    offer_type: "recurring_quota",
    zero_cost_class: "Z1_BILLING_EXPOSURE",
    status: "deprecated",
    confidence_label: "low",
    current_version_number: 3,
  },
];

/** Fixed page size the mocked search endpoint applies (5 items -> 2 pages). */
export const SEARCH_PAGE_SIZE = 3;

/** The 14 canonical categories (slug + display name), mirroring taxonomy.py. */
const CANONICAL_CATEGORIES: { slug: string; name: string }[] = [
  { slug: "compute-vms", name: "Compute (VMs)" },
  { slug: "containers-app-hosting", name: "Containers & app hosting" },
  { slug: "serverless-functions", name: "Serverless functions" },
  { slug: "relational-databases", name: "Relational databases" },
  { slug: "nosql-key-value", name: "NoSQL & key-value" },
  { slug: "object-file-storage", name: "Object & file storage" },
  { slug: "networking-cdn-dns", name: "Networking, CDN & DNS" },
  { slug: "queues-messaging-jobs", name: "Queues, messaging & jobs" },
  { slug: "auth-identity", name: "Auth & identity" },
  { slug: "cicd-source-control", name: "CI/CD & source control" },
  { slug: "monitoring-logs-tracing", name: "Monitoring, logs & tracing" },
  { slug: "ai-inference-embeddings", name: "AI inference & embeddings" },
  { slug: "email-notifications-comms", name: "Email, notifications & comms" },
  { slug: "secrets-config-devtools", name: "Secrets, config & devtools" },
];

/**
 * Build the coverage matrix from the synthetic search index so the matrix,
 * search, and compare fixtures stay internally consistent.
 *
 * `derived_state` mirrors what the API computes from published offers:
 * "verified_free" when a provider has a truly-free offer in the category,
 * "offered_no_z0" when it has published offers but none truly free, and
 * "unknown" otherwise. It is NEVER "not_offered" — an empty catalogue means
 * nobody has verified the pair, not that the provider declined to offer it.
 * The declaration is left absent for the no-offer case so the displayed `state`
 * falls back to "unknown".
 */
export const categoryMatrix: CategoryMatrixResponse = {
  provider_slugs: providerList.map((p) => p.slug),
  categories: CANONICAL_CATEGORIES.map((category, index) => ({
    ordinal: index + 1,
    slug: category.slug,
    name: category.name,
    providers: providerList.map((p) => {
      const offers = searchIndex.filter(
        (o) => o.provider_slug === p.slug && o.category?.slug === category.slug,
      );
      const free = offers.filter((o) => o.zero_cost_class === "Z0_TRUE_FREE");
      const derived =
        offers.length === 0 ? "unknown" : free.length > 0 ? "verified_free" : "offered_no_z0";
      return {
        provider_slug: p.slug,
        provider_name: p.name,
        state: derived,
        declared_state: derived === "unknown" ? null : derived,
        derived_state: derived,
        mismatch: false,
        rationale: null,
        evidence_url: null,
        published_offer_count: offers.length,
        free_offer_count: free.length,
      };
    }),
  })),
  uncategorized: [
    {
      provider_slug: "northwind-cloud",
      provider_name: "Northwind Cloud",
      published_offer_count: 1,
      free_offer_count: 0,
    },
  ],
};

/**
 * A one-row matrix that exercises every one of the seven coverage states plus
 * the "API returned no entry for this provider" case, so the renderer is pinned
 * against silently collapsing states together.
 */
export const allCoverageStatesMatrix: CategoryMatrixResponse = {
  provider_slugs: [
    "p-verified-free",
    "p-offered-no-z0",
    "p-incomplete",
    "p-stale",
    "p-conflicting",
    "p-not-offered",
    "p-unknown",
    "p-absent",
  ],
  categories: [
    {
      ordinal: 1,
      slug: "compute-vms",
      name: "Compute & VMs",
      providers: [
        {
          provider_slug: "p-verified-free",
          provider_name: "Verified Free Co",
          state: "verified_free",
          declared_state: "verified_free",
          derived_state: "verified_free",
          mismatch: false,
          rationale: null,
          evidence_url: "https://example.invalid/free",
          published_offer_count: 2,
          free_offer_count: 1,
        },
        {
          provider_slug: "p-offered-no-z0",
          provider_name: "Paid Only Co",
          state: "offered_no_z0",
          declared_state: "offered_no_z0",
          derived_state: "offered_no_z0",
          mismatch: false,
          rationale: null,
          evidence_url: "https://example.invalid/pricing",
          published_offer_count: 3,
          free_offer_count: 0,
        },
        {
          provider_slug: "p-incomplete",
          provider_name: "Incomplete Co",
          state: "incomplete",
          declared_state: "incomplete",
          derived_state: "incomplete",
          mismatch: false,
          rationale: null,
          evidence_url: null,
          published_offer_count: 1,
          free_offer_count: 0,
        },
        {
          provider_slug: "p-stale",
          provider_name: "Stale Co",
          state: "stale",
          declared_state: "stale",
          derived_state: "stale",
          mismatch: false,
          rationale: null,
          evidence_url: null,
          published_offer_count: 1,
          free_offer_count: 1,
        },
        {
          provider_slug: "p-conflicting",
          provider_name: "Conflicting Co",
          state: "conflicting",
          declared_state: "unknown",
          derived_state: "verified_free",
          mismatch: true,
          rationale: null,
          evidence_url: null,
          published_offer_count: 1,
          free_offer_count: 1,
        },
        {
          provider_slug: "p-not-offered",
          provider_name: "Declines Co",
          state: "not_offered",
          declared_state: "not_offered",
          derived_state: "unknown",
          mismatch: false,
          rationale: "Declines Co publishes no compute product line.",
          evidence_url: null,
          published_offer_count: 0,
          free_offer_count: 0,
        },
        {
          provider_slug: "p-unknown",
          provider_name: "Unchecked Co",
          state: "unknown",
          declared_state: "unknown",
          derived_state: "unknown",
          mismatch: false,
          rationale: null,
          evidence_url: null,
          published_offer_count: 0,
          free_offer_count: 0,
        },
      ],
    },
  ],
  uncategorized: [],
};

/** Compare fixtures keyed by offer id (GET /catalogue/compare?offers=...). */
export const compareOffers: Record<number, CompareOffer> = {
  1: {
    offer_id: 1,
    provider_slug: "cloudflare",
    provider_name: "Cloudflare",
    service_id: 1,
    service_name: "Cloudflare Workers",
    category: { slug: "serverless-functions", name: "Serverless functions" },
    offer_type: "always_free",
    zero_cost_class: "Z0_TRUE_FREE",
    status: "active",
    requires_card: false,
    has_paid_dependencies: false,
    commercial_use_allowed: true,
    personal_use_allowed: true,
    reasons: ["No credit card is required to start."],
    blocking_conditions: [],
    quotas: [
      {
        metric: "requests_per_day",
        amount: 100000,
        unit: "requests",
        reset_period: "daily",
        scope: "account",
        region_scope: null,
        behaviour: "hard_limit",
        exhaustion_behaviour: "requests_blocked",
        retention_policy: null,
        normalized: true,
        canonical_amount: 3000000,
        canonical_unit: "requests_per_month",
        dimension: "requests",
        normalization_note: "Daily quota projected to a 30-day month.",
      },
    ],
    confidence_label: "high",
    completeness: 0.95,
    freshness: 0.9,
    evidence_count: 3,
    advanced: { score: 0.91, signals: { source_trust: 1.0 } },
  },
  3: {
    offer_id: 3,
    provider_slug: "northwind-cloud",
    provider_name: "Northwind Cloud",
    service_id: 3,
    service_name: "Northwind Postgres",
    category: { slug: "relational-databases", name: "Relational databases" },
    offer_type: "trial",
    zero_cost_class: "Z2_TEMPORARY_OR_CONDITIONAL",
    status: "active",
    requires_card: true,
    has_paid_dependencies: null,
    commercial_use_allowed: null,
    personal_use_allowed: true,
    reasons: ["Free for the first 90 days."],
    blocking_conditions: ["Converts to a paid plan after the trial."],
    quotas: [
      {
        metric: "storage",
        amount: 5,
        unit: "GB",
        reset_period: null,
        scope: "project",
        region_scope: null,
        behaviour: "hard_limit",
        exhaustion_behaviour: "writes_blocked",
        retention_policy: null,
        normalized: false,
        canonical_amount: null,
        canonical_unit: null,
        dimension: null,
        normalization_note: null,
      },
    ],
    confidence_label: "medium",
    completeness: 0.6,
    freshness: 0.55,
    evidence_count: 1,
    advanced: { score: 0.62, signals: null },
  },
  4: {
    offer_id: 4,
    provider_slug: "acme-serverless",
    provider_name: "Acme Serverless",
    service_id: 4,
    service_name: "Acme Functions",
    category: { slug: "serverless-functions", name: "Serverless functions" },
    offer_type: "always_free",
    zero_cost_class: "Z0_TRUE_FREE",
    status: "active",
    requires_card: false,
    has_paid_dependencies: false,
    commercial_use_allowed: false,
    personal_use_allowed: true,
    reasons: ["Always-free allowance with no card."],
    blocking_conditions: [],
    quotas: [],
    confidence_label: "high",
    completeness: 0.8,
    freshness: 0.75,
    evidence_count: 2,
    advanced: { score: 0.83, signals: null },
  },
};

function buildSearchResponse(url: URL): SearchResponse {
  const params = url.searchParams;
  const q = params.get("q");
  const provider = params.get("provider");
  const category = params.get("category");
  const zeroCostClass = params.get("zero_cost_class");
  const offerType = params.get("offer_type");
  const commercialParam = params.get("commercial_use");
  const status = params.get("status");
  const commercialUse = commercialParam === null ? null : commercialParam === "true";
  const page = Math.max(1, Number(params.get("page") ?? "1") || 1);

  // Search over the truly-free subset when a keyword is present so the results
  // reflect the query; every filter below composes with AND semantics.
  let matches = searchIndex.filter((item) => {
    if (
      q &&
      !`${item.service_name} ${item.provider_name}`.toLowerCase().includes(q.toLowerCase())
    ) {
      return false;
    }
    if (provider && item.provider_slug !== provider) return false;
    if (category && item.category?.slug !== category) return false;
    if (zeroCostClass && item.zero_cost_class !== zeroCostClass) return false;
    if (offerType && item.offer_type !== offerType) return false;
    if (status && item.status !== status) return false;
    return true;
  });

  // commercial_use is a synthetic per-offer flag: derive it from the compare
  // fixture where available, otherwise treat unknown as non-matching when the
  // caller filters on it (honest, never fabricated).
  if (commercialUse !== null) {
    matches = matches.filter((item) => {
      const detail = compareOffers[item.offer_id];
      return detail ? detail.commercial_use_allowed === commercialUse : false;
    });
  }

  const totalResults = matches.length;
  const totalPages = Math.max(1, Math.ceil(totalResults / SEARCH_PAGE_SIZE));
  const start = (page - 1) * SEARCH_PAGE_SIZE;
  const results = matches.slice(start, start + SEARCH_PAGE_SIZE);

  return {
    filters: {
      q: q,
      provider: provider,
      category: category,
      zero_cost_class: zeroCostClass,
      offer_type: offerType,
      commercial_use: commercialUse,
      status: status,
    },
    page,
    page_size: SEARCH_PAGE_SIZE,
    total_results: totalResults,
    total_pages: totalPages,
    results,
  };
}

function buildCompareResponse(url: URL): CompareResponse {
  const raw = url.searchParams.get("offers") ?? "";
  const ids = raw
    .split(",")
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isFinite(value) && value > 0);
  const offers = ids
    .map((id) => compareOffers[id])
    .filter((offer): offer is CompareOffer => Boolean(offer));
  return { offer_ids: ids, offers };
}

/**
 * Build a `fetch` implementation that routes catalogue GET requests to the
 * fixtures above. Longer suffixes (`/evidence`, `/history`) are matched before
 * the bare `/offers/{id}` path. Unmapped paths resolve to a 404.
 */
export function catalogueFetch(): typeof fetch {
  return (async (input: RequestInfo | URL): Promise<Response> => {
    const raw = String(input);
    const url = new URL(raw, "http://localhost");
    const path = url.pathname;
    const json = (body: unknown) => Response.json(body);

    // F006 slice 2 collection endpoints (query strings allowed).
    if (path.endsWith("/catalogue/search")) return json(buildSearchResponse(url));
    if (path.endsWith("/catalogue/categories")) return json(categoryMatrix);
    if (path.endsWith("/catalogue/compare")) return json(buildCompareResponse(url));
    if (path.endsWith("/catalogue/providers")) return json(providerList);

    // Single-provider (F005) endpoints.
    if (path.endsWith("/catalogue/providers/cloudflare")) return json(provider);
    if (path.endsWith("/catalogue/providers/cloudflare/category-states")) {
      return json(categoryStates);
    }
    if (path.endsWith("/catalogue/providers/cloudflare/offers")) return json(offerSummaries);

    if (path.endsWith("/catalogue/offers/1/evidence")) return json(offerEvidence1);
    if (path.endsWith("/catalogue/offers/2/evidence")) return json(offerEvidence2);
    if (path.endsWith("/catalogue/offers/1/history")) return json(offerHistory1);
    if (path.endsWith("/catalogue/offers/2/history")) return json(offerHistory2);
    if (path.endsWith("/catalogue/offers/1")) return json(offerDetail1);
    if (path.endsWith("/catalogue/offers/2")) return json(offerDetail2);

    return new Response("not found", { status: 404 });
  }) as typeof fetch;
}
