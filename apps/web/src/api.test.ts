import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchCategoryStates,
  fetchOffer,
  fetchOfferEvidence,
  fetchOfferHistory,
  fetchProvider,
  fetchProviderOffers,
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
