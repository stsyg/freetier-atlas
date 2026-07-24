# FreeTier Atlas — Web frontend

A [Vite](https://vite.dev) + [React](https://react.dev) + TypeScript single-page
application. It renders the **public Cloudflare provider experience** (F005
slice 4): a read-only, evidence-backed view of Cloudflare's published free-tier
offers, consumed entirely from the catalogue read API over the same-origin
`/api` proxy. Catalogue-wide search, cross-provider comparison, and the adviser
arrive in a later increment (F006).

The page renders, for Cloudflare: category / service states with zero-cost (Z0)
badges; each offer's Z0 rating with the plain-language reasons behind it; the
official evidence (source + provenance + link) backing each claim; a confidence
**label** as the primary signal (the numeric score lives only in an advanced
disclosure); the offer's version history and change events; completeness and
freshness signals; and the offer's quota rows. Values the API cannot verify are
shown honestly as "Unknown" — never guessed.

## Layout

| Path                     | Purpose                                                              |
| ------------------------ | ------------------------------------------------------------------- |
| `src/main.tsx`           | React entry point.                                                  |
| `src/App.tsx`            | Loads and renders the Cloudflare provider page (loading/error/data).|
| `src/api.ts`             | Read-only catalogue client (typed `GET` fetchers over `/api`).      |
| `src/catalogue/`         | Presentational components + `format.ts` plain-language helpers.     |
| `src/App.test.tsx`       | Integration tests (offline, mocked `fetch`) covering all scope items.|
| `src/api.test.ts`        | API-client tests (paths, headers, error handling).                 |
| `src/catalogue/*.test.*` | Component + formatter unit tests (a11y, honest "Unknown").          |
| `nginx.conf`             | Runtime server: SPA fallback, `/healthz`, `/api/` proxy.            |
| `Dockerfile`             | Multi-stage build (Node build → nginx runtime).                    |

## Accessibility

Accessibility is part of "done": the page uses semantic landmarks and a single
`<h1>`, an accessible quota `<table>` with a caption and row/column headers, and
native keyboard-operable `<details>` disclosures. Z0, confidence, and evidence
badges never rely on colour alone — each pairs its colour with a visible text
label and a decorative (aria-hidden) icon.

## The API seam

The app calls the API through the relative `/api` prefix, so it is always
same-origin and needs no CORS configuration or hard-coded host:

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
