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
