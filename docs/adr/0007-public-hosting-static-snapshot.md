# ADR 0007: Public hosting serves a static snapshot only

Status: Accepted — 2026-09-15

This record **extends ADR 0003** and **supersedes ADR 0003 only on the narrow
question of what the public deployment serves**. ADR 0003's host choice —
Cloudflare Pages as primary with a `stsyg.github.io/freetier-atlas/` mirror —
still stands and is not revisited here. What changes is the *shape* of the
artefact those hosts serve.

## Context — the measured tension

This is not a speculative concern; it was measured against
`origin/main @ 210e6530`.

- The web client requires a live API. `apps/web/src/api.ts` defines
  `export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";`
  (line 17), and every catalogue call issues a runtime `fetch()` against that
  base — GET for reads and POST for the adviser.
- The public HTTP surface is **27 route decorators**, counted on current main:
  `read_api/router.py` 10 GET, `admin/router.py` 10 (6 GET + 4 POST),
  `adviser/router.py` 4 POST, and `main.py` 3 GET (health/meta).
- There is **no static-export path in the tree today**: 0 source paths match
  `static_export|prerender|snapshot_json|build_static`. (The single repository
  match is prose in `agent-state/progress.md` noting the SPA has no prerender,
  not an implementation.)
- A static host cannot serve a POST, hold an OAuth session, or run PostgreSQL.

The consequence is factual, not rhetorical: several MVP acceptance criteria are
**not jointly satisfiable** by static hosting as the application is currently
built. A purely static public deployment cannot serve the four adviser POSTs,
the four admin POSTs, or a GitHub-authenticated admin session, because those
depend on server-side execution and state that a static host does not provide.

## Decision — Option A: static snapshot only

The public deployment serves a **build-time static snapshot** and nothing that
requires a running server:

1. **The catalogue** as a build-time JSON export of the 10 read-only catalogue
   GET routes.
2. **Client-side search, filter, sort, and compare** computed in the browser
   over that exported JSON.
3. **The deterministic adviser**, executed entirely client-side.
4. **An RSS feed** produced as a build-time artefact.

**Admin and the LLM-assisted adviser are local-only, via Docker Compose, and are
not published.**

### Consequences spelled out

- **MVP line 19 ("GitHub-authenticated admin works") is satisfied locally.**
  Admin runs under Compose against the real API and PostgreSQL; the acceptance
  criterion is met by admin working locally, not on the public host.
- **The public deployment loses public admin.** The four admin POST routes and
  the six admin GET routes are simply not present on the public surface.
- **The public deployment loses MVP line 13** ("natural language converts to
  editable structured requirements"). That capability needs an LLM-backed route,
  which a static host cannot serve. It remains available locally.
- **The deterministic adviser is preserved publicly** because it needs no server:
  it is pure computation over the exported catalogue.

## Why Option A rather than Option B (the full app on a free host)

Option B — deploy the whole dynamic application (API, database, OAuth) onto some
free-tier host — was rejected on the argument that matters most to *this*
product:

To demonstrate that a product can find genuinely zero-cost architectures, Option
B would have us adopt the **least-evidenced free tier in the entire design** —
our own hosting — as a load-bearing dependency. If such a host can silently begin
billing, then our own deployment becomes exactly the unsupported "free" claim the
product exists to prevent: published under our name, on the very page asserting
`$0`. That is a self-inflicted instance of the failure mode the catalogue is
built to catch.

Static hosting has **no billing meter at all**: there is no compute to meter, no
database to bill, no request-priced surface. Option A also collapses the public
attack surface to a set of files — no POST handlers, no session, no database
reachable from the internet.

## Option C is the natural upgrade path and is NOT foreclosed

A single narrow serverless function providing *only* the LLM-assisted advice
step remains an available later upgrade. Choosing Option A now does not preclude
Option C; it defers it. When adopted, Option C would restore MVP line 13 to the
public surface through one auditable, tightly-scoped route rather than by
publishing the whole application.

## Principal risk — snapshot currency

**This risk is stated prominently and is not to be softened.**

A snapshot **freezes evidence currency at build time.** The exported JSON becomes
a **tenth catalogue surface** alongside the nine live ones. The defect class
fixed in PR #95 / PR #99 — nine catalogue surfaces independently repeating an
expired free claim — **applies to the export directly and by construction.**

Therefore the export is bound by two hard requirements:

- It **MUST** carry an explicit `as_of` build timestamp.
- It **MUST** be structurally incapable of presenting an expired or
  unknown-currency claim as current. Deriving currency at render time is not
  enough; the exported data itself must encode staleness so a stale claim cannot
  be shown as fresh.

This interacts directly with the `schedule_ref` staleness-window work, which
**merged as PR #128** (`main` at `71c351a`, 2026-09-15) — it resolves each
source's window from its declared cron cadence and removes the silent 7-day
fallback. The staleness window that change defines is the same window the export
must respect, and the two must not diverge. That constraint is now *more*
binding, not less: the window is real and landed rather than pending.

Two details the later implementer must not lose:

- The export must respect **per-source** windows, **not a single global figure.**
  PR #128 derives a distinct window per source from its cron cadence.
- Under #128's rule the set of entries whose derived window is *looser* than the
  old 7-day default is pinned by a guard to exactly `["full_reconciliation"]`.
  An export that assumed a uniform or uniformly-tighter window would misrepresent
  currency for any source referencing such an entry.

How exactly the export encodes `as_of` and per-source staleness is an **open
question for a later implementation slice**, not something this decision settles.

## Local operation is unaffected

The full dynamic application — API, admin, LLM-assisted adviser, PostgreSQL —
remains available locally via Docker Compose, satisfying MVP line 49 ("no cloud
provider is mandatory for local operation"). **Nothing is lost from the product;
only the public surface narrows.**

## References

- ADR 0003: Cloudflare Pages primary, GitHub Pages mirror (extended and, on the
  question of served content, superseded by this record)
- `docs/HOSTING_Z0.md`
- `docs/MVP_ACCEPTANCE.md` (lines 13, 19, 49)
- PR #95, PR #99 — the multi-surface expired-claim defect class
- PR #128 — `schedule_ref` staleness-window resolution (merged; `main` `71c351a`, 2026-09-15)
