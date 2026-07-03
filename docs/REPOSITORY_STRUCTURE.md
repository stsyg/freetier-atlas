# Planned Monorepo Structure

```text
freetier-atlas/
├── AGENTS.md
├── CODEX_TASKS.md
├── PLAN.md
├── README.md
├── LICENSE
├── NOTICE
├── ADDITIONAL_TERMS.md
├── THIRD_PARTY_NOTICES.md
├── docker-compose.yml
├── .env.example
├── apps/
│   ├── web/
│   ├── api/
│   └── worker/
├── packages/
│   ├── schemas/
│   ├── provider-sdk/
│   ├── verification/
│   ├── adviser/
│   └── deployment-templates/
├── config/
│   ├── application.yaml
│   ├── schedules.yaml
│   ├── verification.yaml
│   ├── notifications.yaml
│   ├── llm-providers.yaml
│   └── providers/
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── adviser-evals/
│   └── fixtures/providers/
├── deployment/
│   ├── compose/
│   ├── public-z0/
│   └── kubernetes/
├── docs/
│   ├── adr/
│   ├── adding-a-provider.md
│   ├── source-adapters.md
│   ├── verification-rules.md
│   ├── provider-schema.md
│   └── scaling-to-more-providers.md
└── .github/
    ├── workflows/
    ├── ISSUE_TEMPLATE/
    └── pull_request_template.md
```

Use trunk-based development: protected `main`, short-lived feature branches, provider-specific branches, and early draft PRs. Do not add a permanent `develop` branch unless later evidence justifies it.
