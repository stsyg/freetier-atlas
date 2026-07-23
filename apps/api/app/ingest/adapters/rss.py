"""RSS 2.0 / Atom source adapter (standard library only).

Parses an official feed (changelog, release notes, status updates) into
*candidate* facts using :mod:`xml.etree.ElementTree`. Each ``<item>`` (RSS) or
``<entry>`` (Atom) becomes one :class:`~app.ingest.base.CandidateFacts`.

Design contract (docs/AGENT_HARNESS.md "unknown is better than guessed"):

* The adapter reaches the network *only* through the injected
  :class:`~app.ingest.fetch.Fetcher`; it imports no HTTP client. A non-allowlisted
  URL is therefore refused by the shared safe fetcher before any connection.
* Material offer facts (``offer_type``, ``requires_card``,
  ``has_paid_dependencies``, ``quotas``) are read *only* from machine-readable
  ``key:value`` feed categories. Prose is never mined for a material value, and a
  missing/ambiguous value is left ``None`` (UNKNOWN) -- never guessed.
* Malformed input never crashes and never fabricates a value: a ``DOCTYPE`` /
  entity declaration (XXE / billion-laughs defence-in-depth) or an
  ``xml.etree`` ``ParseError`` yields a single ``rejected`` candidate whose only
  facts describe the failure, which :meth:`validate` flags -- a *captured
  validation failure* the scan records as an error.

Material facts are tagged on a feed entry as ``<category>`` values of the form
``key:value`` (Atom uses the ``term`` attribute)::

    <category>service:Workers</category>
    <category>offer_type:always_free</category>
    <category>requires_card:false</category>
    <category>quota:requests=hard_stop</category>
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from app.ingest.adapters._common import host, normspace, to_bool
from app.ingest.base import (
    AdapterHealth,
    CandidateFacts,
    EvidenceLocation,
    SourceAdapter,
    SourceDocument,
)
from app.ingest.fetch import Fetcher, FetchError, FetchResult

#: Fields that must be present for a feed candidate to be valid; a missing one is
#: a *partial* candidate (UNKNOWN), flagged by :meth:`RssFeedAdapter.validate`.
_REQUIRED_FIELDS: tuple[str, ...] = ("service", "offer_type", "link")

_KNOWN_ROOTS: frozenset[str] = frozenset({"rss", "feed", "rdf"})
_EXCERPT_LIMIT = 280


def _localname(tag: str) -> str:
    """Return an element's namespace-stripped, lowercased local name."""

    return tag.rsplit("}", 1)[-1].lower()


def _children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent if _localname(child.tag) == name]


def _first(parent: ET.Element, *names: str) -> ET.Element | None:
    wanted = {n.lower() for n in names}
    for child in parent:
        if _localname(child.tag) in wanted:
            return child
    return None


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = normspace(element.text)
    return value or None


def _has_dtd(text: str) -> bool:
    """True if the document declares a DOCTYPE or an entity (rejected as unsafe)."""

    head = text[:4096].lower()
    return "<!doctype" in head or "<!entity" in head


class RssFeedAdapter(SourceAdapter):
    """Adapter that extracts candidate facts from an RSS/Atom feed."""

    name = "rss"

    def __init__(
        self,
        fetcher: Fetcher,
        source_urls: Sequence[str],
        *,
        provider: str | None = None,
    ) -> None:
        super().__init__(fetcher)
        self._source_urls = tuple(source_urls)
        self._provider = provider

    # -- contract methods --------------------------------------------------

    def discover(self) -> Sequence[str]:
        return self._source_urls

    def fetch(self, url: str) -> FetchResult:
        return self.fetcher.fetch(url)

    def canonicalize(self, result: FetchResult) -> SourceDocument:
        # Best-effort decode; never raises. The raw bytes remain the provenance
        # hash source, the decoded text is what the extractor parses.
        canonical = result.content.decode("utf-8", errors="replace").strip()
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
        text = document.canonical

        if _has_dtd(text):
            return [self._rejected(document, provider, "dtd_not_allowed")]

        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return [self._rejected(document, provider, "malformed_xml", str(exc))]

        if _localname(root.tag) not in _KNOWN_ROOTS:
            return [self._rejected(document, provider, "unrecognised_feed", _localname(root.tag))]

        items = self._items(root)
        return [
            self._candidate(document, provider, item, index) for index, item in enumerate(items)
        ]

    def validate(self, candidate: CandidateFacts) -> Sequence[str]:
        facts = candidate.facts
        if "error" in facts:
            detail = facts.get("detail")
            suffix = f" ({detail})" if detail else ""
            return [f"Feed rejected: {facts['error']}{suffix}"]

        problems: list[str] = []
        for field in _REQUIRED_FIELDS:
            if not facts.get(field):
                problems.append(f"Missing required field '{field}'.")
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
            detail="Feed reachable.",
            source_url=probe,
        )

    # -- internals ---------------------------------------------------------

    def _items(self, root: ET.Element) -> list[ET.Element]:
        tag = _localname(root.tag)
        if tag == "rss":
            channel = _first(root, "channel")
            return _children(channel, "item") if channel is not None else []
        if tag == "rdf":
            return _children(root, "item")
        if tag == "feed":
            return _children(root, "entry")
        return []

    def _candidate(
        self,
        document: SourceDocument,
        provider: str,
        item: ET.Element,
        index: int,
    ) -> CandidateFacts:
        structured, quotas = self._parse_categories(item)
        title = _text(_first(item, "title"))
        link = self._link(item)
        guid = _text(_first(item, "guid", "id"))
        published = _text(_first(item, "pubdate", "published", "updated"))
        summary = _text(_first(item, "description", "summary"))

        facts = {
            "service": structured.get("service") or title,
            "offer_type": structured.get("offer_type"),
            "requires_card": to_bool(structured.get("requires_card")),
            "has_paid_dependencies": to_bool(structured.get("has_paid_dependencies")),
            "quotas": tuple(sorted(quotas)),
            "summary": summary,
            "link": link,
            "guid": guid,
            "published": published,
        }
        excerpt_source = " ".join(part for part in (title, summary) if part)
        location = EvidenceLocation(
            url=link or document.url,
            selector=f"item[{guid or index}]",
            excerpt=excerpt_source[:_EXCERPT_LIMIT] or None,
            content_hash=document.content_hash,
        )
        return CandidateFacts(
            provider=provider,
            source_url=document.url,
            facts=facts,
            evidence=(location,),
            verification_state="candidate",
        )

    def _parse_categories(self, item: ET.Element) -> tuple[dict[str, str], list[str]]:
        structured: dict[str, str] = {}
        quotas: list[str] = []
        for child in item:
            if _localname(child.tag) != "category":
                continue
            raw = child.get("term")
            if raw is None:
                raw = child.text or ""
            raw = normspace(raw)
            if not raw or ":" not in raw:
                continue
            key, _, value = raw.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if not value:
                continue
            if key == "quota":
                quotas.append(value)
            else:
                structured[key] = value
        return structured, quotas

    def _link(self, item: ET.Element) -> str | None:
        for child in item:
            if _localname(child.tag) != "link":
                continue
            href = child.get("href")
            if href and href.strip():
                return href.strip()
            if child.text and child.text.strip():
                return child.text.strip()
        return None

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


__all__ = ("RssFeedAdapter",)
