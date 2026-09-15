"""Q2 trace + Q6 naive-stripper proof for PR #69."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

WT = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "source_scan", WT / "tests" / "support" / "source_scan.py"
)
ss = importlib.util.module_from_spec(spec)
sys.modules["source_scan"] = ss
spec.loader.exec_module(ss)

VAL = "check_secrets_baseline"

print("=" * 70)
print("Q2 TRACE — ownership assigned to an inert `with: run:` action input")
print("=" * 70)
case = (
    "jobs:\n"
    "  lint:\n"
    "    steps:\n"
    "      - name: Example only\n"
    "        uses: third/party@v1\n"
    "        with:\n"
    "          run: |\n"
    f"            python scripts/{VAL}.py\n"
)
for ln in ss.scan(".github/workflows/ci.yml", case):
    mark = "  <== NEEDLE" if VAL in ln.executable else ""
    print(f"  L{ln.number} ctx={str(ln.context):<9} key={str(ln.key):<8} {ln.raw!r}{mark}")
print(
    "  verdict:",
    ss.invocation_problems(".github/workflows/ci.yml", case, VAL, None, frozenset({"run"}))
    or "ACCEPTED as a live wiring",
)

print()
print("=" * 70)
print("Q6 — the '#' that is NOT a comment, and what a naive stripper does")
print("=" * 70)
for rel, expansion in (
    (".github/workflows/ci.yml", "${#files[@]}"),
    ("scripts/check.sh", "${#FAILURES[@]}"),
):
    text = (WT / rel).read_text(encoding="utf-8")
    scanned = ss.executable_text(ss.scan(rel, text))
    naive = "\n".join(line.split("#")[0] for line in text.splitlines())
    print(f"\n  {rel}   probe={expansion}")
    print(f"    present in raw source            : {expansion in text}")
    print(f"    survives THIS scanner            : {expansion in scanned}")
    print(f"    survives naive line.split('#')[0]: {expansion in naive}")
    hit = next((ln.strip() for ln in text.splitlines() if expansion in ln), "")
    print(f"    the real line                    : {hit[:88]}")
    print(f"    naive stripper turns it into     : {hit.split('#')[0][:88]!r}")
    # Would the guard still find the invocation under a naive stripper?
    naive_has_val = VAL in naive
    print(f"    (naive stripper still sees VAL   : {naive_has_val})")

print()
print("=" * 70)
print("Q4 — fail-closed on an unknown suffix")
print("=" * 70)
for probe in ("scripts/check.rb", "scripts/check", "scripts/check.yml.bak", "Makefile"):
    try:
        ss.scan(probe, f"system '{VAL}'\n")
        print(f"  {probe:<24} -> RETURNED (no raise)  <== fallback risk")
    except ss.UnknownLanguage:
        print(f"  {probe:<24} -> UnknownLanguage raised")
    except Exception as exc:  # noqa: BLE001
        print(f"  {probe:<24} -> {type(exc).__name__}: {exc}")
