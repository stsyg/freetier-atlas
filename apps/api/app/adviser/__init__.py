"""Deterministic architecture adviser core (F006 slice 3).

The adviser turns a **strict, structured workload** (a list of requirements with
quantified demands and constraints) into a **guaranteed-$0 architecture** drawn
only from the *published* catalogue, or -- when no $0 architecture is possible --
an ordered, evidence-backed explanation of why, plus deterministic fallbacks.

The defining property is that a recommendation is a **pure, deterministic
function** of the requirements schema and the published PostgreSQL catalogue.
There is **no LLM anywhere in this package** and no network access: the module
therefore behaves identically whether or not any LLM provider is enabled (the
F006 corpus condition is "all providers disabled", which is the default). Every
fit/headroom decision is made with exact :class:`decimal.Decimal` arithmetic and
**fails closed** on anything it cannot confidently normalize -- "unknown is
better than guessed". Zero-cost safety is delegated to the shared classify
engine (:mod:`app.classify`); the adviser never re-derives a Z0 verdict.

Public surface:

* :mod:`~app.adviser.schema` -- the strict request model.
* :mod:`~app.adviser.recommend` -- the orchestrator (:func:`recommend`).
* :mod:`~app.adviser.router` -- the stateless ``POST /adviser/recommend`` route.
"""

from __future__ import annotations

__all__ = ["router"]


def __getattr__(name: str):  # pragma: no cover - thin lazy re-export
    # Lazy import keeps ``import app.adviser`` free of FastAPI/DB imports for the
    # pure-function unit tests, while still exposing the router for main.py.
    if name == "router":
        from .router import router

        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
