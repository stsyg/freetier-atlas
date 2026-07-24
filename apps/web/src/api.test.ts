import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchCategoryMatrix,
  fetchCategoryStates,
  fetchCompare,
  fetchOffer,
  fetchOfferEvidence,
  fetchOfferHistory,
  fetchProvider,
  fetchProviderOffers,
  fetchProviders,
  fetchSearch,
} from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function stubFetch(impl: typeof fetch) {
  const spy = vi.fn(impl);
  vi.stubGlobal("fetch", spy);
  return spy;
}

describe("catalogue API client", () => {
  it("builds fixed same-origin /api paths and encodes the slug", async () => {
    const spy = stubFetch(async () => Response.json({ ok: true }));
    await fetchProvider("cloudflare");
    await fetchCategoryStates("cloudflare");
    await fetchProviderOffers("cloudflare");
    await fetchOffer(7);
    await fetchOfferEvidence(7);
    await fetchOfferHistory(7);

    const urls = spy.mock.calls.map((c) => c[0]);
    expect(urls).toContain("/api/catalogue/providers/cloudflare");
    expect(urls).toContain("/api/catalogue/providers/cloudflare/category-states");
    expect(urls).toContain("/api/catalogue/providers/cloudflare/offers");
    expect(urls).toContain("/api/catalogue/offers/7");
    expect(urls).toContain("/api/catalogue/offers/7/evidence");
    expect(urls).toContain("/api/catalogue/offers/7/history");
  });

  it("sends a JSON Accept header and never credentials", async () => {
    const spy = stubFetch(async () => Response.json({}));
    await fetchProvider("cloudflare");
    const init = spy.mock.calls[0][1];
    expect(init).toMatchObject({ headers: { Accept: "application/json" } });
    expect(init).not.toHaveProperty("credentials");
  });

  it("surfaces a friendly message when the API is unreachable", async () => {
    stubFetch(async () => {
      throw new TypeError("network down");
    });
    await expect(fetchProvider("cloudflare")).rejects.toThrow(/unable to reach the api/i);
  });

  it("maps a 404 to a not-found message", async () => {
    stubFetch(async () => new Response("nope", { status: 404 }));
    await expect(fetchProvider("nope")).rejects.toThrow(/not found in the published catalogue/i);
  });

  it("reports the status code for other non-2xx responses", async () => {
    stubFetch(async () => new Response("boom", { status: 500 }));
    await expect(fetchProvider("cloudflare")).rejects.toThrow(/HTTP 500/);
  });

  it("rejects when the body is not valid JSON", async () => {
    stubFetch(async () => new Response("<html>", { status: 200 }));
    await expect(fetchProvider("cloudflare")).rejects.toThrow(/not valid JSON/i);
  });
});

describe("catalogue-wide search / matrix / compare client (F006)", () => {
  it("lists providers from the fixed collection path", async () => {
    const spy = stubFetch(async () => Response.json([]));
    await fetchProviders();
    expect(spy.mock.calls[0][0]).toBe("/api/catalogue/providers");
  });

  it("encodes only present filters as query params onto the fixed search path", async () => {
    const spy = stubFetch(async () => Response.json({}));
    await fetchSearch({
      q: "workers",
      provider: "cloudflare",
      category: "serverless-functions",
      zero_cost_class: "Z0_TRUE_FREE",
      offer_type: "always_free",
      commercial_use: true,
      status: "active",
      page: 2,
    });
    const url = new URL(String(spy.mock.calls[0][0]), "http://localhost");
    expect(url.pathname).toBe("/api/catalogue/search");
    expect(url.searchParams.get("q")).toBe("workers");
    expect(url.searchParams.get("provider")).toBe("cloudflare");
    expect(url.searchParams.get("category")).toBe("serverless-functions");
    expect(url.searchParams.get("zero_cost_class")).toBe("Z0_TRUE_FREE");
    expect(url.searchParams.get("offer_type")).toBe("always_free");
    expect(url.searchParams.get("commercial_use")).toBe("true");
    expect(url.searchParams.get("status")).toBe("active");
    expect(url.searchParams.get("page")).toBe("2");
  });

  it("omits empty, null, and default-page params (no stray query string)", async () => {
    const spy = stubFetch(async () => Response.json({}));
    await fetchSearch({ q: "", provider: null, page: 1 });
    expect(spy.mock.calls[0][0]).toBe("/api/catalogue/search");
  });

  it("still encodes commercial_use=false (a meaningful filter, not absent)", async () => {
    const spy = stubFetch(async () => Response.json({}));
    await fetchSearch({ commercial_use: false });
    const url = new URL(String(spy.mock.calls[0][0]), "http://localhost");
    expect(url.searchParams.get("commercial_use")).toBe("false");
  });

  it("fetches the category matrix from the fixed path", async () => {
    const spy = stubFetch(async () => Response.json({}));
    await fetchCategoryMatrix();
    expect(spy.mock.calls[0][0]).toBe("/api/catalogue/categories");
  });

  it("joins offer ids into the fixed compare path (internal ids only)", async () => {
    const spy = stubFetch(async () => Response.json({}));
    await fetchCompare([1, 3, 4]);
    const url = new URL(String(spy.mock.calls[0][0]), "http://localhost");
    expect(url.pathname).toBe("/api/catalogue/compare");
    expect(url.searchParams.get("offers")).toBe("1,3,4");
  });
});
