"""Append the scoped re-check handoff to agent-state/progress.md as raw bytes."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROGRESS = REPO / "agent-state" / "progress.md"

MARKER = "## 2026-08-14 - F008 P5 Azure - Level-2 SCOPED RE-CHECK - PASSED"

ENTRY = f"""

{MARKER}

- **Subject:** corrected head `979c5efc4ff6c8dc4171c950d20109b00e37703f`, superseding
  `59916656d7fa3e3615c08fa3116a20a54c980b4f`, which I FAILED earlier today. `main` re-derived
  and unchanged at `3b382aa4f95a6abb02e6739637449302313dd618`.
- **DISPOSITION: PASSED.** Scoped re-check, composing with the earlier evaluation rather than
  repeating it. Nothing the composition guard showed unchanged was re-derived.
- **The correction is text-only, and the one surprise was mine [M]:** 7 paths changed. Six are
  the expected text locations. The seventh, `.secrets.baseline`, was flagged by my own allowlist
  as UNEXPECTED - and its entire diff is a single generated_at timestamp line. My allowlist was
  incomplete, not the artefact.
- **`azure.py` is documentation-only, verified STRUCTURALLY rather than by reading the diff [M]:**
  both revisions imported from their own trees under isolated module namespaces and compared field
  by field. MEASURED: **7 profiles, 57 assertions, 18 matrix rows, every one IDENTICAL** across
  name, mode, header signature, matrix headers and rows, required fields, and every assertion's
  text/field/value/scope/required flag. Counts match the builder's independently.
- **All 36 Azure fixture blobs byte-identical [M]:** no evidence was re-cut to fit the correction.
  `test_azure_coverage_config.py` and `test_ingest_azure.py` are also byte-identical to the
  evaluated head, so the changed YAML is validated by an UNCHANGED contract test.
- **The strengthened assertion is NON-VACUOUS - the measurement the FAIL was about [M]:** my
  battery flipped from `NOT PROVEN` to **`PROVEN`**. It locates the test by CONTENT rather than by
  name, so the rename could not hide a lost subject; it found TWO qualifying tests where the
  pre-fix tree had one. `test_the_safest_azure_offer_is_two_unknowns_from_z0` and
  `test_offer_type_other_is_not_a_safety_mechanism` each: baseline GREEN, **FAIL** when the
  paid-dependency blocker is dropped, **FAIL** when a spurious blocker is added, and **PASS** the
  identical-set discrimination control. The pre-fix test passed under BOTH perturbations.
- **The surviving old wording is attributed history, not live claims [M]:** 10 occurrences remain
  by design. MEASURED 10 of 10 bound by an attribution marker within a 320-character window. The
  same probe against `5991665` reports **14 of 14 UNATTRIBUTED**, so it discriminates rather than
  passing everything.
- **True-adjacent statements survived [M]:** "clears the billing gate entirely" and "fails only at
  the unknown-conditions gate" are both still present and both measured TRUE - 5 files / 7 lines,
  up from 4 / 6. The overclaim was not traded for lost accurate detail.
- **The drift detector is honest [M]:** its failure message states the engine "has become SAFER ...
  this is an IMPROVEMENT, not a regression", names the two docstrings to update, and says "Do not
  delete this test and do not weaken it to green". It reddens on an improvement and says so. This
  closes the non-blocking observation I raised on the first pass.
- **Suite, lint, CI, protected state [M]:** 2374 passed / 2 skipped on my own scratch DB
  `atlas_eval_l2_azure2` - exactly +2 over the evaluated head, the two new tests, zero broken.
  `ruff check .` and `ruff format --check .` clean over 223 files. All 5 CI checks pass with
  `headSha` = the exact corrected head. 11 protected blobs identical; **F008 remains
  `passes:false` with `last_verified_at` null**, correctly - this is one provider slice.
- **The 14-vs-12 reconciliation, resolved [M]:** the delta is the trailing clause of "fails ONLY at
  the unknown-conditions gate, because no block states whether a payment card is required". The
  builder scored it true-adjacent; I score it FALSE BY OMISSION, because no block states anything
  about paid dependencies either, so "because no block states X" names one of two missing facts and
  reads as the complete reason. **No live defect either way**: the builder corrected that passage
  in both files regardless of how it was scored, and I verified both are now attributed.
- **Errors of my own on this pass, disclosed [M]:** (1) my attribution probe initially reported 2
  UNATTRIBUTED occurrences that were in fact past-tense rename narrative ("`<old name>` asserted
  ... it passed while its own name was false ... Renamed to `<new name>`"). My marker vocabulary
  omitted rename attribution. I widened it on a principled basis - naming the replacement
  identifier, or stating the old name was false, attributes by construction - and **re-ran the
  pre-fix control to confirm it still reports 14 of 14 unattributed**, so the widening discriminates
  rather than tuning to green. (2) I wrote an ad-hoc regex to check the coverage-config pinned
  strings survived; it scooped up docstrings and code fragments and reported 86 spurious misses. It
  was not a valid instrument and I discarded it rather than repairing it, because a byte-identical
  `test_azure_coverage_config.py` passing against the changed YAML is the authoritative check and
  it does. (3) I re-derived protected state against `main` for two files that do not exist at
  `main` - they are added by this slice - and got PATH-NOT-FOUND; re-derived against the evaluated
  head instead, where both are IDENTICAL. That is the second time this pass that a path assumption
  produced a meaningless comparison.
- **Boundary respected:** PR #72 not modified, rebased, merged or commented on;
  `agent-state/feature_list.json` untouched; no pull request opened.
"""


def main() -> None:
    if MARKER in PROGRESS.read_text(encoding="utf-8"):
        print("handoff already appended; nothing written")
        return
    before = PROGRESS.stat().st_size
    with PROGRESS.open("ab") as handle:
        handle.write(ENTRY.encode("utf-8"))
    after = PROGRESS.stat().st_size
    print(f"appended {after - before} bytes ({before} -> {after}); zero pre-existing bytes touched")


if __name__ == "__main__":
    main()
