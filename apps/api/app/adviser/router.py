"""The stateless ``POST /adviser/recommend`` route (F006 slice 3).

Security / determinism posture:

* **Stateless.** The handler reads the published catalogue through the read-only
  :func:`app.db.get_session` dependency (which never commits) and returns a pure
  projection. Nothing is persisted, and the request body -- which is structured
  requirements, never a free-text project description -- is not logged.
* **No LLM, no network.** The recommendation is a pure function of the request
  and the catalogue; the module imports nothing from any LLM/provider client.
* **No user-controlled URLs / no SSRF surface.** The request schema rejects
  URL/host/path-like input, so no field can be coerced into a fetchable location.
* **Read-only.** Only ``POST`` is registered for the recommendation (there is no
  write/mutation route); the DB session issues ``SELECT`` only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from .recommend import recommend
from .schema import RecommendationRequest
from .schemas import RecommendationResponse, build_response
from .select import gather_candidates

router = APIRouter(prefix="/adviser", tags=["adviser"])

SessionDep = Annotated[Session, Depends(get_session)]


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


__all__ = ["router"]
