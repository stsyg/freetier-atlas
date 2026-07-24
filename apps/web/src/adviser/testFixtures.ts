import type {
  AdviserComponent,
  AdviserImpossible,
  AdviserOfferRef,
  RecommendationRequest,
  RecommendationResponse,
} from "../api";

/**
 * Deterministic, OFFLINE adviser fixtures for the F006 slice-4 web tests.
 *
 * The recommendation payloads here mirror the exact shape of
 * `POST /api/adviser/recommend` (see `apps/api/app/adviser/schemas.py`). They are
 * SYNTHETIC on purpose: the provider names below ("Northwind Cloud", "Acme
 * Serverless", "Globex Data", "Initech Object Store", …) exist ONLY in this test
 * file so we can prove the adviser page renders a provider-AGNOSTIC recommendation
 * across many vendors. No synthetic provider ever leaks into production code, and
 * the live stack only ever shows real published (Cloudflare) data.
 *
 * `mixedRecommendation` intentionally combines: a satisfiable component, a
 * blocking requirement whose full resolution chain (blocking → reduction →
 * recalculation → self-hosting) is populated, and a clearly separated "not $0"
 * (Z1/Z2) section — so tests can assert the strict ordering and separation.
 */

function offer(overrides: Partial<AdviserOfferRef> & { service_name: string }): AdviserOfferRef {
  return {
    provider_slug: "synthetic",
    provider_name: "Synthetic Provider",
    offer_id: 1,
    zero_cost_class: "Z0_TRUE_FREE",
    confidence_label: "high",
    ...overrides,
  };
}

const workersComponent: AdviserComponent = {
  requirement_index: 0,
  category: "serverless-functions",
  label: "Public API",
  offer: offer({
    provider_slug: "northwind",
    provider_name: "Northwind Cloud",
    service_name: "Northwind Functions",
    offer_id: 101,
    zero_cost_class: "Z0_TRUE_FREE",
    confidence_label: "high",
  }),
  reduced: false,
  demands: [
    {
      metric: "invocations",
      covered: true,
      boundary: false,
      demand_amount: "50000",
      demand_unit: "count",
      demand_period: "day",
      matched_metric: "invocations",
      canonical_unit: "count",
      demand_canonical: "50000",
      quota_canonical: "100000",
      headroom: "50000",
      reason: "50,000 of 100,000 per day used.",
    },
  ],
  quota_math: ["invocations: 50,000 ≤ 100,000 per day (headroom 50,000)."],
  z0_safety: [
    "Truly free: no card required and no usage-based billing.",
    "Hard quota — requests beyond the free tier are rejected, not billed.",
  ],
  portability: {
    score: "0.80",
    label: "high",
    lock_in_label: "low",
    deployment_model: "serverless",
    positive_traits: ["Standard runtime", "Export supported"],
    negative_traits: [],
    unknown_traits: [],
    basis: ["Runs a standard JavaScript runtime.", "Config is exportable."],
    exit_plan: ["Redeploy the same code to any Node-compatible host."],
  },
  evidence: [
    {
      title: "Northwind Functions free plan",
      url: "https://example.test/northwind/functions/pricing",
      official: true,
    },
  ],
  explanation: ["Northwind Functions fits the public API at $0."],
};

const objectStorageComponent: AdviserComponent = {
  requirement_index: 1,
  category: "object-file-storage",
  label: "Media storage",
  offer: offer({
    provider_slug: "initech",
    provider_name: "Initech Object Store",
    service_name: "Initech Buckets",
    offer_id: 202,
    zero_cost_class: "Z0_TRUE_FREE",
    confidence_label: "medium",
  }),
  reduced: false,
  demands: [
    {
      metric: "storage",
      covered: true,
      boundary: true,
      demand_amount: "10",
      demand_unit: "GB",
      demand_period: "month",
      matched_metric: "storage",
      canonical_unit: "GB",
      demand_canonical: "10",
      quota_canonical: "10",
      headroom: "0",
      reason: "Exactly at the 10 GB free limit.",
    },
  ],
  quota_math: ["storage: 10 GB = 10 GB per month (headroom 0)."],
  z0_safety: ["Truly free: egress within the free allowance is included."],
  portability: {
    score: "0.60",
    label: "medium",
    lock_in_label: "medium",
    deployment_model: "managed_service",
    positive_traits: ["S3-compatible API"],
    negative_traits: ["Bulk export is manual"],
    unknown_traits: [],
    basis: ["Exposes an S3-compatible API."],
    exit_plan: ["Sync buckets to any S3-compatible store using standard tooling."],
  },
  evidence: [{ title: null, url: null, official: false }],
  explanation: ["Initech Buckets covers media storage at the free boundary."],
};

const recalculatedDbComponent: AdviserComponent = {
  requirement_index: 2,
  category: "relational-databases",
  label: "App database",
  offer: offer({
    provider_slug: "globex",
    provider_name: "Globex Data",
    service_name: "Globex Postgres",
    offer_id: 303,
    zero_cost_class: "Z0_TRUE_FREE",
    confidence_label: "medium",
  }),
  reduced: true,
  demands: [
    {
      metric: "storage",
      covered: true,
      boundary: false,
      demand_amount: "1",
      demand_unit: "GB",
      demand_period: "month",
      matched_metric: "storage",
      canonical_unit: "GB",
      demand_canonical: "1",
      quota_canonical: "5",
      headroom: "4",
      reason: "Under the reduced demand, 1 of 5 GB used.",
    },
  ],
  quota_math: ["storage: 1 GB ≤ 5 GB per month (headroom 4)."],
  z0_safety: ["Truly free at the reduced footprint."],
  portability: {
    score: "0.70",
    label: "high",
    lock_in_label: "low",
    deployment_model: "managed_service",
    positive_traits: ["Standard PostgreSQL"],
    negative_traits: [],
    unknown_traits: [],
    basis: ["Standard PostgreSQL wire protocol."],
    exit_plan: ["pg_dump and restore to any PostgreSQL host."],
  },
  evidence: [
    { title: "Globex Postgres free tier", url: "https://example.test/globex/pg", official: true },
  ],
  explanation: ["Globex Postgres fits once storage is reduced to 1 GB."],
};

const blockingDb: AdviserImpossible = {
  requirement_index: 2,
  category: "relational-databases",
  label: "App database",
  blocking_reason: "No free (Z0) relational database offers 100 GB of storage.",
  closest: offer({
    provider_slug: "globex",
    provider_name: "Globex Data",
    service_name: "Globex Postgres",
    offer_id: 303,
    zero_cost_class: "Z0_TRUE_FREE",
    confidence_label: "medium",
  }),
  reductions: [
    {
      metric: "storage",
      original_amount: "100",
      original_unit: "GB",
      reduced_amount: "5",
      feasible: true,
      reason: "The largest free relational quota is 5 GB.",
    },
  ],
  recalculated: recalculatedDbComponent,
  self_hosting: [
    {
      building_block: offer({
        provider_slug: "postgres",
        provider_name: "PostgreSQL",
        service_name: "PostgreSQL (self-hosted)",
        offer_id: 900,
        zero_cost_class: "Z3_SELF_HOSTED_BUILDING_BLOCK",
        confidence_label: "high",
      }),
      host: offer({
        provider_slug: "northwind",
        provider_name: "Northwind Cloud",
        service_name: "Northwind Micro VM",
        offer_id: 950,
        zero_cost_class: "Z0_TRUE_FREE",
        confidence_label: "medium",
      }),
      note: "Run PostgreSQL yourself on a free micro VM if you truly need 100 GB.",
    },
  ],
  steps: [
    "Blocking: no free relational database offers 100 GB.",
    "Reduction: the largest free quota is 5 GB.",
    "Recalculation: Globex Postgres fits at 5 GB.",
    "Self-hosting: PostgreSQL on a free micro VM.",
  ],
};

/** A fully-satisfiable, provider-agnostic $0 recommendation (two providers). */
export const satisfiableRecommendation: RecommendationResponse = {
  workload_name: "Personal side project",
  priorities: ["truly_free", "portability", "confidence"],
  fully_zero_cost: true,
  zero_cost_proof: [
    "Every component maps to a truly-free (Z0) offer with a hard quota.",
    "No card is required and no path leads to usage-based billing.",
  ],
  architecture: [workersComponent, objectStorageComponent],
  impossible: [],
  not_free_section: {
    label: "These options are NOT $0 and are not part of the recommendation.",
    options: [],
  },
};

/**
 * A mixed workload: one component fits at $0, one is blocking (full resolution
 * chain), and a Z1/Z2 option sits in the clearly separated not-$0 section.
 */
export const mixedRecommendation: RecommendationResponse = {
  workload_name: "Growing SaaS",
  priorities: ["truly_free", "portability"],
  fully_zero_cost: false,
  zero_cost_proof: [
    "The public API fits a truly-free offer, but the database requirement has no $0 option at 100 GB.",
  ],
  architecture: [workersComponent],
  impossible: [blockingDb],
  not_free_section: {
    label: "These options are NOT $0 and are not part of the recommendation.",
    options: [
      {
        requirement_index: 2,
        category: "relational-databases",
        offer: offer({
          provider_slug: "acme",
          provider_name: "Acme Serverless",
          service_name: "Acme SQL",
          offer_id: 404,
          zero_cost_class: "Z1_BILLING_EXPOSURE",
          confidence_label: "high",
        }),
        fits: true,
        note: "Z1_BILLING_EXPOSURE: excluded from the $0 recommendation; shown only for awareness.",
      },
    ],
  },
};

/**
 * Build a `fetch` implementation for the adviser page. Catalogue GETs still route
 * through the provided catalogue fetcher; `POST /api/adviser/recommend` returns
 * the chosen recommendation (satisfiable by default, mixed when the workload name
 * contains "saas") so a single stub can drive both flows.
 */
export function adviserFetch(base: typeof fetch): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = String(input);
    const url = new URL(raw, "http://localhost");
    if (url.pathname.endsWith("/adviser/recommend") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { workload_name?: string | null };
      const wantsMixed = (body.workload_name ?? "").toLowerCase().includes("saas");
      return Response.json(wantsMixed ? mixedRecommendation : satisfiableRecommendation);
    }
    return base(input, init);
  }) as typeof fetch;
}

/** A deterministic-parser interpretation echoed by the assisted endpoint. */
const parsedInterpretation: RecommendationRequest = {
  workload_name: null,
  requirements: [
    {
      category: "serverless-functions",
      label: null,
      demands: [{ metric: "invocations", amount: "50000", unit: "count", period: "day" }],
    },
  ],
};

/**
 * Build a `fetch` for the assisted (natural-language) adviser flow.
 *
 * Mirrors `POST /api/adviser/recommend/assisted` deterministically OFFLINE:
 *
 * - a description carrying a URL signal → `422` (as the API rejects it),
 * - a description containing "gibberish" → an honest `interpreted:false` reply
 *   (nothing guessed) with a `fallback_reason`,
 * - anything else → an `interpreted:true` reply whose `recommendation` is the
 *   SAME deterministic payload the structured endpoint returns.
 *
 * In this slice no LLM provider is enabled, so `llm_used` is always `false` and
 * external processing is never `used` — even when the caller consents. Catalogue
 * GETs and the structured POST still route through {@link adviserFetch}.
 */
export function assistedFetch(base: typeof fetch): typeof fetch {
  const withStructured = adviserFetch(base);
  return (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/adviser/recommend/assisted") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as {
        description: string;
        consent?: { external_processing: boolean } | null;
      };
      const description = body.description ?? "";
      const requested = Boolean(body.consent?.external_processing);
      if (/:\/\/|https?:|www\./i.test(description)) {
        return new Response("url rejected", { status: 422 });
      }
      const interpreted = !description.toLowerCase().includes("gibberish");
      return Response.json({
        interpreted,
        interpretation: interpreted ? parsedInterpretation : null,
        recommendation: interpreted ? satisfiableRecommendation : null,
        routing: {
          llm_used: false,
          llm_provider: null,
          tier: interpreted ? "deterministic_parser" : "deterministic_fallback",
          routing_path: interpreted
            ? ["deterministic_parser"]
            : ["deterministic_parser", "deterministic_fallback"],
          fallback_reason: interpreted ? "deterministic_parser" : "no_provider_enabled",
        },
        consent: {
          external_processing_requested: requested,
          external_processing_used: false,
        },
        notice: interpreted
          ? "Interpreted your description into structured requirements."
          : "Couldn't confidently interpret your description. Nothing was guessed.",
      });
    }
    return withStructured(input, init);
  }) as typeof fetch;
}
