# FreeTier Atlas — Web frontend

A [Vite](https://vite.dev) + [React](https://react.dev) + TypeScript single-page
application. This is the **F002 application scaffold** frontend: it renders a
minimal landing view and verifies the seam to the API by fetching its health
endpoint. Catalogue, adviser, evidence, and comparison features arrive in later
increments (F006/F008).

## Layout

| Path                | Purpose                                                       |
| ------------------- | ------------------------------------------------------------- |
| `src/main.tsx`      | React entry point.                                            |
| `src/App.tsx`       | Landing view with a live **API status** panel.                |
| `src/api.ts`        | Minimal API client (`GET /api/health`).                       |
| `src/App.test.tsx`  | Vitest + Testing Library unit tests (offline, mocked `fetch`).|
| `nginx.conf`        | Runtime server: SPA fallback, `/healthz`, `/api/` proxy.      |
| `Dockerfile`        | Multi-stage build (Node build → nginx runtime).               |

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
