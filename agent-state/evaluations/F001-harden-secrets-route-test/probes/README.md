# Level-2 evaluation probes — F001 `harden-secrets-route-test` (PR #69)

Evaluator-owned. **Not part of the test suite** and not run by CI: these are the
instruments a fresh-context Level-2 evaluator used to measure PR #69 across its
five heads. They are committed because several of them outlived the evaluation
and are the machinery behind rules this project now applies generally.

Nothing here imports from `tests/`, and nothing under `tests/` imports from here.
Each probe loads `tests/support/source_scan.py` **by path** and **re-declares the
route rules locally**, on purpose: an instrument that shares the subject's
declarations cannot detect a change in them.

## Running them

```
python <probe>.py <path-to-a-worktree>
```

Every probe takes the worktree to measure, so one checkout can be pointed at
another. They need only `pyyaml`, which the project already pins.

| probe | what it measures |
|---|---|
| `probe_loader.py` | `load_module()` — registers in `sys.modules` **before** `exec_module`. Run it directly for a selftest that first reproduces the failure, then shows the fix. |
| `eval_harness.py` | Q1/Q3/Q5 — the caught modes, the false-positive controls, and the twelve-case matrix. |
| `spelling_battery.py` | 16 legitimate spellings that must be accepted, 8 decoys that must be rejected. 12 of the 16 appear in none of the four route files. |
| `walk_blindspots.py` | Shapes a schema walk visits nothing for, plus fail-closed inputs, with diagnostic grading built in. |
| `parser_probes.py` | Anchors, aliases, merge keys, duplicate keys, unparseable input. |
| `new_shapes.py` | Cyclic aliases, alias fan-out, multi-document, tags, YAML 1.1 boolean coercion, deep nesting. |
| `type_awareness.py` | Whether every schema segment records its type, or only the leaf. |
| `arity_probes.py` | Terminal vs intermediate sequence typing, including list-of-lists. |
| `mutate_controls.py` | **Mutation-tests the suite's own controls** — removes each guarantee and requires the guarding test to go red. |
| `trace_q2_q4_q6.py`, `probe_lead.py` | Narrow traces kept for provenance of specific findings. |
| `append_evaluation.py` | Appends this evaluation's `evaluation.json` entry without hand-editing the array. |

## The two that are worth reusing

**`mutate_controls.py`** is the answer to "is this control non-vacuous?". It
baselines each test green, removes the guarantee it claims to guard, requires
red, then restores and proves restoration by `git hash-object` — never by
`--numstat`, which `.gitattributes` eol filters can mask.

It also encodes a trap found the hard way: **a mutation that does not apply looks
exactly like a control that does not fire.** Two first-attempt mutations here were
duds — a `raise` placed after a `return`, and an anchor string that did not exist
verbatim — and both reported false vacuity. So the harness asserts the anchor
matched before replacing, and treats "anchor not found" as an *instrument*
failure printed differently from a finding.

**`probe_loader.py`** exists because loading a module by path has one non-obvious
ordering requirement whose error message names neither the cause nor the fix
(`AttributeError: 'NoneType' object has no attribute '__dict__'`, raised by
`dataclasses` resolving annotations through `sys.modules`). It bit three separate
sessions. Its selftest is deliberately built around
`from __future__ import annotations`, because without that import the naive
ordering happens to work and the selftest proves nothing.
