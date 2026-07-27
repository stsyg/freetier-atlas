"""Internal admin CLI for the abuse layer (F007 slice 2).

Slice 4 will expose an authenticated admin surface; for this slice the AI kill
switch (and breaker reset) is togglable through this small, stdlib-only CLI so an
operator can flip it without a schema change or a code deploy. It writes through
the same :class:`PostgresAbuseStore` the API uses.

Usage (host-side, with ``DATABASE_URL`` pointing at the database)::

    python -m app.adviser.abuse.admin kill-switch on
    python -m app.adviser.abuse.admin kill-switch off
    python -m app.adviser.abuse.admin status
    python -m app.adviser.abuse.admin reset-breaker <provider>

No secret or prompt/description is ever printed.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from .service import AI_KILL_SWITCH_FLAG
from .store import AbuseStore, BreakerRecord, get_abuse_store


def set_kill_switch(store: AbuseStore, enabled: bool, now: datetime | None = None) -> None:
    """Persist the AI kill-switch flag."""

    store.set_flag(AI_KILL_SWITCH_FLAG, enabled, now or datetime.now(UTC))


def reset_breaker(store: AbuseStore, provider: str, now: datetime | None = None) -> None:
    """Force a provider's circuit breaker back to the closed state."""

    store.breaker_store(
        provider,
        BreakerRecord(state="closed", consecutive_failures=0, opened_at=None),
        now or datetime.now(UTC),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abuse-admin", description="Adviser abuse controls")
    sub = parser.add_subparsers(dest="command", required=True)

    ks = sub.add_parser("kill-switch", help="Toggle the AI kill switch")
    ks.add_argument("state", choices=["on", "off"])

    sub.add_parser("status", help="Print the current kill-switch state")

    rb = sub.add_parser("reset-breaker", help="Close a provider's circuit breaker")
    rb.add_argument("provider")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    store = get_abuse_store()

    if args.command == "kill-switch":
        enabled = args.state == "on"
        set_kill_switch(store, enabled)
        print(f"ai_kill_switch={'on' if enabled else 'off'}")
        return 0
    if args.command == "status":
        print(f"ai_kill_switch={'on' if store.get_flag(AI_KILL_SWITCH_FLAG) else 'off'}")
        return 0
    if args.command == "reset-breaker":
        reset_breaker(store, args.provider)
        print(f"circuit_breaker[{args.provider}]=closed")
        return 0
    return 1  # pragma: no cover - argparse enforces a valid subcommand


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())


__all__ = ["set_kill_switch", "reset_breaker", "main"]
