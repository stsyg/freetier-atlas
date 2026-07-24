"""A conservative, deterministic free-text -> candidate requirements parser.

This is **tier 1** of the routing ladder (``docs/ARCHITECTURE.md`` "LLM
routing"): a pure, rule-based interpreter that runs *before* any LLM. For text
that clearly names a canonical category together with a quantified demand, it
produces a candidate structured requirements dict identical to what a user would
have typed into the structured form -- so the resulting recommendation is
byte-identical to ``POST /adviser/recommend``. It uses **no** LLM, needs **no**
consent, performs **no** network or filesystem I/O, and re-derives **nothing**
about Z0 / quotas / classification.

It is deliberately conservative: when it cannot confidently extract at least one
requirement with at least one demand, it returns ``None`` ("I don't know"),
which lets the router escalate to an LLM tier (if any is enabled and consented)
and otherwise fall back to a graceful deterministic "couldn't interpret"
response. It only ever emits tokens drawn from a fixed vocabulary or a single
short ``[a-z0-9-]`` word, so it never produces URL/host/path-like output; the
strict request schema is still the final gate on everything it proposes.
"""

from __future__ import annotations

import re
from typing import Any

from app.read_api.taxonomy import canonical_slugs

# --- Category detection ---------------------------------------------------- #
# Ordered (category slug, keyword phrases). Iterated in taxonomy order so the
# output is deterministic. Phrases are matched as lowercase substrings on word
# boundaries where practical. No phrase contains a URL marker ("/", "://", "@",
# "..", "www."), so a description made only of these stays URL-clean.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("compute-vms", ("virtual machine", "compute instance", "compute vm", " vps", " vm ")),
    (
        "containers-app-hosting",
        ("container", "app hosting", "web app hosting", "app service", "host a web app"),
    ),
    ("serverless-functions", ("serverless", "cloud function", "edge function", "lambda function")),
    (
        "relational-databases",
        ("relational database", "postgres", "postgresql", "mysql", "sql database"),
    ),
    (
        "nosql-key-value",
        ("nosql", "key-value", "key value", "document database", "redis cache", " redis"),
    ),
    (
        "object-file-storage",
        (
            "object storage",
            "file storage",
            "blob storage",
            "bucket",
            "store files",
            "store objects",
        ),
    ),
    ("networking-cdn-dns", ("cdn", "content delivery", "dns hosting", "dns records")),
    (
        "queues-messaging-jobs",
        ("message queue", "task queue", "messaging", "scheduled job", "cron job", "background job"),
    ),
    ("auth-identity", ("authentication", "identity provider", "user login", "sign in", "sign-in")),
    (
        "cicd-source-control",
        ("continuous integration", "continuous delivery", "cicd", "build pipeline", "git hosting"),
    ),
    (
        "monitoring-logs-tracing",
        (
            "monitoring",
            "observability",
            "log aggregation",
            "logging",
            "tracing",
            "metrics dashboard",
        ),
    ),
    (
        "ai-inference-embeddings",
        ("model inference", "llm inference", "embeddings", "ai inference", "text generation"),
    ),
    (
        "email-notifications-comms",
        (
            "transactional email",
            "send email",
            "email delivery",
            "notifications",
            "push notification",
        ),
    ),
    (
        "secrets-config-devtools",
        ("secret management", "secrets manager", "configuration store", "feature flags"),
    ),
)

# --- Demand extraction ----------------------------------------------------- #
# Units are matched against a fixed vocabulary; the canonical (singular) form is
# emitted so the output is stable regardless of plural/casing in the prose.
_UNIT_CANONICAL: dict[str, str] = {
    "gb": "GB",
    "gigabyte": "GB",
    "gigabytes": "GB",
    "mb": "MB",
    "megabyte": "MB",
    "megabytes": "MB",
    "tb": "TB",
    "terabyte": "TB",
    "terabytes": "TB",
    "kb": "KB",
    "request": "requests",
    "requests": "requests",
    "invocation": "invocations",
    "invocations": "invocations",
    "hour": "hours",
    "hours": "hours",
    "minute": "minutes",
    "minutes": "minutes",
    "user": "users",
    "users": "users",
    "message": "messages",
    "messages": "messages",
    "email": "emails",
    "emails": "emails",
    "build": "builds",
    "builds": "builds",
}

# A default metric for each unit family, used only when the prose does not name a
# metric explicitly. Keeps the extracted demand honest and stable.
_UNIT_DEFAULT_METRIC: dict[str, str] = {
    "GB": "storage",
    "MB": "storage",
    "TB": "storage",
    "KB": "storage",
    "requests": "requests",
    "invocations": "invocations",
    "hours": "compute",
    "minutes": "build_minutes",
    "users": "users",
    "messages": "messages",
    "emails": "emails",
    "builds": "builds",
}

# Metric nouns the parser will accept when named right after a quantity. Bounded
# vocabulary -> never emits arbitrary/user-controlled tokens as a metric.
_METRIC_WORDS: frozenset[str] = frozenset(
    {
        "storage",
        "requests",
        "invocations",
        "compute",
        "bandwidth",
        "egress",
        "traffic",
        "users",
        "messages",
        "emails",
        "builds",
        "operations",
        "reads",
        "writes",
    }
)

_UNIT_ALTERNATION = "|".join(sorted(_UNIT_CANONICAL, key=len, reverse=True))
# e.g. "10 GB storage", "500 requests per month", "2 gb of object storage"
_DEMAND_RE = re.compile(
    r"(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>" + _UNIT_ALTERNATION + r")\b"
    r"(?:\s+(?:of\s+)?(?P<metric>[a-z][a-z-]{0,40}))?",
    re.IGNORECASE,
)

_PERIOD_RE = re.compile(
    r"(?:per|each|/|a)\s*(?P<period>month|day|week|year|hour|minute)|"
    r"(?P<adverb>monthly|daily|weekly|yearly|hourly)",
    re.IGNORECASE,
)
_ADVERB_PERIOD = {
    "monthly": "month",
    "daily": "day",
    "weekly": "week",
    "yearly": "year",
    "hourly": "hour",
}

# Clause separators: commas, semicolons, sentence stops, and the words
# "and"/"plus"/"also". A period is only a separator when it is NOT a decimal
# point (i.e. not immediately followed by a digit) so "0.5 GB" stays intact.
_CLAUSE_SPLIT_RE = re.compile(r"[,;]|\.(?!\d)|\band\b|\bplus\b|\balso\b", re.IGNORECASE)

_CANONICAL_SLUGS = frozenset(canonical_slugs())


def _period_in(text: str) -> str | None:
    match = _PERIOD_RE.search(text)
    if match is None:
        return None
    if match.group("adverb"):
        return _ADVERB_PERIOD[match.group("adverb").lower()]
    return match.group("period").lower()


def _demands_in(clause: str) -> list[dict[str, Any]]:
    """Extract quantified demands from a single clause, in order of appearance."""

    demands: list[dict[str, Any]] = []
    period = _period_in(clause)
    for match in _DEMAND_RE.finditer(clause):
        unit_raw = match.group("unit").lower()
        unit = _UNIT_CANONICAL.get(unit_raw)
        if unit is None:  # pragma: no cover - alternation only yields known units
            continue
        metric_word = (match.group("metric") or "").lower().strip("-")
        if metric_word in _METRIC_WORDS:
            metric = metric_word
        else:
            metric = _UNIT_DEFAULT_METRIC[unit]
        demand: dict[str, Any] = {
            "metric": metric,
            "amount": match.group("amount"),
            "unit": unit,
        }
        if period is not None:
            demand["period"] = period
        demands.append(demand)
    return demands


def _category_in(clause: str) -> str | None:
    """Return the first canonical category whose keyword appears in ``clause``."""

    lowered = clause.lower()
    for slug, phrases in _CATEGORY_KEYWORDS:
        for phrase in phrases:
            if phrase in lowered:
                return slug
    return None


def deterministic_parse(description: str, limits: Any = None) -> dict[str, Any] | None:
    """Parse ``description`` into a candidate requirements dict, or ``None``.

    Returns a dict shaped like :class:`app.adviser.schema.RecommendationRequest`
    when it can confidently extract at least one canonical category with at
    least one quantified demand; otherwise returns ``None`` so the router can
    escalate or fall back. Purely rule-based: no LLM, no I/O, no Z0/quota logic.
    """

    if not isinstance(description, str):
        return None
    text = description.strip()
    if not text:
        return None

    requirements: list[dict[str, Any]] = []
    seen_categories: set[str] = set()
    for clause in _CLAUSE_SPLIT_RE.split(text):
        clause = clause.strip()
        if not clause:
            continue
        category = _category_in(clause)
        if category is None or category not in _CANONICAL_SLUGS:
            continue
        demands = _demands_in(clause)
        if not demands:
            continue
        if category in seen_categories:
            # Merge additional demands into the existing requirement for the
            # same category so the output stays one requirement per category.
            for existing in requirements:
                if existing["category"] == category:
                    existing["demands"].extend(demands)
                    break
            continue
        requirements.append({"category": category, "demands": demands})
        seen_categories.add(category)

    if not requirements:
        return None
    return {"requirements": requirements}


__all__ = ["deterministic_parse"]
