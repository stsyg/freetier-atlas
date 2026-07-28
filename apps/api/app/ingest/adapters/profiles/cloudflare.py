"""Cloudflare OFFICIAL free-tier extraction profiles (F005, relocated in F008 S3).

Provider-specific selectors expressed purely as data. Each profile reads one
offer-centric row (one Cloudflare product on its free tier) from a captured
official ``developers.cloudflare.com`` snapshot. Every per-limit value is coerced
verbatim as ``text`` (never ``list``) so a real quota such as ``100,000/day`` is
captured exactly rather than being split on its thousands separator -- honouring
"unknown is better than guessed": a missing column yields ``None`` (UNKNOWN),
never a fabricated number.

Relocated verbatim from :mod:`app.ingest.adapters.html` behind the F008 S3
registration seam. The profile *data* is byte-identical to the F005 definitions,
so extraction, evidence locations and the candidate ``content_hash`` are
unchanged; only the registration mechanism moved. This module is the template a
provider slice copies: one file, no shared-file edit.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..html import HtmlColumn, HtmlExtractionProfile
from . import register_html_profile

CLOUDFLARE_WORKERS_LIMITS = register_html_profile(
    HtmlExtractionProfile(
        name="cloudflare_workers_limits",
        table_id="workers-free-tier",
        columns={
            "service": HtmlColumn("service", "text"),
            "offer type": HtmlColumn("offer_type", "text"),
            "card required": HtmlColumn("requires_card", "bool"),
            "paid dependencies": HtmlColumn("has_paid_dependencies", "bool"),
            "requests per day": HtmlColumn("requests_per_day", "text"),
            "cpu time": HtmlColumn("cpu_time", "text"),
            "memory": HtmlColumn("memory", "text"),
            "subrequests per request": HtmlColumn("subrequests_per_request", "text"),
            "worker size": HtmlColumn("worker_size", "text"),
            "workers per account": HtmlColumn("workers_per_account", "text"),
            "cron triggers per account": HtmlColumn("cron_triggers_per_account", "text"),
            "static asset files": HtmlColumn("static_asset_files", "text"),
            "static asset file size": HtmlColumn("static_asset_file_size", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

CLOUDFLARE_PAGES_LIMITS = register_html_profile(
    HtmlExtractionProfile(
        name="cloudflare_pages_limits",
        table_id="pages-free-tier",
        columns={
            "service": HtmlColumn("service", "text"),
            "offer type": HtmlColumn("offer_type", "text"),
            "card required": HtmlColumn("requires_card", "bool"),
            "paid dependencies": HtmlColumn("has_paid_dependencies", "bool"),
            "builds per month": HtmlColumn("builds_per_month", "text"),
            "concurrent builds": HtmlColumn("concurrent_builds", "text"),
            "custom domains": HtmlColumn("custom_domains", "text"),
            "files": HtmlColumn("files", "text"),
            "file size": HtmlColumn("file_size", "text"),
            "header rules": HtmlColumn("header_rules", "text"),
            "redirects": HtmlColumn("redirects", "text"),
            "projects per account": HtmlColumn("projects_per_account", "text"),
            "exhaustion behaviour": HtmlColumn("exhaustion_behaviour", "text"),
        },
        required_fields=("service", "offer_type"),
    )
)

__all__: Sequence[str] = (
    "CLOUDFLARE_WORKERS_LIMITS",
    "CLOUDFLARE_PAGES_LIMITS",
)
