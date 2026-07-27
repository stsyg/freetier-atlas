# Security, Privacy, and Abuse Controls

## Public adviser

Not a general chatbot. Input is normalized into a strict requirements schema.

The model cannot access credentials, filesystem, shell, arbitrary URLs, admin APIs, deployment APIs, or user-supplied tools.

## Initial limits

- 3 AI recommendations/IP/day
- 10 deterministic recommendations/IP/day
- 1 active generation/session
- 2,000 input characters
- 4,000 output tokens
- no file uploads
- no arbitrary URLs
- global provider caps
- duplicate caching
- provider circuit breakers
- deterministic fallback
- admin kill switch

All values are configurable.

## External model consent

Identify provider/model, warn against secrets or personal/confidential data, explain external processing, link provider policy, and require explicit consent.

## Retention

- Project descriptions: session only
- Prompts in logs: prohibited
- ZIPs: browser only
- Evidence: per architecture policy
- Security logs: minimal and short-lived

## ZIP security

Fixed paths, no traversal, no binaries, no secrets, placeholder `.env.example`, maximum size, Compose parsing/validation, and generation manifest.

## Admin

GitHub OAuth, explicit `stsyg` allowlist, CSRF protection, secure cookies, audit trail, environment/secret-store credentials, and validated YAML diffs.

## Source fetching

Official-domain allowlists, SSRF protection, private-network blocking, timeouts, size caps, MIME validation, safe archives, browser sandboxing, and MCP capability allowlists.

## Abuse-control enforcement (F007 slice 2)

The controls listed under *Initial limits* are enforced on the public adviser
endpoints (`POST /adviser/recommend`, `/adviser/recommend/assisted`,
`/adviser/export`) by `app.adviser.abuse`. All state is persisted in PostgreSQL
(migration `0008_adviser_abuse_controls`); there is no Redis and no new runtime
dependency (stdlib `hmac`/`hashlib`/`secrets`/`time` + the existing SQLAlchemy).

- **Per-IP rate limiting.** A fixed daily window per IP and scope. The client IP
  is **never stored or logged**: only a keyed HMAC-SHA256 digest (`ABUSE_SECRET`)
  is persisted in `rate_limit_bucket`. A deterministic-endpoint overage returns
  **HTTP 429** with a `Retry-After` header. The assisted endpoint never 429s for
  an AI reason — it degrades (below) — except for one absolute anti-hammering
  ceiling.
- **AI kill switch.** A persisted flag (`abuse_flag`, plus an `AI_KILL_SWITCH`
  env override) that forces the assisted path to the deterministic fallback
  (`llm_used=false`, reason `ai_kill_switch`) without hard-failing. The
  deterministic `/adviser/recommend` path is unaffected. Toggle it with
  `python -m app.adviser.abuse.admin kill-switch on|off` (a later slice adds the
  authenticated admin surface).
- **Per-provider circuit breaker.** After `ABUSE_BREAKER_THRESHOLD` consecutive
  provider failures/timeouts the breaker opens for
  `ABUSE_BREAKER_COOLDOWN_SECONDS`; while open the provider is skipped without
  being called (the router degrades down the ladder to deterministic fallback).
  After the cooldown a single half-open probe closes the breaker on success or
  re-opens it on failure. State is persisted in `circuit_breaker` so a
  known-down provider survives an API restart within its cooldown.
- **Request dedupe.** Identical requests (keyed HMAC of the canonical body per
  scope) inside `ABUSE_DEDUPE_WINDOW_SECONDS` are collapsed (`request_dedupe`) so
  a burst of duplicates neither multiplies LLM calls nor burns the rate budget.
- **Self-hosted proof-of-work (no external CAPTCHA).** Beyond the free AI
  threshold, an assisted request must carry a solved, server-signed PoW
  challenge. `POST /adviser/challenge` issues a stdlib-HMAC-signed token; the
  client finds a nonce whose `sha256(f"{token}:{nonce}")` has `difficulty`
  leading hex zeros and submits it via `X-PoW-Token` / `X-PoW-Nonce`. The server
  verifies the signature (constant-time), expiry, and work, then atomically
  consumes the single-use challenge (`pow_challenge`). Difficulty is configurable
  and low by default. A missing/invalid proof degrades gracefully.
- **Quota-exhaustion semantics.** When the AI budget is exhausted, the kill
  switch is on, the breaker is open, or a required PoW is absent, the **assisted**
  endpoint degrades to the deterministic fallback (HTTP 200, `llm_used=false`,
  with a clear `fallback_reason`). A **deterministic** overage returns HTTP 429.

Privacy posture is preserved throughout: no raw IP or request body is stored
(only keyed HMAC digests), and prompts/descriptions are never logged. Consent
remains per-request and ephemeral.
