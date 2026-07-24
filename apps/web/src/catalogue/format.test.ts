import { describe, expect, it } from "vitest";
import {
  confidenceMeaning,
  formatDate,
  formatSignal,
  formatTriState,
  humanizeToken,
  orUnknown,
  z0Meaning,
} from "./format";

describe("format helpers", () => {
  it("maps each Z0 class to a plain-language label and non-colour tone/icon", () => {
    expect(z0Meaning("Z0_TRUE_FREE").label).toMatch(/truly free/i);
    expect(z0Meaning("Z1_BILLING_EXPOSURE").tone).toBe("warn");
    expect(z0Meaning("Z3_SELF_HOSTED_BUILDING_BLOCK").tone).toBe("info");
    // Every meaning carries a text icon so the badge is never colour-only.
    expect(z0Meaning("Z0_TRUE_FREE").icon).not.toBe("");
  });

  it("falls back to an honest Unknown for null or unrecognised Z0 classes", () => {
    expect(z0Meaning(null).label).toBe("Unknown");
    expect(z0Meaning("SOMETHING_NEW").label).toBe("Unknown");
    expect(z0Meaning("UNKNOWN").tone).toBe("unknown");
  });

  it("maps confidence labels case-insensitively with an Unknown fallback", () => {
    expect(confidenceMeaning("HIGH").label).toBe("High");
    expect(confidenceMeaning("low").tone).toBe("warn");
    expect(confidenceMeaning(null).label).toBe("Unknown");
    expect(confidenceMeaning("bogus").label).toBe("Unknown");
  });

  it("formats 0..1 signals as percentages and null as Unknown", () => {
    expect(formatSignal(0.923)).toBe("92%");
    expect(formatSignal(0)).toBe("0%");
    expect(formatSignal(null)).toBe("Unknown");
    expect(formatSignal(undefined)).toBe("Unknown");
  });

  it("formats tri-state booleans honestly", () => {
    expect(formatTriState(true)).toBe("Yes");
    expect(formatTriState(false)).toBe("No");
    expect(formatTriState(null)).toBe("Unknown");
  });

  it("formats ISO dates and degrades to Unknown", () => {
    expect(formatDate("2024-06-01T00:00:00Z")).toBe("2024-06-01");
    expect(formatDate(null)).toBe("Unknown");
  });

  it("humanizes snake/kebab tokens and returns Unknown for empty input", () => {
    expect(humanizeToken("requests_per_day")).toBe("Requests per day");
    expect(humanizeToken("hard-limit")).toBe("Hard limit");
    expect(humanizeToken(null)).toBe("Unknown");
  });

  it("orUnknown returns Unknown for null, undefined, and empty string", () => {
    expect(orUnknown(null)).toBe("Unknown");
    expect(orUnknown(undefined)).toBe("Unknown");
    expect(orUnknown("")).toBe("Unknown");
    expect(orUnknown(0)).toBe("0");
  });
});
