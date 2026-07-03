# Codex Build Tasks

Each task should be a focused, reviewable pull request.

## 001 Initialize repository

Create public `stsyg/freetier-atlas`, add planning package, AGPL/notice placeholders, tooling, CI, and protected `main`.

## 002 Scaffold applications

FastAPI API, Python worker/scheduler, React static frontend, PostgreSQL, Docker Compose, health checks, smoke test.

## 003 Configuration

Pydantic settings, YAML loader, schema export, environment overrides, examples, validation command.

## 004 Domain model

Provider, Service, Offer, Quota, Region, Evidence, Snapshot, OfferVersion, ChangeEvent, Source, ScanRun, ReviewItem, migrations, tests.

## 005 Z0 classifier

Implement Z0/Z1/Z2/Z3, explanation output, exhaustion rules, card/dependency gates, test matrix.

## 006 Adapter SDK

Common interface, safe HTTP, RSS, HTML, MCP wrapper, URL allowlists, hashing/compression, fixtures.

## 007 Cloudflare vertical slice

Provider YAML, official source registry, extraction, verification, API, provider page, evidence, completeness, admin review.

## 008 Catalogue UX

Homepage, cards, search, filters, comparison, history, recent findings, advanced evidence.

## 009 RSS and Discord

All/verified/new/removed/provider/category feeds plus Discord formatting and tests.

## 010 Deterministic adviser

Requirements schema, guided form, matching, scoring, quota math, reductions, self-host fallback, explainability.

## 011 LLM routing

Ollama, free hosted, commercial adapters, consent, caps, circuit breakers, fallback.

## 012 Browser ZIP

Fixed templates, Compose validation, manifest, ZIP creation, security tests.

## 013 Admin

GitHub OAuth, `stsyg` allowlist, scans, evidence/review, YAML diff editor, audit trail.

## 014 Remaining providers

One PR each: GitHub, AWS, Google Cloud, Azure, Vercel, Oracle Cloud.

## 015 Public Z0 deployment

Cloudflare Pages, GitHub Pages mirror, verified dynamic services, quota/headroom endpoint, deployment docs.

## 016 MVP acceptance

Run the complete checklist, fix gaps, perform data-quality review, and tag `v0.1.0`.
