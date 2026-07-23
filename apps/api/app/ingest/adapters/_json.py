"""Shared, side-effect-free declarative JSON record extraction.

Both the structured-API adapter (:mod:`app.ingest.adapters.structured`) and the
MCP adapter (:mod:`app.ingest.adapters.mcp`) consume *structured JSON* -- a REST
response body or an MCP tool result. This module holds the one declarative
extractor they share so neither adapter hand-rolls JSON walking or coercion.

Design contract (docs/AGENT_HARNESS.md "unknown is better than guessed"):

* A :class:`JsonExtractionProfile` is pure data: which key path holds the list of
  offer records, which top-level key names the provider, and how each source key
  maps to a material fact field with a coercion. A new document shape is a new
  profile entry, not code.
* :func:`extract_records` **never raises**. A JSON decode error, or a records
  path whose target is not a list, is reported as a :class:`JsonExtractError`
  *return value* (not an exception) so the calling adapter can emit a single
  handled ``rejected`` candidate.
* A missing key or an ambiguous value yields ``None`` (UNKNOWN) -- never a
  guessed value. ``list`` fields yield an order-stable tuple, empty when absent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.ingest.adapters._common import normspace, to_bool

_LIST_SEPARATORS = (",", ";")

# Maximum JSON container-nesting depth accepted before parsing. Chosen well below
# any platform's effective ``json`` recursion limit (CPython trips RecursionError
# somewhere in the low thousands, and that threshold is interpreter- and
# build-dependent, i.e. NOT portable) and far above any legitimate offer document
# (which nests only a few levels). Enforcing this cap on the raw text BEFORE
# ``json.loads`` makes "input nesting too deep" resolve to a rejected candidate
# deterministically on every platform, instead of depending on whether the
# decoder happens to raise RecursionError.
MAX_JSON_NESTING_DEPTH = 100


def _exceeds_max_depth(text: str, cap: int = MAX_JSON_NESTING_DEPTH) -> bool:
    """Return ``True`` if ``text``'s JSON container nesting exceeds ``cap``.

    A single allocation-free pass over the string counts ``{``/``[`` against
    ``}``/``]`` while skipping brackets inside string literals (honouring
    backslash escapes). It returns as soon as the cap is exceeded, so a hostile
    deeply-nested body is rejected in O(cap) work rather than parsed.
    """

    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[" or ch == "{":
            depth += 1
            if depth > cap:
                return True
        elif ch == "]" or ch == "}":
            depth -= 1
    return False


@dataclass(frozen=True)
class JsonField:
    """How one source record key maps to a candidate fact field.

    ``coercion`` is one of ``"text"`` (stringify + whitespace-normalise, else
    ``None``), ``"bool"`` (native bool or yes/no string -> bool, else ``None``)
    or ``"list"`` (a JSON array, or a delimited string, -> a sorted tuple of
    strings; empty tuple when absent).
    """

    field: str
    source_key: str
    coercion: str = "text"


@dataclass(frozen=True)
class JsonExtractionProfile:
    """A declarative recipe for extracting offer records from one JSON shape.

    ``records_path`` is the sequence of mapping keys to descend to reach the list
    of records (empty means the document root is itself the list).
    ``provider_key`` names the top-level key holding the provider name.
    ``fields`` maps source keys to :class:`JsonField`. Nothing here is code.
    """

    name: str
    records_path: tuple[str, ...] = ()
    provider_key: str | None = None
    fields: tuple[JsonField, ...] = ()
    required_fields: tuple[str, ...] = ("service", "offer_type")


@dataclass(frozen=True)
class ExtractedRecord:
    """One extracted record: its coerced facts plus a short provenance excerpt."""

    facts: dict[str, Any]
    excerpt: str


@dataclass(frozen=True)
class JsonExtractError:
    """A handled extraction failure (returned, never raised)."""

    error: str
    detail: str = ""


@dataclass(frozen=True)
class JsonExtraction:
    """The result of :func:`extract_records`: a provider plus records or an error."""

    provider: str | None = None
    records: tuple[ExtractedRecord, ...] = ()
    error: JsonExtractError | None = field(default=None)


def parse_json(text: str) -> tuple[Any, JsonExtractError | None]:
    """Parse ``text`` as JSON, returning ``(value, None)`` or ``(None, error)``.

    Never raises. A malformed body becomes a returned :class:`JsonExtractError`
    so the caller can emit a handled rejected candidate.

    Hostile deeply-nested input is rejected **deterministically**: before
    decoding, :func:`_exceeds_max_depth` scans the raw text and, if container
    nesting exceeds :data:`MAX_JSON_NESTING_DEPTH`, returns a ``malformed_json``
    error without calling :func:`json.loads`. This is portable -- it does not
    rely on the decoder raising :class:`RecursionError`, whose threshold is
    interpreter/platform-dependent. The ``RecursionError`` and broad catch-all
    arms below are kept purely as defense-in-depth so the "never raises" contract
    holds even for input under the cap on a platform with a very low limit.
    """

    if _exceeds_max_depth(text):
        return None, JsonExtractError("malformed_json", "input nesting too deep")

    try:
        return json.loads(text), None
    except RecursionError:
        # Do NOT call str(exc): we may still be near the recursion limit, so use
        # a fixed, allocation-free detail rather than risk re-raising.
        return None, JsonExtractError("malformed_json", "input nesting too deep")
    except (json.JSONDecodeError, ValueError) as exc:
        return None, JsonExtractError("malformed_json", str(exc))
    except Exception as exc:  # noqa: BLE001 - "never raises" safety net
        return None, JsonExtractError("malformed_json", type(exc).__name__)


def _descend(data: Any, path: Sequence[str]) -> tuple[Any, bool]:
    """Follow ``path`` through nested mappings; return ``(value, found)``."""

    current = data
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None, False
        current = current[key]
    return current, True


def _coerce(value: Any, coercion: str) -> Any:
    """Coerce a raw JSON value into a fact value without ever guessing."""

    if coercion == "list":
        return _coerce_list(value)
    if value is None:
        return None
    if coercion == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return to_bool(value)
        return None
    # "text" (default): stringify scalars, normalise whitespace; None if empty.
    if isinstance(value, (Mapping, list, tuple)):
        return None
    text = normspace(str(value))
    return text or None


def _coerce_list(value: Any) -> tuple[str, ...]:
    """Coerce a JSON array or delimited string into a sorted tuple of strings.

    An absent value yields an empty tuple -- "no entries found", not a guess.
    """

    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        items = [normspace(str(v)) for v in value if v is not None]
        return tuple(sorted({item for item in items if item}))
    if isinstance(value, str):
        parts = [value]
        for sep in _LIST_SEPARATORS:
            parts = [piece for chunk in parts for piece in chunk.split(sep)]
        cleaned = {normspace(piece) for piece in parts if normspace(piece)}
        return tuple(sorted(cleaned))
    return ()


def _excerpt(record: Any, limit: int) -> str:
    """A short, deterministic JSON excerpt of a record for provenance."""

    try:
        return json.dumps(record, sort_keys=True)[:limit]
    except (TypeError, ValueError):
        return str(record)[:limit]


def extract_records(
    text: str,
    profile: JsonExtractionProfile,
    *,
    excerpt_limit: int = 280,
) -> JsonExtraction:
    """Extract candidate records from a JSON ``text`` per ``profile``.

    Returns a :class:`JsonExtraction`. On a decode error, a non-mapping root, or a
    records path that is absent or not a list, the ``error`` field is populated
    and ``records`` is empty -- the function never raises.
    """

    data, parse_error = parse_json(text)
    if parse_error is not None:
        return JsonExtraction(error=parse_error)

    if not isinstance(data, Mapping) and profile.records_path:
        return JsonExtraction(error=JsonExtractError("unexpected_root", type(data).__name__))

    provider = None
    if profile.provider_key is not None and isinstance(data, Mapping):
        raw_provider = data.get(profile.provider_key)
        if raw_provider is not None:
            provider = normspace(str(raw_provider)) or None

    records_value, found = _descend(data, profile.records_path)
    if not found:
        return JsonExtraction(
            provider=provider,
            error=JsonExtractError("records_not_found", ".".join(profile.records_path) or "$"),
        )
    if not isinstance(records_value, list):
        return JsonExtraction(
            provider=provider,
            error=JsonExtractError("records_not_a_list", type(records_value).__name__),
        )

    records: list[ExtractedRecord] = []
    for record in records_value:
        mapping = record if isinstance(record, Mapping) else {}
        facts: dict[str, Any] = {}
        for spec in profile.fields:
            facts[spec.field] = _coerce(mapping.get(spec.source_key), spec.coercion)
        records.append(ExtractedRecord(facts=facts, excerpt=_excerpt(record, excerpt_limit)))

    return JsonExtraction(provider=provider, records=tuple(records))


__all__ = (
    "JsonField",
    "JsonExtractionProfile",
    "ExtractedRecord",
    "JsonExtractError",
    "JsonExtraction",
    "parse_json",
    "extract_records",
)
