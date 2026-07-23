"""Static-document / HTML source adapter (standard library only).

Parses an official documentation or pricing page into *candidate* facts using
:class:`html.parser.HTMLParser`. The adapter itself contains **no**
provider-specific selectors: it is a generic table-walking engine driven by a
declarative :class:`HtmlExtractionProfile`. All provider/document-specific
knowledge (which table to read, and how its columns map to offer facts) lives in
the :data:`HTML_EXTRACTION_PROFILES` registry -- i.e. in configuration, not code.

Design contract (docs/AGENT_HARNESS.md "unknown is better than guessed"):

* The network is reached only through the injected
  :class:`~app.ingest.fetch.Fetcher`; no HTTP client is imported, so a
  non-allowlisted URL is refused by the shared safe fetcher pre-connection.
* Each body row of the profile's table becomes one candidate. A column that is
  absent, or a cell that is empty / ambiguous, yields ``None`` (UNKNOWN) -- never
  a guessed value.
* Malformed input never crashes and never fabricates a value: if the profile's
  table is absent the adapter emits a single ``rejected`` candidate that
  :meth:`validate` flags (a *captured validation failure*); a partial row simply
  carries ``None`` for its missing fields.

An offer table is expected to look like::

    <table id="free-tier">
      <tr><th>Service</th><th>Offer type</th><th>Card required</th>
          <th>Paid dependencies</th><th>Exhaustion</th></tr>
      <tr><td>Workers</td><td>always_free</td><td>No</td>
          <td>No</td><td>hard_stop</td></tr>
    </table>
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser

from app.ingest.adapters._common import host, normspace, to_bool
from app.ingest.base import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    SourceAdapter,
    SourceDocument,
)
from app.ingest.fetch import Fetcher, FetchError, FetchResult

_EXCERPT_LIMIT = 280
_LIST_SEPARATORS = (",", ";")


class UnknownProfileError(ValueError):
    """Raised when a source references an HTML extraction profile that is unknown."""


@dataclass(frozen=True)
class HtmlColumn:
    """How one table column maps to a candidate fact field.

    ``coercion`` is one of ``"text"`` (verbatim), ``"bool"`` (yes/no -> bool, else
    ``None``) or ``"list"`` (split on commas/semicolons into a sorted tuple).
    """

    field: str
    coercion: str = "text"


@dataclass(frozen=True)
class HtmlExtractionProfile:
    """A declarative recipe for extracting offer rows from one document shape.

    The table is selected by ``table_id`` (exact ``id`` match) or ``table_class``
    (a class token); ``columns`` maps a *normalised, lowercased* header label to
    the :class:`HtmlColumn` it feeds. Nothing here is code -- a new document layout
    is a new profile entry, not a code change.
    """

    name: str
    table_id: str | None = None
    table_class: str | None = None
    columns: Mapping[str, HtmlColumn] = field(default_factory=dict)
    required_fields: tuple[str, ...] = ("service", "offer_type")

    def __post_init__(self) -> None:
        normalised = {normspace(label).lower(): col for label, col in self.columns.items()}
        object.__setattr__(self, "columns", normalised)


#: Registry of extraction profiles keyed by name. This stands in for the
#: provider-config-supplied profiles a later slice will load from YAML; the point
#: is that provider-specific selectors live here as data, not in adapter code.
HTML_EXTRACTION_PROFILES: dict[str, HtmlExtractionProfile] = {
    "quota_document": HtmlExtractionProfile(
        name="quota_document",
        table_id="free-tier",
        columns={
            "service": HtmlColumn("service", "text"),
            "offer type": HtmlColumn("offer_type", "text"),
            "card required": HtmlColumn("requires_card", "bool"),
            "paid dependencies": HtmlColumn("has_paid_dependencies", "bool"),
            "exhaustion": HtmlColumn("quotas", "list"),
        },
        required_fields=("service", "offer_type"),
    ),
    "pricing_document": HtmlExtractionProfile(
        name="pricing_document",
        table_class="pricing",
        columns={
            "plan": HtmlColumn("service", "text"),
            "billing": HtmlColumn("offer_type", "text"),
            "credit card": HtmlColumn("requires_card", "bool"),
            "paid add-ons": HtmlColumn("has_paid_dependencies", "bool"),
            "quota exhaustion": HtmlColumn("quotas", "list"),
        },
        required_fields=("service", "offer_type"),
    ),
    # -- Cloudflare OFFICIAL free-tier profiles (F005) --------------------
    # Provider-specific selectors expressed purely as data. Each profile reads
    # one offer-centric row (one Cloudflare product on its free tier) from a
    # captured official developers.cloudflare.com snapshot. Every per-limit
    # value is coerced verbatim as ``text`` (never ``list``) so a real quota
    # such as "100,000/day" is captured exactly rather than being split on its
    # thousands separator -- honouring "unknown is better than guessed": a
    # missing column yields ``None`` (UNKNOWN), never a fabricated number.
    "cloudflare_workers_limits": HtmlExtractionProfile(
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
        },
        required_fields=("service", "offer_type"),
    ),
    "cloudflare_pages_limits": HtmlExtractionProfile(
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
        },
        required_fields=("service", "offer_type"),
    ),
}


def resolve_profile(name: str | None) -> HtmlExtractionProfile:
    """Return the named profile or raise :class:`UnknownProfileError`."""

    try:
        return HTML_EXTRACTION_PROFILES[name]  # type: ignore[index]
    except KeyError as exc:
        raise UnknownProfileError(
            f"No HTML extraction profile named '{name}'; known: {sorted(HTML_EXTRACTION_PROFILES)}."
        ) from exc


def _coerce(raw: str | None, coercion: str) -> object:
    if raw is None:
        return None
    value = normspace(raw)
    if not value:
        return None
    if coercion == "bool":
        return to_bool(value)
    if coercion == "list":
        parts = [value]
        for sep in _LIST_SEPARATORS:
            parts = [piece for chunk in parts for piece in chunk.split(sep)]
        cleaned = sorted({normspace(piece) for piece in parts if normspace(piece)})
        return tuple(cleaned)
    return value


class _TableCollector(HTMLParser):
    """Collect the rows of the first table matching a profile's selector.

    Rows are captured as ``(cells, is_header)`` pairs; ``is_header`` is true when
    the row contained any ``<th>``. Only the outermost matching table is captured
    (nested tables inside it are ignored for row purposes).
    """

    def __init__(self, profile: HtmlExtractionProfile) -> None:
        super().__init__(convert_charrefs=True)
        self._profile = profile
        self.rows: list[tuple[list[str], bool]] = []
        self._table_depth = 0
        self._capturing = False
        self._capture_depth = 0
        self._in_row = False
        self._in_cell = False
        self._is_header = False
        self._cur_cells: list[str] = []
        self._cell_parts: list[str] = []

    def _matches(self, attrs: dict[str, str | None]) -> bool:
        if self._profile.table_id is not None:
            return attrs.get("id") == self._profile.table_id
        if self._profile.table_class is not None:
            classes = (attrs.get("class") or "").split()
            return self._profile.table_class in classes
        return True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if not self._capturing and self._matches(dict(attrs)):
                self._capturing = True
                self._capture_depth = self._table_depth
            return
        if not self._capturing:
            return
        if tag == "tr":
            self._in_row = True
            self._is_header = False
            self._cur_cells = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_parts = []
            if tag == "th":
                self._is_header = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            if self._capturing and self._table_depth == self._capture_depth:
                self._capturing = False
            self._table_depth = max(0, self._table_depth - 1)
            return
        if not self._capturing:
            return
        if tag in ("td", "th") and self._in_cell:
            self._cur_cells.append(normspace("".join(self._cell_parts)))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self.rows.append((self._cur_cells, self._is_header))
            self._in_row = False

    def handle_data(self, data: str) -> None:
        if self._capturing and self._in_cell:
            self._cell_parts.append(data)


class HtmlDocAdapter(SourceAdapter):
    """Adapter that extracts candidate facts from an HTML document via a profile."""

    name = "html"

    def __init__(
        self,
        fetcher: Fetcher,
        source_urls: Sequence[str],
        profile: HtmlExtractionProfile,
        *,
        provider: str | None = None,
    ) -> None:
        super().__init__(fetcher)
        self._source_urls = tuple(source_urls)
        self._profile = profile
        self._provider = provider

    # -- contract methods --------------------------------------------------

    def discover(self) -> Sequence[str]:
        return self._source_urls

    def fetch(self, url: str) -> FetchResult:
        return self.fetcher.fetch(url)

    def canonicalize(self, result: FetchResult) -> SourceDocument:
        canonical = result.content.decode("utf-8", errors="replace")
        return SourceDocument(
            url=result.final_url,
            mime=result.mime,
            content_hash=result.content_hash,
            fetched_at=result.fetched_at,
            raw=result.content,
            canonical=canonical,
        )

    def extract(self, document: SourceDocument) -> Sequence[CandidateFacts]:
        provider = self._provider or host(document.url) or "unknown"

        collector = _TableCollector(self._profile)
        try:
            collector.feed(document.canonical)
            collector.close()
        except Exception as exc:  # noqa: BLE001 - html.parser is lenient; never crash
            return [self._rejected(document, provider, "html_parse_error", str(exc))]

        rows = collector.rows
        if not rows:
            return [
                self._rejected(
                    document, provider, "table_not_found", f"profile={self._profile.name}"
                )
            ]

        header_idx = next((i for i, (_, is_header) in enumerate(rows) if is_header), 0)
        header_index = {
            normspace(label).lower(): idx for idx, label in enumerate(rows[header_idx][0])
        }

        candidates: list[CandidateFacts] = []
        for row_index in range(header_idx + 1, len(rows)):
            cells = rows[row_index][0]
            candidates.append(self._candidate(document, provider, cells, header_index, row_index))
        return candidates

    def validate(self, candidate: CandidateFacts) -> Sequence[str]:
        facts = candidate.facts
        if "error" in facts:
            detail = facts.get("detail")
            suffix = f" ({detail})" if detail else ""
            return [f"Document rejected: {facts['error']}{suffix}"]

        problems: list[str] = []
        for field_name in self._profile.required_fields:
            if facts.get(field_name) in (None, ""):
                problems.append(f"Missing required field '{field_name}'.")
        if not candidate.evidence:
            problems.append("Candidate has no evidence location.")
        return problems

    def evidence(self, candidate: CandidateFacts) -> Sequence[EvidenceLocation]:
        return candidate.evidence

    def health(self) -> AdapterHealth:
        now = datetime.now(UTC)
        urls = self.discover()
        if not urls:
            return AdapterHealth(
                adapter=self.name,
                healthy=False,
                checked_at=now,
                detail="No source URLs configured.",
            )
        probe = urls[0]
        try:
            self.fetch(probe)
        except FetchError as exc:
            return AdapterHealth(
                adapter=self.name,
                healthy=False,
                checked_at=now,
                detail=f"{exc.reason}: {exc}",
                source_url=probe,
            )
        return AdapterHealth(
            adapter=self.name,
            healthy=True,
            checked_at=now,
            detail=f"Document reachable (profile={self._profile.name}).",
            source_url=probe,
        )

    # -- internals ---------------------------------------------------------

    def _candidate(
        self,
        document: SourceDocument,
        provider: str,
        cells: list[str],
        header_index: Mapping[str, int],
        row_index: int,
    ) -> CandidateFacts:
        facts: dict[str, object] = {}
        for label, column in self._profile.columns.items():
            idx = header_index.get(label)
            raw = cells[idx] if (idx is not None and idx < len(cells)) else None
            facts[column.field] = _coerce(raw, column.coercion)

        location = EvidenceLocation(
            url=document.url,
            selector=f"{self._profile.name} row[{row_index}]",
            excerpt=" | ".join(cells)[:_EXCERPT_LIMIT] or None,
            content_hash=document.content_hash,
        )
        return CandidateFacts(
            provider=provider,
            source_url=document.url,
            facts=facts,
            evidence=(location,),
            verification_state="candidate",
        )

    def _rejected(
        self,
        document: SourceDocument,
        provider: str,
        error: str,
        detail: str = "",
    ) -> CandidateFacts:
        location = EvidenceLocation(url=document.url, content_hash=document.content_hash)
        return CandidateFacts(
            provider=provider,
            source_url=document.url,
            facts={"error": error, "detail": detail},
            evidence=(location,),
            verification_state="rejected",
        )


__all__ = (
    "HtmlColumn",
    "HtmlExtractionProfile",
    "HtmlDocAdapter",
    "HTML_EXTRACTION_PROFILES",
    "resolve_profile",
    "UnknownProfileError",
)
