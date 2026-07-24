"""Request/response models for the LLM-assisted intake endpoint (F007 s1).

``POST /adviser/recommend/assisted`` accepts a free-text project description
plus an optional *ephemeral* consent assertion, runs the routing ladder
(:func:`app.adviser.llm.routing.route`), and -- when a schema-valid
interpretation is produced -- returns the existing deterministic recommendation
alongside routing metadata and the echoed interpretation.

Privacy posture (``docs/SECURITY_PRIVACY_ABUSE.md``): the ``description`` is a
transient prompt -- it is never persisted and never logged. ``consent`` is a
per-request assertion; it is not stored server-side and is re-asked each
session. The response never re-derives Z0 / quota / classification: the
``interpretation`` is the validated structured request and ``recommendation`` is
the verbatim deterministic result.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .schema import RecommendationRequest
from .schemas import RecommendationResponse

#: A hard upper bound on the accepted description length. This is a coarse
#: safety cap; the *effective* limit is the configured
#: ``public_adviser.maximum_input_characters`` enforced in the route handler.
MAX_DESCRIPTION_CHARS = 20_000


class ConsentAssertion(BaseModel):
    """An explicit, per-request consent to external LLM processing.

    Ephemeral: not persisted, not logged, and re-asked each session. When absent
    (or ``external_processing=False``) the router skips every external provider
    and takes the local/deterministic path.
    """

    model_config = ConfigDict(extra="forbid")

    external_processing: bool = False


class AssistedRequest(BaseModel):
    """The request body for the LLM-assisted intake endpoint.

    Carries a free-text ``description`` (a plain natural-language project
    description -- never a URL) and an optional ``consent`` assertion. Unknown
    fields are rejected (``extra="forbid"``).
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_CHARS)
    consent: ConsentAssertion | None = None


class RoutingInfo(BaseModel):
    """How the request was routed (interpreter provenance, not a Z0 decision)."""

    model_config = ConfigDict(extra="forbid")

    llm_used: bool
    llm_provider: str | None = None
    tier: str
    routing_path: list[str]
    fallback_reason: str | None = None


class ConsentEcho(BaseModel):
    """Echo of the ephemeral consent decision for this request only."""

    model_config = ConfigDict(extra="forbid")

    external_processing_requested: bool
    external_processing_used: bool


class AssistedRecommendationResponse(BaseModel):
    """The assisted-intake response: interpretation + deterministic result.

    ``interpreted`` is ``True`` when the ladder produced a schema-valid
    ``interpretation`` (from the deterministic parser or an LLM); in that case
    ``recommendation`` is the verbatim deterministic recommendation for that
    interpretation. When nothing could be interpreted, ``interpreted`` is
    ``False``, ``interpretation``/``recommendation`` are ``null``, and
    ``routing.fallback_reason`` explains why -- the request never hard-fails.
    """

    model_config = ConfigDict(extra="forbid")

    interpreted: bool
    interpretation: RecommendationRequest | None = None
    recommendation: RecommendationResponse | None = None
    routing: RoutingInfo
    consent: ConsentEcho
    notice: str


__all__: Sequence[str] = (
    "MAX_DESCRIPTION_CHARS",
    "ConsentAssertion",
    "AssistedRequest",
    "RoutingInfo",
    "ConsentEcho",
    "AssistedRecommendationResponse",
)
