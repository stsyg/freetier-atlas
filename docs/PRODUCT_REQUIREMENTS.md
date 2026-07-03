# Product Requirements

## Audience

Primary user: project owner.  
Secondary audience: developers publicly browsing the read-only catalogue.  
No public accounts and no commercial SaaS plan.

## Offer types

- Always-free services
- Recurring free quotas
- New-customer credits and trials
- Startup programs
- Student/education programs
- Open-source programs
- Hackathons and promotions
- Free developer SaaS
- Self-hostable open-source software
- Personal-use-only services

Temporary and conditional offers are hidden by default.

## MVP providers

Azure, AWS, Google Cloud, Oracle Cloud, Cloudflare, Vercel, and GitHub.

## Category taxonomy

Every provider is evaluated across:

1. Compute and virtual machines
2. Containers and application hosting
3. Serverless functions
4. Relational databases
5. NoSQL and key-value databases
6. Object and file storage
7. Networking, CDN, and DNS
8. Queues, messaging, and scheduled jobs
9. Authentication and identity
10. CI/CD and source control
11. Monitoring, logs, and tracing
12. AI models, inference, and embeddings
13. Email, notifications, and communications
14. Secrets, configuration, and developer tools

Coverage states: verified offer, category exists but no Z0, provider does not offer category, incomplete, stale, or conflicting.

## Zero-cost classes

### Z0 — True $0

No card, purchase, paid dependency, or automatic billing. Quota exhaustion must stop, reject, throttle, sleep, disable, or become read-only.

### Z1 — Billing exposure

Card, automatic overage, or billable dependency exists.

### Z2 — Temporary or conditional

Trials, credits, startup, student, open-source, and promotional programs.

### Z3 — Self-hosted building block

Free software requiring infrastructure. It joins a Z0 architecture only when hosted on verified Z0 infrastructure.

## Homepage

1. “What are you trying to build for $0?”
2. Newly verified offers and changes
3. Provider cards
4. Category browsing
5. Search and filters
6. “This application costs $0” proof

## Adviser behaviour

Priorities: exactly $0, portability, low lock-in.

When no Z0 architecture exists:

1. Explain the blocking requirement.
2. Suggest requirement reductions.
3. Recalculate.
4. Suggest self-hosting.
5. Show Z1/Z2 only in a separate clearly marked section.

## Privacy

Project descriptions are not persisted or logged by default. External model use requires explicit consent and identifies the provider. Local/Ollama mode is supported.
