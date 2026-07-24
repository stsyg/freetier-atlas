import type {
  CategoryStatesResponse,
  OfferDetail,
  OfferEvidenceResponse,
  OfferHistoryResponse,
  OfferSummary,
  ProviderDetail,
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

/**
 * Build a `fetch` implementation that routes catalogue GET requests to the
 * fixtures above. Longer suffixes (`/evidence`, `/history`) are matched before
 * the bare `/offers/{id}` path. Unmapped paths resolve to a 404.
 */
export function catalogueFetch(): typeof fetch {
  return (async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    const json = (body: unknown) => Response.json(body);

    if (url.endsWith("/catalogue/providers/cloudflare")) return json(provider);
    if (url.endsWith("/catalogue/providers/cloudflare/category-states")) {
      return json(categoryStates);
    }
    if (url.endsWith("/catalogue/providers/cloudflare/offers")) return json(offerSummaries);

    if (url.endsWith("/catalogue/offers/1/evidence")) return json(offerEvidence1);
    if (url.endsWith("/catalogue/offers/2/evidence")) return json(offerEvidence2);
    if (url.endsWith("/catalogue/offers/1/history")) return json(offerHistory1);
    if (url.endsWith("/catalogue/offers/2/history")) return json(offerHistory2);
    if (url.endsWith("/catalogue/offers/1")) return json(offerDetail1);
    if (url.endsWith("/catalogue/offers/2")) return json(offerDetail2);

    return new Response("not found", { status: 404 });
  }) as typeof fetch;
}
