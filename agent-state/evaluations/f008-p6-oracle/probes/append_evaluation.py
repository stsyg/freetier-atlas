"""Append one entry to agent-state/evaluation.json on RAW BYTES.

``agent-state`` is shared and concurrently written, and a prior evaluator used
``Path.write_text`` here: that rewrote all 495 line endings to CRLF and corrupted
a pre-existing character. This appender therefore never round-trips the whole
document through ``json.load``/``json.dump``. It splices the new entry in as
bytes, preserves the file's existing newline convention, and REFUSES TO WRITE
unless every pre-existing byte is provably unchanged.

Usage::

    python append_evaluation.py <evaluation.json> <entry.json>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

TAIL = b"\n]\n"


def main() -> int:
    target = Path(sys.argv[1])
    entry_path = Path(sys.argv[2])

    original = target.read_bytes()
    if not original.endswith(TAIL):
        print(f"REFUSED: {target} does not end with the expected {TAIL!r}")
        return 1
    if b"\r\n" in original:
        print("REFUSED: file already contains CRLF; this appender preserves LF only.")
        return 1

    prefix = original[: -len(TAIL)]
    prefix_digest = hashlib.sha256(prefix).hexdigest()

    # Validate the entry parses, then re-serialise it with the file's own
    # 2-space indentation and indent it one level to sit inside the array.
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    rendered = json.dumps(entry, indent=2, ensure_ascii=False)
    indented = "\n".join("  " + line if line else "" for line in rendered.split("\n"))

    addition = b",\n" + indented.encode("utf-8") + TAIL
    updated = prefix + addition

    # REFUSE-TO-WRITE GUARD, evaluated before touching the file.
    if not updated.startswith(prefix):
        print("REFUSED: splice would alter pre-existing bytes.")
        return 1
    if hashlib.sha256(updated[: len(prefix)]).hexdigest() != prefix_digest:
        print("REFUSED: prefix digest changed under the splice.")
        return 1
    if b"\r\n" in updated:
        print("REFUSED: splice introduced CRLF.")
        return 1

    with target.open("wb") as handle:
        handle.write(updated)

    # Re-derive from disk rather than trusting the in-memory value.
    reread = target.read_bytes()
    if reread[: len(prefix)] != prefix:
        print("CORRUPTION: pre-existing bytes changed on disk. Restoring.")
        target.write_bytes(original)
        return 1
    if hashlib.sha256(reread[: len(prefix)]).hexdigest() != prefix_digest:
        print("CORRUPTION: prefix digest mismatch on disk. Restoring.")
        target.write_bytes(original)
        return 1

    parsed = json.loads(reread.decode("utf-8"))
    crlf_count = reread.count(b"\r\n")
    print(f"OK appended. bytes {len(original)} -> {len(reread)}; entries -> {len(parsed)}")
    print(f"prefix sha256 unchanged: {prefix_digest[:16]}")
    print(f"CRLF count in result: {crlf_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
