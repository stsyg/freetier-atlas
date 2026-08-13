# FreeTier Atlas — Web frontend

A [Vite](https://vite.dev) + [React](https://react.dev) + TypeScript single-page
application. It is a **provider-agnostic catalogue browser** (F006 slice 2):
a read-only, evidence-backed view of published free-tier offers across every
provider, consumed entirely from the catalogue read API over the same-origin
`/api` proxy. The adviser arrives in a later increment.

The app is a small hash-routed SPA (no router dependency) with four views:

- **Browse** (`#/`) — a keyword search plus composable filters (provider,
  category, zero-cost class, offer type, commercial use, status) driving
  `GET /api/catalogue/search`, with a paged results list.
- **Categories** (`#/categories`) — the fourteen-category × provider coverage
  matrix from `GET /api/catalogue/categories`.
- **Compare** (`#/compare`) — a normalized side-by-side of the two or three
  offers picked in Browse, from `GET /api/catalogue/compare?offers=…`.
- **Cloudflare** (`#/provider/cloudflare`) — the retained single-provider
  evidence page (F005 slice 4): category / service states with zero-cost (Z0)
  badges, each offer's Z0 rating and plain-language reasons, the official
  evidence backing each claim, version history, completeness/freshness, and
  quota rows.

A confidence **label** is always the primary signal; any numeric score lives
only inside a closed advanced disclosure. Values the API cannot verify are shown
honestly as "Unknown" — never guessed — and the UI never re-derives a Z0 or
confidence rating, it displays exactly what the API returns.

## Layout

| Path                               | Purpose                                                                |
| ---------------------------------- | ---------------------------------------------------------------------- |
| `src/main.tsx`                     | React entry point.                                                     |
| `src/App.tsx`                      | Hash router + landing; Browse / Categories / Compare / provider views. |
| `src/api.ts`                       | Read-only catalogue client (typed `GET` fetchers over `/api`).         |
| `src/catalogue/SearchControls.tsx` | Search box + composable filter form (typed query, no fetch).           |
| `src/catalogue/ResultsList.tsx`    | Paged results list with compare selection + pagination.                |
| `src/catalogue/CategoryMatrix.tsx` | Accessible 14-category × provider coverage table.                      |
| `src/catalogue/CompareView.tsx`    | Accessible side-by-side comparison table (normalized quotas).          |
| `src/catalogue/vocab.ts`           | Closed filter vocabularies + coverage-state labels.                    |
| `src/catalogue/`                   | Provider-page components + `format.ts` plain-language helpers.         |
| `src/App.test.tsx`                 | Routing + multi-provider + a11y integration tests (mocked `fetch`).    |
| `src/api.test.ts`                  | API-client tests (fixed paths, param encoding, error handling).        |
| `src/catalogue/*.test.*`           | Component + formatter unit tests (a11y, honest "Unknown").             |
| `nginx.conf`                       | Runtime server: SPA fallback, `/healthz`, `/api/` proxy.               |
| `Dockerfile`                       | Multi-stage build (Node build → nginx runtime).                        |

## Accessibility

Accessibility is part of "done" and is asserted by the tests:

- semantic landmarks (`banner`/`navigation`/`main`/`contentinfo`) and exactly
  one `<h1>` per route, with headings in order;
- the primary `<nav>` marks the active view with `aria-current="page"`;
- keyboard-operable controls: native `<form>`, labelled `<select>`s, checkboxes,
  and `<details>` disclosures;
- accessible data tables — the category matrix and compare view both carry a
  `<caption>` and `scope`d row/column headers;
- external links use `rel="noopener noreferrer"` with `target="_blank"`;
- every badge (Z0, confidence, coverage) pairs its colour with a visible text
  label and a decorative (`aria-hidden`) icon — never colour alone.

## The API seam

The app calls the API through the relative `/api` prefix, so it is always
same-origin and needs no CORS configuration or hard-coded host. Every request is
a `GET` against a **fixed** catalogue path built only from internal identifiers
(provider slugs, offer ids) and enum/keyword query parameters — no URL or host is
ever taken from user input, so there is no SSRF surface, and the app never writes
or touches the database.

- **In the container**, `nginx` reverse-proxies `/api/` → `http://api:8000/`
  (see `nginx.conf`), so `/api/health` reaches the API's `/health`.
- **In local `npm run dev`**, Vite proxies `/api` → `http://localhost:8000`
  (see `vite.config.ts`; override with `VITE_API_PROXY_TARGET`).

## Local development

```bash
npm install        # or: npm ci
npm run dev        # Vite dev server on http://localhost:5173
npm run test       # Vitest unit tests
npm run lint       # ESLint
npm run build      # Type-check + production build to dist/
```

These commands are also wired into the repository-level scripts
(`scripts/bootstrap-dev`, `scripts/test`) and the Docker Compose stack.

## Running in the stack

The `web` service in `docker-compose.yml` builds this image and serves the app
on `http://localhost:${WEB_PORT:-8080}`. It depends on the `api` service being
healthy. Use the canonical scripts from the repository root:

```bash
scripts/stack-up.ps1      # build + start the stack (postgres, api, worker, scheduler, web)
scripts/stack-smoke.ps1   # verify the web container is healthy and serving
scripts/stack-down.ps1    # stop the stack
```

## Container health

`nginx` exposes `GET /healthz` (returns `200 ok`), used by the Docker
healthcheck. The application itself surfaces API connectivity in the UI rather
than failing the container when the API is down.
