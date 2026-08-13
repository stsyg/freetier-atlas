import { describe, expect, it } from "vitest";
import { buildZip, crc32, downloadZip, type ZipEntry } from "./zip";

const decoder = new TextDecoder();

function u16(bytes: Uint8Array, offset: number): number {
  return bytes[offset] | (bytes[offset + 1] << 8);
}

function u32(bytes: Uint8Array, offset: number): number {
  return (
    (bytes[offset] |
      (bytes[offset + 1] << 8) |
      (bytes[offset + 2] << 16) |
      (bytes[offset + 3] << 24)) >>>
    0
  );
}

/** Minimal STORE-method reader: walk local file headers and extract entries. */
function readStoredZip(bytes: Uint8Array): { path: string; content: string; crc: number }[] {
  const entries: { path: string; content: string; crc: number }[] = [];
  let offset = 0;
  while (offset + 4 <= bytes.length && u32(bytes, offset) === 0x04034b50) {
    const method = u16(bytes, offset + 8);
    const crc = u32(bytes, offset + 14);
    const compSize = u32(bytes, offset + 18);
    const nameLen = u16(bytes, offset + 26);
    const extraLen = u16(bytes, offset + 28);
    const nameStart = offset + 30;
    const dataStart = nameStart + nameLen + extraLen;
    const path = decoder.decode(bytes.slice(nameStart, nameStart + nameLen));
    const content = decoder.decode(bytes.slice(dataStart, dataStart + compSize));
    expect(method).toBe(0); // STORE
    entries.push({ path, content, crc });
    offset = dataStart + compSize;
  }
  return entries;
}

const SAMPLE: ZipEntry[] = [
  { path: "docker-compose.yml", content: "services:\n  app:\n    image: nginx:1.27-alpine\n" },
  {
    path: ".env.example",
    content: "APP_PORT=8080\nPOSTGRES_PASSWORD=${POSTGRES_PASSWORD:-REPLACE_ME}\n",
  },
  { path: "README.md", content: "# Scaffold\n" },
];

describe("browser-side ZIP assembly (F007 slice 3)", () => {
  it("produces a valid ZIP container with the PK signatures", () => {
    const bytes = buildZip(SAMPLE);
    expect(bytes[0]).toBe(0x50); // 'P'
    expect(bytes[1]).toBe(0x4b); // 'K'
    expect(bytes[2]).toBe(0x03);
    expect(bytes[3]).toBe(0x04);
    // End-of-central-directory signature present near the tail.
    const eocdIndex = bytes.length - 22;
    expect(u32(bytes, eocdIndex)).toBe(0x06054b50);
    expect(u16(bytes, eocdIndex + 10)).toBe(SAMPLE.length);
  });

  it("round-trips the exact files and contents", () => {
    const bytes = buildZip(SAMPLE);
    const parsed = readStoredZip(bytes);
    expect(parsed.map((e) => e.path)).toEqual(SAMPLE.map((e) => e.path));
    for (let i = 0; i < SAMPLE.length; i += 1) {
      expect(parsed[i].content).toBe(SAMPLE[i].content);
    }
  });

  it("stores a correct CRC-32 for each entry", () => {
    const bytes = buildZip(SAMPLE);
    const parsed = readStoredZip(bytes);
    for (let i = 0; i < SAMPLE.length; i += 1) {
      const expected = crc32(new TextEncoder().encode(SAMPLE[i].content));
      expect(parsed[i].crc).toBe(expected);
    }
  });

  it("is deterministic: identical input yields byte-identical output", () => {
    const a = buildZip(SAMPLE);
    const b = buildZip(SAMPLE);
    expect(Array.from(a)).toEqual(Array.from(b));
  });

  it("only ever contains placeholder secrets (no real secret material)", () => {
    const bytes = buildZip(SAMPLE);
    const text = decoder.decode(bytes);
    // The env content carries a placeholder, never a real secret value.
    expect(text).toContain("REPLACE_ME");
    expect(text).not.toMatch(/AKIA[0-9A-Z]{16}/);
  });

  it("downloadZip returns the bytes without a DOM in the test environment", () => {
    // jsdom lacks URL.createObjectURL; the guard should return bytes, not throw.
    const bytes = downloadZip(SAMPLE, "bundle.zip");
    expect(bytes.length).toBeGreaterThan(0);
    expect(readStoredZip(bytes).map((e) => e.path)).toEqual(SAMPLE.map((e) => e.path));
  });
});
