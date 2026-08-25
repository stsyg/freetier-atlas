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

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..db import get_session
from .abuse import (
    client_ip_hash,
    enforce_deterministic,
    evaluate_assisted,
    load_abuse_config,
)
from .abuse.breaker import wrap_registry
from .abuse.pow import issue_challenge
from .abuse.service import SCOPE_DETERMINISTIC
from .abuse.store import get_abuse_store
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

#: Headers a client uses to submit a solved proof-of-work challenge.
_POW_TOKEN_HEADER = "x-pow-token"
_POW_NONCE_HEADER = "x-pow-nonce"

#: Strong URL signals rejected from the assisted free-text description when the
#: configured ``reject_urls`` is on. Deliberately narrower than the structured
#: schema's marker set (which also rejects a bare ``/``) so ordinary natural
#: language ("CI/CD", "10 GB/month") is accepted while an actual URL is not; the
#: strict request schema remains the final gate on any parsed/proposed field.
_URL_SIGNALS: tuple[str, ...] = ("://", "http:", "https:", "www.")


def _now() -> datetime:
    return datetime.now(UTC)


def _looks_like_url(text: str) -> bool:
    lowered = text.lower()
    return any(signal in lowered for signal in _URL_SIGNALS)


def _enforce_rate_limit(request: Request, body: RecommendationRequest, scope: str) -> None:
    """Apply per-IP rate limiting to a deterministic endpoint (429 on overage)."""

    config = load_abuse_config()
    limits = get_limits()
    decision = enforce_deterministic(
        get_abuse_store(),
        config,
        request,
        body,
        scope,
        limits.deterministic_requests_per_ip_per_day,
        _now(),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded; retry later.",
            headers={"Retry-After": str(decision.retry_after)},
        )


@router.post("/recommend", response_model=RecommendationResponse)
def recommend_architecture(
    request: RecommendationRequest, http_request: Request, session: SessionDep
) -> RecommendationResponse:
    """Return a deterministic, evidence-backed $0 architecture recommendation.

    The recommendation is computed entirely from the structured ``request`` and
    the published catalogue: no LLM, no persistence, no logging of the body. A
    per-IP rate limit protects the endpoint; an overage returns HTTP 429 with a
    ``Retry-After`` header rather than degrading (this is the deterministic path).
    """

    _enforce_rate_limit(http_request, request, SCOPE_DETERMINISTIC)
    pool = gather_candidates(session, now=_now())
    result = recommend(request, pool)
    return build_response(result)


@router.post("/recommend/assisted", response_model=AssistedRecommendationResponse)
def recommend_assisted(
    request: AssistedRequest, http_request: Request, session: SessionDep
) -> AssistedRecommendationResponse:
    """Interpret a free-text description, then run the deterministic adviser.

    Runs the routing ladder (deterministic parser -> consent-gated LLM tiers ->
    deterministic fallback). The abuse layer gates the *AI* path only: the AI
    kill switch, an exhausted AI quota, an open provider circuit, a duplicate
    request, or a required-but-missing proof-of-work all cause a graceful
    **degrade to the deterministic fallback** (HTTP 200, ``llm_used=false``, with
    a clear ``fallback_reason``) -- the request never hard-fails for those. Only
    an absolute anti-hammering ceiling returns 429. The description is never
    logged or persisted.
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

    config = load_abuse_config()
    store = get_abuse_store()
    registry = get_registry()
    consent_requested = bool(request.consent and request.consent.external_processing)

    decision = evaluate_assisted(
        store,
        config,
        limits,
        http_request,
        request,
        had_providers=bool(registry),
        pow_token=http_request.headers.get(_POW_TOKEN_HEADER),
        pow_nonce=http_request.headers.get(_POW_NONCE_HEADER),
        now=_now(),
    )
    if decision.rate_limited:
        raise HTTPException(
            status_code=429,
            detail="Assisted request ceiling exceeded; retry later.",
            headers={"Retry-After": str(decision.retry_after)},
        )

    if decision.allow_ai:
        active_registry = wrap_registry(registry, store, config)
    else:
        # AI is gated (or absent): route deterministically with no provider so
        # no LLM call is made, then surface the abuse reason when nothing was
        # interpreted by the deterministic parser.
        active_registry = ()

    outcome = route(
        description,
        limits,
        active_registry,
        external_processing_consented=consent_requested,
    )

    fallback_reason = outcome.fallback_reason
    if (
        decision.forced_reason is not None
        and bool(registry)
        and outcome.interpretation is None
        and not outcome.llm_used
    ):
        fallback_reason = decision.forced_reason

    recommendation: RecommendationResponse | None = None
    if outcome.interpretation is not None:
        pool = gather_candidates(session, now=_now())
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
            fallback_reason=fallback_reason,
        ),
        consent=ConsentEcho(
            external_processing_requested=consent_requested,
            external_processing_used=outcome.external_processing_used,
        ),
        notice=notice,
    )


@router.post("/challenge")
def issue_pow_challenge(http_request: Request, response: Response) -> dict[str, object]:
    """Issue a self-hosted proof-of-work challenge for the assisted endpoint.

    The client solves the returned challenge (find a ``nonce`` whose
    ``sha256(f"{token}:{nonce}")`` has ``difficulty`` leading hex zeros) and
    submits ``token`` + ``nonce`` via the ``X-PoW-Token`` / ``X-PoW-Nonce``
    headers on a subsequent assisted request that is beyond the free AI
    threshold. The challenge is server-signed (stdlib HMAC), single-use, and
    expires; no external CAPTCHA service is involved.
    """

    config = load_abuse_config()
    store = get_abuse_store()
    ip_hash = client_ip_hash(config, http_request)
    issued = issue_challenge(store, config, ip_hash, _now())
    response.headers["Cache-Control"] = "no-store"
    return {
        "challenge_id": issued.challenge_id,
        "token": issued.token,
        "difficulty": issued.difficulty,
        "algorithm": issued.algorithm,
        "expires_at": issued.expires_at.isoformat(),
        "instructions": (
            "Find a nonce N such that sha256(f'{token}:{N}') begins with "
            f"{issued.difficulty} hex zero(s); submit token + nonce via the "
            "X-PoW-Token and X-PoW-Nonce headers."
        ),
    }


@router.post("/export", response_model=ExportResponse)
def export_deployment(
    request: RecommendationRequest, http_request: Request, session: SessionDep
) -> ExportResponse:
    """Return the validated, secret-free deployment bundle for a recommendation.

    Recomputes the deterministic recommendation from the structured ``request``
    and the published catalogue, then generates a deployment bundle (Compose,
    ``.env.example``, README, manifest) whose **contents** are returned as JSON.

    Security posture: the endpoint is stateless and read-only -- it writes
    **nothing** to disk or the database (the bundle is produced in-memory and the
    browser assembles the ``.zip`` client-side). It shares the deterministic
    per-IP rate limit (429 + ``Retry-After`` on overage). Every generated file is
    validated fail-closed (safe paths, text-only, secret-free, parseable Compose
    with healthchecks + multi-arch images, size cap); a validation failure is
    surfaced as HTTP 422 without echoing any file content.
    """

    _enforce_rate_limit(http_request, request, SCOPE_DETERMINISTIC)
    pool = gather_candidates(session, now=_now())
    result = recommend(request, pool)
    try:
        return build_export(result)
    except ExportValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Deployment export rejected: {exc}") from exc


__all__ = ["router"]
