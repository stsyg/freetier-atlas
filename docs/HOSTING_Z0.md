# Z0 Hosting Decision

## GitHub Pages naming constraint

Authenticated owner: `stsyg`.

Root GitHub Pages sites use the owner name:

- Available: `https://stsyg.github.io/`
- Project site: `https://stsyg.github.io/freetier-atlas/`
- Not available from this account: `https://freetier-atlas.github.io/`

The requested root URL requires renaming the account, creating an account/organization named `freetier-atlas`, or using a purchased custom domain. None is selected.

## Candidates

### Cloudflare Pages — primary recommendation

Target: `https://freetier-atlas.pages.dev/` if available.

Benefits:

- Clean project-specific subdomain
- Static asset requests documented as free and unlimited
- 500 builds/month on Free
- 20,000 files/site on Free
- Global edge delivery
- GitHub integration and previews

Verification requirement: confirm real signup and deployment require no payment method and avoid paid Workers/services.

Provisional status: Z0 candidate pending onboarding test.

### GitHub Pages — mirror

Target: `https://stsyg.github.io/freetier-atlas/`.

Benefits: native repository integration, public repos on GitHub Free, simple Actions deployment, 100 GB/month soft bandwidth.

Status: Z0 static mirror.

### Firebase Hosting Spark — fallback

No-cost Spark plan, free subdomain, 10 GB/month transfer, and disabling rather than billing when not on Blaze.

Status: Z0 static fallback.

### Vercel Hobby

Free with hard usage limits in most cases, but restricted to personal/non-commercial use.

Status: Z0 with policy caveat; not preferred.

### GitLab Pages

Free static hosting but requires mirroring to another source platform.

Status: Z0 but unnecessary for MVP.

### Netlify

Credit-based free plan is more complex and needs current hard-stop/payment verification.

Status: not selected.

## Decision

Primary Cloudflare Pages after onboarding verification. GitHub Pages mirror. Keep dynamic components separate and publish monthly Z0 verification.

## Official references

- https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages
- https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
- https://developers.cloudflare.com/pages/platform/limits/
- https://developers.cloudflare.com/pages/functions/pricing/
- https://firebase.google.com/docs/hosting/usage-quotas-pricing
- https://vercel.com/docs/plans/hobby
