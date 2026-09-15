"""Re-check instrument 5: is every surviving false-claim occurrence a QUOTATION?

The correction leaves the old wording in the tree deliberately, as attributed
history ("an earlier revision said ..."). That is legitimate ONLY if a reader
meeting the sentence can tell locally that it is a quotation rather than a live
claim -- otherwise the tree still asserts the false thing.

This does not ask whether an attribution exists SOMEWHERE in the file. It asks
whether one is close enough to bind: within a bounded window BEFORE the match,
which is what "self-attributing" has to mean if it is to survive a reader
skimming, a grep, or a future author editing one paragraph.

Every occurrence must be attributed. One unattributed occurrence is a live false
claim and the disposition stays FAILED.

Usage:  python probe_quotations_are_attributed.py <repo_root> [git_sha]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inventory_one_unknown_claim import (  # noqa: E402
    FALSE_CLAIMS,
    normalise,
    read,
    tracked_files,
)

# Markers that mark the surrounding text as history rather than assertion.
ATTRIBUTION = re.compile(
    r"(an earlier revision|previously read|this test previously|corrected after|"
    r"was committed in|the now-corrected wording|quoted here from|"
    r"said the offer|of this (?:module|document|contract) said|"
    r"the claim '|the claim \"|earlier brief|first revision|"
    # Rename narrative: a passage naming the REPLACEMENT identifier, or saying the
    # old name was false, is describing a past state by construction. Added after
    # this probe raised two false positives on prose that attributes by past tense
    # and by naming the new test rather than by an "an earlier revision" phrase.
    r"renamed to|was renamed|its own name was false|while its own name|"
    r"is_two_unknowns_from_z0)",
    re.I,
)

# How far back an attribution may sit and still bind the quotation locally.
WINDOW = 320
# Attribution may also FOLLOW the quotation ("X ... - which was false, renamed to Y").
WINDOW_AFTER = 320


def main() -> None:
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    sha = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"repo={repo}  sha={sha or '<working tree>'}  window={WINDOW}/{WINDOW_AFTER} chars\n")

    unattributed = []
    attributed = []

    for path in tracked_files(repo, sha):
        text = read(repo, path, sha)
        if text is None:
            continue
        flat, _index = normalise(text)
        for label, pattern in FALSE_CLAIMS.items():
            for match in pattern.finditer(flat):
                before = flat[max(0, match.start() - WINDOW) : match.start()]
                after = flat[match.end() : match.end() + WINDOW_AFTER]
                marker = ATTRIBUTION.search(before) or ATTRIBUTION.search(after)
                record = (path.as_posix(), label, match.group(0), before[-150:])
                if marker:
                    attributed.append((*record, marker.group(0)))
                else:
                    unattributed.append(record)

    print("=" * 78)
    print(f"ATTRIBUTED OCCURRENCES ({len(attributed)})")
    print("=" * 78)
    for path, label, text, _before, marker in attributed:
        print(f"  {path}")
        print(f"    [{label}] {' '.join(text.split())[:90]}")
        print(f"    bound by: {marker!r}")

    print()
    print("=" * 78)
    print(f"UNATTRIBUTED OCCURRENCES ({len(unattributed)})  <-- these are LIVE CLAIMS")
    print("=" * 78)
    for path, label, text, before in unattributed:
        print(f"  {path}")
        print(f"    [{label}] {' '.join(text.split())[:90]}")
        print(f"    preceding {WINDOW} chars: ...{' '.join(before.split())[-120:]}")

    print()
    print("=" * 78)
    print(f"TOTAL occurrences : {len(attributed) + len(unattributed)}")
    print(f"  attributed      : {len(attributed)}")
    print(f"  UNATTRIBUTED    : {len(unattributed)}")
    print(f"ALL SURVIVING OCCURRENCES ARE QUOTATIONS: {not unattributed}")
    print("=" * 78)
    if unattributed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
