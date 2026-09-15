# ADR 0003: Cloudflare Pages primary, GitHub Pages mirror

Status: Provisionally accepted pending onboarding test

> Extended by ADR 0007, which supersedes this record only on the question of
> what the public deployment serves (a static snapshot). The host choice below —
> Cloudflare Pages primary with a GitHub Pages mirror — is unchanged.

The GitHub owner is `stsyg`, so the current account cannot produce `freetier-atlas.github.io`. Use `freetier-atlas.pages.dev` if available and no-payment onboarding passes. Mirror at `stsyg.github.io/freetier-atlas/`.
