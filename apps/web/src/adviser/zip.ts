/**
 * Dependency-free ZIP assembly for browser-side deployment downloads (F007 S3).
 *
 * The server returns validated, secret-free file *contents*; the browser turns
 * them into a `.zip` entirely client-side. To avoid adding any npm dependency we
 * write a minimal, spec-correct ZIP container using the STORE method (no
 * compression): local file headers, a central directory, and an end-of-central-
 * directory record, with a manually computed CRC-32 per entry.
 *
 * Determinism: a fixed DOS timestamp (1980-01-01 00:00:00) is used for every
 * entry, so identical inputs yield a byte-identical archive.
 */

/** A single file to place in the archive (UTF-8 text content). */
export interface ZipEntry {
  path: string;
  content: string;
}

const CRC32_TABLE: Uint32Array = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

/** Compute the CRC-32 (as an unsigned 32-bit int) of a byte array. */
export function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc = CRC32_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function utf8(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

/**
 * Assemble `entries` into a ZIP archive and return the raw bytes.
 *
 * Every entry is stored (method 0) with the UTF-8 filename flag set (bit 11) and
 * a fixed DOS date/time for reproducibility.
 */
export function buildZip(entries: ZipEntry[]): Uint8Array {
  const encoder = new (class {
    parts: Uint8Array[] = [];
    length = 0;
    push(bytes: Uint8Array) {
      this.parts.push(bytes);
      this.length += bytes.length;
    }
  })();

  const central: Uint8Array[] = [];
  let centralSize = 0;
  const DOS_TIME = 0; // 00:00:00
  const DOS_DATE = 0x0021; // 1980-01-01

  const u16 = (value: number): number[] => [value & 0xff, (value >>> 8) & 0xff];
  const u32 = (value: number): number[] => [
    value & 0xff,
    (value >>> 8) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 24) & 0xff,
  ];

  for (const entry of entries) {
    const nameBytes = utf8(entry.path);
    const dataBytes = utf8(entry.content);
    const crc = crc32(dataBytes);
    const size = dataBytes.length;
    const offset = encoder.length;

    const localHeader = new Uint8Array([
      0x50,
      0x4b,
      0x03,
      0x04, // local file header signature "PK\x03\x04"
      ...u16(20), // version needed to extract (2.0)
      ...u16(0x0800), // general purpose bit flag: bit 11 (UTF-8 filenames)
      ...u16(0), // compression method: 0 = store
      ...u16(DOS_TIME),
      ...u16(DOS_DATE),
      ...u32(crc),
      ...u32(size), // compressed size (== uncompressed for store)
      ...u32(size), // uncompressed size
      ...u16(nameBytes.length),
      ...u16(0), // extra field length
    ]);
    encoder.push(localHeader);
    encoder.push(nameBytes);
    encoder.push(dataBytes);

    const centralHeader = new Uint8Array([
      0x50,
      0x4b,
      0x01,
      0x02, // central directory header signature "PK\x01\x02"
      ...u16(20), // version made by
      ...u16(20), // version needed to extract
      ...u16(0x0800), // general purpose bit flag: UTF-8
      ...u16(0), // compression method: store
      ...u16(DOS_TIME),
      ...u16(DOS_DATE),
      ...u32(crc),
      ...u32(size),
      ...u32(size),
      ...u16(nameBytes.length),
      ...u16(0), // extra field length
      ...u16(0), // file comment length
      ...u16(0), // disk number start
      ...u16(0), // internal file attributes
      ...u32(0), // external file attributes
      ...u32(offset), // relative offset of local header
    ]);
    const centralEntry = new Uint8Array(centralHeader.length + nameBytes.length);
    centralEntry.set(centralHeader, 0);
    centralEntry.set(nameBytes, centralHeader.length);
    central.push(centralEntry);
    centralSize += centralEntry.length;
  }

  const centralOffset = encoder.length;
  for (const c of central) {
    encoder.push(c);
  }

  const eocd = new Uint8Array([
    0x50,
    0x4b,
    0x05,
    0x06, // end of central directory signature "PK\x05\x06"
    ...u16(0), // number of this disk
    ...u16(0), // disk where central directory starts
    ...u16(entries.length), // central directory records on this disk
    ...u16(entries.length), // total central directory records
    ...u32(centralSize),
    ...u32(centralOffset),
    ...u16(0), // comment length
  ]);
  encoder.push(eocd);

  const out = new Uint8Array(encoder.length);
  let cursor = 0;
  for (const part of encoder.parts) {
    out.set(part, cursor);
    cursor += part.length;
  }
  return out;
}

/**
 * Build a ZIP from `entries` and trigger a browser download named `filename`.
 *
 * Guarded for non-browser (test/jsdom) environments: if the DOM download APIs
 * are unavailable it returns the raw bytes without attempting a download so the
 * assembly logic stays unit-testable.
 */
export function downloadZip(entries: ZipEntry[], filename: string): Uint8Array {
  const bytes = buildZip(entries);
  const canDownload =
    typeof document !== "undefined" &&
    typeof URL !== "undefined" &&
    typeof URL.createObjectURL === "function";
  if (!canDownload) {
    return bytes;
  }
  // Copy into a fresh ArrayBuffer-backed view for Blob (avoids SharedArrayBuffer typing).
  const blob = new Blob([bytes.slice()], { type: "application/zip" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
  return bytes;
}
