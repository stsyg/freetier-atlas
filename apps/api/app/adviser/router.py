"""The adviser HTTP routes.

* ``POST /adviser/recommend`` (F006 slice 3): the structured, deterministic
  recommendation. Unchanged.
* ``POST /adviser/recommend/assisted`` (F007 slice 1): an LLM-assisted
  natural-language front door that routes free text through the deterministic
  parser / (optional, consent-gated) LLM tiers / deterministic fallback, then
  runs the *same* deterministic core on any schema-valid interpretation.

Security / determinism posture:

* **Stateless.** Both handlers read the published catalogue through the
  read-only :func:`app.db.get_session` dependency (which never commits) and
  return a pure projection. Nothing is persisted, and neither the structured
  requirements nor the assisted free-text description is ever logged.
* **The recommendation is always deterministic.** The LLM (when enabled and
  consented) only proposes a candidate structured request; it is validated
  through the strict :class:`RecommendationRequest` and, only when valid, fed to
  the existing :func:`recommend`. Z0 / quota / classification are never
  re-derived in the LLM path, and there is no LLM-to-publication path.
* **No user-controlled URLs / no SSRF surface.** The structured request schema
  rejects URL/host/path-like input; the assisted endpoint additionally rejects a
  description that carries a URL when ``reject_urls`` is configured, and bounds
  the description to ``maximum_input_characters``.
* **Read-only.** Only ``POST`` is registered (``GET`` -> 405); the DB session
  issues ``SELECT`` only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from .assist_schema import (
    AssistedRecommendationResponse,
    AssistedRequest,
    ConsentEcho,
    RoutingInfo,
)
from .export import ExportResponse, ExportValidationError, build_export
from .llm.routing import route
from .llm.runtime import get_limits, get_registry
from .recommend import recommend
from .schema import RecommendationRequest
from .schemas import RecommendationResponse, build_response
from .select import gather_candidates

router = APIRouter(prefix="/adviser", tags=["adviser"])

SessionDep = Annotated[Session, Depends(get_session)]

#: Strong URL signals rejected from the assisted free-text description when the
#: configured ``reject_urls`` is on. Deliberately narrower than the structured
#: schema's marker set (which also rejects a bare ``/``) so ordinary natural
#: language ("CI/CD", "10 GB/month") is accepted while an actual URL is not; the
#: strict request schema remains the final gate on any parsed/proposed field.
_URL_SIGNALS: tuple[str, ...] = ("://", "http:", "https:", "www.")


def _looks_like_url(text: str) -> bool:
    lowered = text.lower()
    return any(signal in lowered for signal in _URL_SIGNALS)


@router.post("/recommend", response_model=RecommendationResponse)
def recommend_architecture(
    request: RecommendationRequest, session: SessionDep
) -> RecommendationResponse:
    """Return a deterministic, evidence-backed $0 architecture recommendation.

    The recommendation is computed entirely from the structured ``request`` and
    the published catalogue: no LLM, no persistence, no logging of the body.
    """

    pool = gather_candidates(session)
    result = recommend(request, pool)
    return build_response(result)


@router.post("/recommend/assisted", response_model=AssistedRecommendationResponse)
def recommend_assisted(
    request: AssistedRequest, session: SessionDep
) -> AssistedRecommendationResponse:
    """Interpret a free-text description, then run the deterministic adviser.

    Runs the routing ladder (deterministic parser -> consent-gated LLM tiers ->
    deterministic fallback). When a schema-valid interpretation is produced, the
    existing deterministic :func:`recommend` is run over the published catalogue
    and returned verbatim. When nothing can be interpreted, a graceful
    ``interpreted=false`` response is returned with a ``fallback_reason`` -- the
    request never hard-fails. The description is never logged or persisted.
    """

    limits = get_limits()
    description = request.description

    if len(description) > limits.maximum_input_characters:
        raise HTTPException(
            status_code=422,
            detail=(
                f"description exceeds the maximum of {limits.maximum_input_characters} characters"
            ),
        )
    if limits.reject_urls and _looks_like_url(description):
        raise HTTPException(
            status_code=422,
            detail="URLs are not accepted; describe your project in plain words",
        )

    consent_requested = bool(request.consent and request.consent.external_processing)
    outcome = route(
        description,
        limits,
        get_registry(),
        external_processing_consented=consent_requested,
    )

    recommendation: RecommendationResponse | None = None
    if outcome.interpretation is not None:
        pool = gather_candidates(session)
        result = recommend(outcome.interpretation, pool)
        recommendation = build_response(result)
        notice = (
            "Interpreted your description into structured requirements and ran the "
            "deterministic adviser. Review the interpretation below and switch to the "
            "structured form to adjust anything."
        )
    else:
        notice = (
            "Couldn't confidently interpret your description. Nothing was guessed. "
            "Please use the structured form to enter your requirements."
        )

    return AssistedRecommendationResponse(
        interpreted=outcome.interpreted,
        interpretation=outcome.interpretation,
        recommendation=recommendation,
        routing=RoutingInfo(
            llm_used=outcome.llm_used,
            llm_provider=outcome.provider,
            tier=outcome.tier,
            routing_path=list(outcome.routing_path),
            fallback_reason=outcome.fallback_reason,
        ),
        consent=ConsentEcho(
            external_processing_requested=consent_requested,
            external_processing_used=outcome.external_processing_used,
        ),
        notice=notice,
    )


@router.post("/export", response_model=ExportResponse)
def export_deployment(request: RecommendationRequest, session: SessionDep) -> ExportResponse:
    """Return the validated, secret-free deployment bundle for a recommendation.

    Recomputes the deterministic recommendation from the structured ``request``
    and the published catalogue, then generates a deployment bundle (Compose,
    ``.env.example``, README, manifest) whose **contents** are returned as JSON.

    Security posture: the endpoint is stateless and read-only -- it writes
    **nothing** to disk or the database (the bundle is produced in-memory and the
    browser assembles the ``.zip`` client-side). Every generated file is
    validated fail-closed (safe paths, text-only, secret-free, parseable Compose
    with healthchecks + multi-arch images, size cap); a validation failure is
    surfaced as HTTP 422 without echoing any file content.
    """

    pool = gather_candidates(session)
    result = recommend(request, pool)
    try:
        return build_export(result)
    except ExportValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Deployment export rejected: {exc}") from exc


__all__ = ["router"]
