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
* Legacy selector profiles keep their one-candidate-per-body-row behavior. A
  header-signature matrix profile can instead pivot one uniquely selected tier
  column into one candidate.
* Trusted static profiles may map exact normalized title, heading, or p/li text
  blocks to facts. A missing/drifted required assertion rejects the candidate;
  there are no unconditional profile constants or fuzzy matches.
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
from typing import Any

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
class HtmlMatrixRow:
    """Map one exact matrix row label to a candidate fact field."""

    field: str
    coercion: str = "text"
    required: bool = True


@dataclass(frozen=True)
class HtmlTextAssertion:
    """Map one exact normalized same-document text block to a fact.

    Assertions are profile-authored trusted mappings, never runtime regex or
    user-provided inference. ``scope`` is one of ``title``, ``heading`` (h1-h6),
    or ``document`` (p/li blocks). The whole normalized block must equal ``text``.
    """

    text: str
    field: str
    value: Any
    scope: str = "document"
    required: bool = True


@dataclass(frozen=True)
class HtmlExtractionProfile:
    """A declarative recipe for extracting offer rows from one document shape.

    The table is selected by ``table_id`` (exact ``id`` match) or ``table_class``
    (a class token), preserving the original row-mode behavior, or by an
    order-insensitive ``header_signature`` that must match exactly one table.
    ``mode="matrix"`` maps exact row labels from one tier column into one
    candidate. Trusted assertions use whole-block normalized equality only.
    """

    name: str
    table_id: str | None = None
    table_class: str | None = None
    columns: Mapping[str, HtmlColumn] = field(default_factory=dict)
    required_fields: tuple[str, ...] = ("service", "offer_type")
    header_signature: tuple[str, ...] = ()
    mode: str = "rows"
    matrix_metric_header: str | None = None
    matrix_tier_header: str | None = None
    matrix_rows: Mapping[str, HtmlMatrixRow] = field(default_factory=dict)
    ignored_matrix_rows: tuple[str, ...] = ()
    trusted_assertions: bool = False
    assertions: tuple[HtmlTextAssertion, ...] = ()

    def __post_init__(self) -> None:
        normalised = {normspace(label).lower(): col for label, col in self.columns.items()}
        object.__setattr__(self, "columns", normalised)
        signature = tuple(normspace(label).lower() for label in self.header_signature)
        object.__setattr__(self, "header_signature", signature)
        matrix_rows = {normspace(label).lower(): row for label, row in self.matrix_rows.items()}
        object.__setattr__(self, "matrix_rows", matrix_rows)
        object.__setattr__(
            self,
            "ignored_matrix_rows",
            tuple(normspace(label).lower() for label in self.ignored_matrix_rows),
        )
        assertions = tuple(
            HtmlTextAssertion(
                text=normspace(assertion.text),
                field=assertion.field,
                value=assertion.value,
                scope=normspace(assertion.scope).lower(),
                required=assertion.required,
            )
            for assertion in self.assertions
        )
        object.__setattr__(self, "assertions", assertions)

        selectors = sum(
            value is not None and value != ()
            for value in (self.table_id, self.table_class, self.header_signature)
        )
        if selectors > 1:
            raise ValueError("An HTML profile must use exactly one table selection strategy.")
        if self.mode not in {"rows", "matrix"}:
            raise ValueError("HTML profile mode must be 'rows' or 'matrix'.")
        if self.mode == "matrix":
            if not self.header_signature:
                raise ValueError("Matrix profiles require a header_signature.")
            if self.matrix_metric_header is None or self.matrix_tier_header is None:
                raise ValueError("Matrix profiles require metric and tier headers.")
            if not self.matrix_rows:
                raise ValueError("Matrix profiles require at least one mapped row.")
        if assertions and not self.trusted_assertions:
            raise ValueError("Text assertions require trusted_assertions=True.")
        bad_scopes = sorted({a.scope for a in assertions} - {"title", "heading", "document"})
        if bad_scopes:
            raise ValueError(f"Unsupported assertion scope(s): {bad_scopes}.")


#: Registry of extraction profiles keyed by name. This stands in for the
#: provider-config-supplied profiles a later slice will load from YAML; the point
#: is that provider-specific selectors live here as data, not in adapter code.
#:
#: Only the two *generic*, provider-agnostic shapes are defined here. Every
#: provider-specific profile lives in its own module under
#: :mod:`app.ingest.adapters.profiles` and registers itself through that
#: package's seam (F008 S3), so concurrent provider slices never edit this dict.
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


@dataclass(frozen=True)
class _HtmlRow:
    cells: tuple[str, ...]
    spans: tuple[tuple[int, int], ...]
    is_header: bool
    in_thead: bool


@dataclass(frozen=True)
class _HtmlTable:
    rows: tuple[_HtmlRow, ...]


@dataclass(frozen=True)
class _TextBlock:
    scope: str
    index: int
    text: str


class _DocumentCollector(HTMLParser):
    """Collect every outer table plus exact title/heading/body text blocks."""

    _HEADING_TAGS = frozenset({f"h{level}" for level in range(1, 7)})
    _DOCUMENT_TAGS = frozenset({"p", "li"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_HtmlTable] = []
        self.text_blocks: list[_TextBlock] = []
        self._scope_counts = {"title": 0, "heading": 0, "document": 0}
        self._text_capture: tuple[str, str, list[str]] | None = None
        self._table_depth = 0
        self._in_thead = False
        self._rows: list[_HtmlRow] = []
        self._in_row = False
        self._row_header = False
        self._cells: list[str] = []
        self._spans: list[tuple[int, int]] = []
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._cell_span = (1, 1)

    @staticmethod
    def _span(attrs: Mapping[str, str | None], name: str) -> int:
        raw = attrs.get(name)
        if raw is None:
            return 1
        try:
            value = int(raw)
        except ValueError:
            return 0
        return value if value > 0 else 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._text_capture = ("title", tag, [])
        elif tag in self._HEADING_TAGS:
            self._text_capture = ("heading", tag, [])
        elif tag in self._DOCUMENT_TAGS:
            self._text_capture = ("document", tag, [])

        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows = []
            return
        if self._table_depth != 1:
            return
        if tag == "br" and self._in_cell:
            self._cell_parts.append(" ")
            return
        if tag == "thead":
            self._in_thead = True
        elif tag == "tr":
            self._in_row = True
            self._row_header = False
            self._cells = []
            self._spans = []
        elif tag in {"td", "th"} and self._in_row:
            attr_map = dict(attrs)
            self._in_cell = True
            self._cell_parts = []
            self._cell_span = (
                self._span(attr_map, "rowspan"),
                self._span(attr_map, "colspan"),
            )
            if tag == "th":
                self._row_header = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._text_capture is not None and tag == self._text_capture[1]:
            scope, _, parts = self._text_capture
            text = normspace("".join(parts))
            if text:
                index = self._scope_counts[scope]
                self.text_blocks.append(_TextBlock(scope=scope, index=index, text=text))
                self._scope_counts[scope] += 1
            self._text_capture = None

        if tag == "table":
            if self._table_depth == 1:
                self.tables.append(_HtmlTable(rows=tuple(self._rows)))
                self._rows = []
                self._in_thead = False
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1:
            return
        if tag in {"td", "th"} and self._in_cell:
            self._cells.append(normspace("".join(self._cell_parts)))
            self._spans.append(self._cell_span)
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self._rows.append(
                _HtmlRow(
                    cells=tuple(self._cells),
                    spans=tuple(self._spans),
                    is_header=self._row_header,
                    in_thead=self._in_thead,
                )
            )
            self._in_row = False
        elif tag == "thead":
            self._in_thead = False

    def handle_data(self, data: str) -> None:
        if self._text_capture is not None:
            self._text_capture[2].append(data)
        if self._table_depth == 1 and self._in_cell:
            self._cell_parts.append(data)


def _normalised_header(row: _HtmlRow) -> tuple[str, ...]:
    return tuple(normspace(label).lower() for label in row.cells)


def _header_row(table: _HtmlTable) -> tuple[int, _HtmlRow] | tuple[None, str]:
    in_thead = [
        (index, row) for index, row in enumerate(table.rows) if row.in_thead and row.is_header
    ]
    candidates = in_thead or [(index, row) for index, row in enumerate(table.rows) if row.is_header]
    if len(candidates) != 1:
        return None, f"expected one header row, found {len(candidates)}"
    index, row = candidates[0]
    if len(row.cells) != len(row.spans) or any(span != (1, 1) for span in row.spans):
        return None, "header contains invalid rowspan/colspan"
    return index, row


def _select_table(
    tables: Sequence[_HtmlTable], signature: Sequence[str]
) -> tuple[_HtmlTable, int, _HtmlRow] | tuple[None, str, str]:
    required = frozenset(signature)
    matches: list[tuple[int, _HtmlTable, int, _HtmlRow]] = []
    diagnostics: list[str] = []
    for table_index, table in enumerate(tables):
        header_index, header_or_error = _header_row(table)
        if header_index is None:
            diagnostics.append(f"table[{table_index}] {header_or_error}")
            continue
        header = header_or_error
        labels = _normalised_header(header)
        diagnostics.append(f"table[{table_index}] headers={list(labels)}")
        if required.issubset(set(labels)):
            matches.append((table_index, table, header_index, header))
    if not matches:
        detail = f"required_headers={sorted(required)}; " + "; ".join(diagnostics)
        return None, "table_not_found", detail
    if len(matches) > 1:
        indices = [match[0] for match in matches]
        return None, "ambiguous_table", f"required_headers={sorted(required)}; matches={indices}"
    _, table, header_index, header = matches[0]
    return table, header_index, header


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

        document_collector = _DocumentCollector()
        try:
            document_collector.feed(document.canonical)
            document_collector.close()
        except Exception as exc:  # noqa: BLE001 - html.parser is lenient; never crash
            return [self._rejected(document, provider, "html_parse_error", str(exc))]

        if self._profile.header_signature:
            selection = _select_table(document_collector.tables, self._profile.header_signature)
            if selection[0] is None:
                _, error, detail = selection
                return [self._rejected(document, provider, error, detail)]
            table, header_idx, header = selection
            if self._profile.mode == "matrix":
                candidate = self._matrix_candidate(
                    document,
                    provider,
                    table,
                    header_idx,
                    header,
                )
                if candidate.verification_state == "rejected":
                    return [candidate]
                asserted = self._apply_assertions(
                    document, provider, candidate, document_collector.text_blocks
                )
                return [asserted]
            rows = [(list(row.cells), row.is_header) for row in table.rows]
        else:
            collector = _TableCollector(self._profile)
            try:
                collector.feed(document.canonical)
                collector.close()
            except Exception as exc:  # noqa: BLE001
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
            candidate = self._candidate(document, provider, cells, header_index, row_index)
            candidates.append(
                self._apply_assertions(
                    document, provider, candidate, document_collector.text_blocks
                )
            )
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

    def _matrix_candidate(
        self,
        document: SourceDocument,
        provider: str,
        table: _HtmlTable,
        header_index: int,
        header: _HtmlRow,
    ) -> CandidateFacts:
        labels = _normalised_header(header)
        metric_label = normspace(self._profile.matrix_metric_header or "").lower()
        tier_label = normspace(self._profile.matrix_tier_header or "").lower()
        metric_columns = [index for index, label in enumerate(labels) if label == metric_label]
        tier_columns = [index for index, label in enumerate(labels) if label == tier_label]
        if len(metric_columns) != 1:
            return self._rejected(
                document,
                provider,
                "invalid_metric_column",
                f"header={metric_label!r}; matches={metric_columns}",
            )
        if len(tier_columns) != 1:
            return self._rejected(
                document,
                provider,
                "invalid_tier_column",
                f"header={tier_label!r}; matches={tier_columns}",
            )

        metric_index = metric_columns[0]
        tier_index = tier_columns[0]
        row_width = len(header.cells)
        found: dict[str, list[tuple[int, str, str]]] = {}
        ignored = frozenset(self._profile.ignored_matrix_rows)
        unknown: list[str] = []

        for row_index in range(header_index + 1, len(table.rows)):
            row = table.rows[row_index]
            if not row.cells or not any(row.cells):
                continue
            if (
                len(row.cells) != row_width
                or len(row.spans) != row_width
                or any(span != (1, 1) for span in row.spans)
            ):
                return self._rejected(
                    document,
                    provider,
                    "irregular_row_width",
                    f"row={row_index}; expected={row_width}; actual={len(row.cells)}",
                )
            raw_label = row.cells[metric_index]
            label = normspace(raw_label).lower()
            if label in ignored:
                continue
            if label not in self._profile.matrix_rows:
                unknown.append(raw_label)
                continue
            found.setdefault(label, []).append((row_index, raw_label, row.cells[tier_index]))

        if unknown:
            return self._rejected(
                document,
                provider,
                "unknown_matrix_rows",
                f"rows={sorted(unknown)}",
            )

        duplicates = sorted(label for label, matches in found.items() if len(matches) > 1)
        if duplicates:
            return self._rejected(
                document,
                provider,
                "duplicate_matrix_rows",
                f"rows={duplicates}",
            )
        missing = sorted(
            label
            for label, row in self._profile.matrix_rows.items()
            if row.required and label not in found
        )
        if missing:
            return self._rejected(
                document,
                provider,
                "missing_matrix_rows",
                f"rows={missing}",
            )

        facts: dict[str, object] = {}
        evidence: list[EvidenceLocation] = []
        for label, row_spec in self._profile.matrix_rows.items():
            matches = found.get(label)
            if not matches:
                continue
            row_index, raw_label, raw_value = matches[0]
            value = _coerce(raw_value, row_spec.coercion)
            if row_spec.field in facts and facts[row_spec.field] != value:
                return self._rejected(
                    document,
                    provider,
                    "conflicting_matrix_values",
                    f"field={row_spec.field!r}",
                )
            facts[row_spec.field] = value
            evidence.append(
                EvidenceLocation(
                    url=document.url,
                    selector=(
                        f"{self._profile.name} matrix row[{row_index}:{raw_label}] "
                        f"column[{header.cells[tier_index]}] -> fact[{row_spec.field}]"
                    ),
                    excerpt=f"{raw_label} | {raw_value}"[:_EXCERPT_LIMIT] or None,
                    content_hash=document.content_hash,
                )
            )

        return CandidateFacts(
            provider=provider,
            source_url=document.url,
            facts=facts,
            evidence=tuple(evidence),
            verification_state="candidate",
        )

    def _apply_assertions(
        self,
        document: SourceDocument,
        provider: str,
        candidate: CandidateFacts,
        blocks: Sequence[_TextBlock],
    ) -> CandidateFacts:
        if not self._profile.assertions:
            return candidate

        facts = dict(candidate.facts)
        evidence = list(candidate.evidence)
        asserted_fields: dict[str, object] = {}
        for assertion_index, assertion in enumerate(self._profile.assertions):
            matches = [
                block
                for block in blocks
                if block.scope == assertion.scope and normspace(block.text) == assertion.text
            ]
            if not matches:
                if assertion.required:
                    return self._rejected(
                        document,
                        provider,
                        "assertion_not_found",
                        (
                            f"assertion={assertion_index}; scope={assertion.scope}; "
                            f"text={assertion.text!r}"
                        ),
                    )
                continue
            if len(matches) != 1:
                return self._rejected(
                    document,
                    provider,
                    "ambiguous_assertion",
                    f"assertion={assertion_index}; matches={len(matches)}",
                )
            existing = facts.get(assertion.field)
            if assertion.field in facts and existing != assertion.value:
                return self._rejected(
                    document,
                    provider,
                    "conflicting_assertion",
                    f"field={assertion.field!r}",
                )
            if (
                assertion.field in asserted_fields
                and asserted_fields[assertion.field] != assertion.value
            ):
                return self._rejected(
                    document,
                    provider,
                    "conflicting_assertion",
                    f"field={assertion.field!r}",
                )
            asserted_fields[assertion.field] = assertion.value
            facts[assertion.field] = assertion.value
            match = matches[0]
            evidence.append(
                EvidenceLocation(
                    url=document.url,
                    selector=(
                        f"{self._profile.name} assertion[{assertion_index}] "
                        f"{match.scope}[{match.index}] -> fact[{assertion.field}]"
                    ),
                    excerpt=match.text[:_EXCERPT_LIMIT],
                    content_hash=document.content_hash,
                )
            )

        return CandidateFacts(
            provider=candidate.provider,
            source_url=candidate.source_url,
            facts=facts,
            evidence=tuple(evidence),
            verification_state=candidate.verification_state,
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
    "HtmlMatrixRow",
    "HtmlTextAssertion",
    "HtmlExtractionProfile",
    "HtmlDocAdapter",
    "HTML_EXTRACTION_PROFILES",
    "resolve_profile",
    "UnknownProfileError",
)
