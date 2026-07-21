"""Offline safety tests for the ingestion transport (F004 slice 1).

Every network-blocking, allowlist, scheme, MIME, timeout, size, and redirect
guard is exercised here. Pure policy functions run with no I/O; the timeout /
oversize / redirect mechanics run against a throwaway ``127.0.0.1`` loopback
server that is *explicitly* allowlisted for that test only -- loopback is not
external network access, and no real DNS or egress occurs.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.ingest import (
    BlockedAddressError,
    DisallowedContentTypeError,
    DisallowedHostError,
    DisallowedSchemeError,
    FetchPolicy,
    FetchTimeoutError,
    FixtureFetcher,
    FixtureResponse,
    NetworkDisabledError,
    OfflineFetcher,
    ResponseTooLargeError,
    SafeFetcher,
    default_fetcher,
)
from app.ingest.fetch import (
    host_matches_allowlist,
    is_blocked_address,
    screen_url,
    validate_content_type,
)

OFFICIAL = "official"


# --------------------------------------------------------------------------- #
# Loopback test server
# --------------------------------------------------------------------------- #
@contextmanager
def loopback_server(handler_factory: Callable[..., BaseHTTPRequestHandler]):
    """Start a throwaway HTTP server on 127.0.0.1 and yield its base URL."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _silence(_self, *_args, **_kwargs) -> None:
    """No-op log to keep test output clean (matches log_message signature)."""


def loopback_policy(**overrides) -> FetchPolicy:
    """A policy that permits the loopback server for a single mechanics test."""

    base = dict(
        allowlist=("127.0.0.1",),
        allowed_schemes=("http",),
        max_bytes=1000,
        timeout_seconds=0.4,
        max_redirects=5,
        allow_loopback=True,
    )
    base.update(overrides)
    return FetchPolicy(**base)


# --------------------------------------------------------------------------- #
# Pure policy: host allowlist
# --------------------------------------------------------------------------- #
def test_host_matches_allowlist_exact_and_subdomain():
    allowlist = ("cloudflare.com", "aws.amazon.com")
    assert host_matches_allowlist("cloudflare.com", allowlist)
    assert host_matches_allowlist("developers.cloudflare.com", allowlist)
    assert host_matches_allowlist("AWS.Amazon.com", allowlist)


def test_host_matches_allowlist_rejects_lookalikes():
    allowlist = ("cloudflare.com",)
    assert not host_matches_allowlist("notcloudflare.com", allowlist)
    assert not host_matches_allowlist("cloudflare.com.evil.test", allowlist)
    assert not host_matches_allowlist("", allowlist)


# --------------------------------------------------------------------------- #
# Pure policy: SSRF / private-address blocking
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "169.254.0.1",  # link-local
        "::1",  # ipv6 loopback
        "fd00::1",  # ULA
        "fe80::1",  # ipv6 link-local
        "::ffff:10.0.0.1",  # ipv4-mapped private
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "not-an-ip",  # unresolvable literal
    ],
)
def test_is_blocked_address_rejects_unsafe(ip):
    assert is_blocked_address(ip, FetchPolicy(allowlist=()))


@pytest.mark.parametrize("ip", ["1.1.1.1", "104.16.0.1", "2606:4700::1111"])
def test_is_blocked_address_allows_public(ip):
    assert not is_blocked_address(ip, FetchPolicy(allowlist=()))


def test_is_blocked_address_metadata_blocked_even_with_allow_private():
    # The metadata address must stay blocked regardless of the loopback/private
    # escape hatches used only by loopback mechanics tests.
    policy = FetchPolicy(allowlist=(), allow_loopback=True, allow_private=True)
    assert is_blocked_address("169.254.169.254", policy)


# --------------------------------------------------------------------------- #
# Pure policy: scheme + MIME
# --------------------------------------------------------------------------- #
def test_screen_url_rejects_non_https():
    policy = FetchPolicy(allowlist=("cloudflare.com",))
    with pytest.raises(DisallowedSchemeError):
        screen_url("http://cloudflare.com/docs", policy)


def test_screen_url_rejects_non_allowlisted_host():
    policy = FetchPolicy(allowlist=("cloudflare.com",))
    with pytest.raises(DisallowedHostError):
        screen_url("https://evil.example.com/", policy)


def test_validate_content_type_strips_params_and_enforces_allowlist():
    allowed = frozenset({"application/json"})
    assert validate_content_type("application/json; charset=utf-8", allowed) == "application/json"
    with pytest.raises(DisallowedContentTypeError):
        validate_content_type("text/csv", allowed)
    with pytest.raises(DisallowedContentTypeError):
        validate_content_type(None, allowed)


# --------------------------------------------------------------------------- #
# SafeFetcher: pre-connect rejection, resolver not called
# --------------------------------------------------------------------------- #
def test_non_allowlisted_host_rejected_before_connect():
    calls: list[str] = []

    def spy_resolver(host: str) -> list[str]:
        calls.append(host)
        return ["1.1.1.1"]

    fetcher = SafeFetcher(
        FetchPolicy(allowlist=("cloudflare.com",)),
        enable_network=True,
        resolver=spy_resolver,
    )
    with pytest.raises(DisallowedHostError):
        fetcher.fetch("https://evil.example.com/data.json")
    assert calls == []  # never resolved -> never any chance to connect


def test_non_https_rejected_before_resolve():
    calls: list[str] = []
    fetcher = SafeFetcher(
        FetchPolicy(allowlist=("cloudflare.com",)),
        enable_network=True,
        resolver=lambda h: calls.append(h) or ["1.1.1.1"],
    )
    with pytest.raises(DisallowedSchemeError):
        fetcher.fetch("http://cloudflare.com/data.json")
    assert calls == []


def test_dns_rebinding_to_private_address_blocked():
    # Host is allowlisted but resolves to a private address: reject before connect.
    fetcher = SafeFetcher(
        FetchPolicy(allowlist=("api.cloudflare.com",)),
        enable_network=True,
        resolver=lambda h: ["10.0.0.7"],
    )
    with pytest.raises(BlockedAddressError):
        fetcher.fetch("https://api.cloudflare.com/data.json")


# --------------------------------------------------------------------------- #
# SafeFetcher: redirect re-screening mid-chain
# --------------------------------------------------------------------------- #
def _redirect_handler(location: str):
    class Handler(BaseHTTPRequestHandler):
        log_message = _silence  # type: ignore[assignment]

        def do_GET(self):  # noqa: N802 - http.server API
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

    return Handler


def test_redirect_to_disallowed_host_rejected_mid_chain():
    with loopback_server(_redirect_handler("http://evil.example.com/")) as base:
        fetcher = SafeFetcher(
            loopback_policy(),
            enable_network=True,
            resolver=lambda h: ["127.0.0.1"],
        )
        with pytest.raises(DisallowedHostError):
            fetcher.fetch(f"{base}/start")


def test_redirect_to_private_address_rejected_mid_chain():
    # Redirect target host is allowlisted but resolves to a private address.
    with loopback_server(_redirect_handler("http://internal.test/secret")) as base:
        policy = loopback_policy(allowlist=("127.0.0.1", "internal.test"))
        resolved = {"127.0.0.1": ["127.0.0.1"], "internal.test": ["10.0.0.9"]}
        fetcher = SafeFetcher(
            policy,
            enable_network=True,
            resolver=lambda h: resolved[h],
        )
        with pytest.raises(BlockedAddressError):
            fetcher.fetch(f"{base}/start")


# --------------------------------------------------------------------------- #
# SafeFetcher: timeout, oversize, MIME against loopback
# --------------------------------------------------------------------------- #
def test_timeout_aborts_within_budget():
    class SlowHandler(BaseHTTPRequestHandler):
        log_message = _silence  # type: ignore[assignment]

        def do_GET(self):  # noqa: N802
            time.sleep(5.0)  # far longer than the 0.4s policy timeout

    with loopback_server(SlowHandler) as base:
        fetcher = SafeFetcher(
            loopback_policy(timeout_seconds=0.4),
            enable_network=True,
            resolver=lambda h: ["127.0.0.1"],
        )
        started = time.monotonic()
        with pytest.raises(FetchTimeoutError):
            fetcher.fetch(f"{base}/slow")
        elapsed = time.monotonic() - started
        assert elapsed < 3.0  # aborted well before the 5s server sleep


def test_oversize_response_aborted():
    big = b"x" * 100_000

    class BigHandler(BaseHTTPRequestHandler):
        log_message = _silence  # type: ignore[assignment]

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(big)))
            self.end_headers()
            self.wfile.write(big)

    with loopback_server(BigHandler) as base:
        fetcher = SafeFetcher(
            loopback_policy(max_bytes=1000),
            enable_network=True,
            resolver=lambda h: ["127.0.0.1"],
        )
        with pytest.raises(ResponseTooLargeError):
            fetcher.fetch(f"{base}/big")


def test_disallowed_mime_rejected():
    class CsvHandler(BaseHTTPRequestHandler):
        log_message = _silence  # type: ignore[assignment]

        def do_GET(self):  # noqa: N802
            body = b"a,b,c"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with loopback_server(CsvHandler) as base:
        fetcher = SafeFetcher(
            loopback_policy(),
            enable_network=True,
            resolver=lambda h: ["127.0.0.1"],
        )
        with pytest.raises(DisallowedContentTypeError):
            fetcher.fetch(f"{base}/data.csv")


def test_successful_loopback_fetch_hashes_body():
    body = b'{"ok": true}'

    class JsonHandler(BaseHTTPRequestHandler):
        log_message = _silence  # type: ignore[assignment]

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with loopback_server(JsonHandler) as base:
        fetcher = SafeFetcher(
            loopback_policy(max_bytes=1000),
            enable_network=True,
            resolver=lambda h: ["127.0.0.1"],
        )
        result = fetcher.fetch(f"{base}/data.json")
    import hashlib

    assert result.status == 200
    assert result.mime == "application/json"
    assert result.body == body
    assert result.content_hash == hashlib.sha256(body).hexdigest()


# --------------------------------------------------------------------------- #
# Default / offline fetchers open no socket
# --------------------------------------------------------------------------- #
def test_default_fetcher_is_offline():
    assert isinstance(default_fetcher(), OfflineFetcher)


def test_offline_fetcher_opens_no_socket(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("no socket may be opened by the offline fetcher")

    monkeypatch.setattr(socket, "socket", explode)
    with pytest.raises(NetworkDisabledError):
        OfflineFetcher().fetch("https://cloudflare.com/data.json")


def test_safe_fetcher_disabled_by_default_opens_no_socket(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("no socket may be opened when network is disabled")

    monkeypatch.setattr(socket, "socket", explode)
    fetcher = SafeFetcher(FetchPolicy(allowlist=("cloudflare.com",)))
    with pytest.raises(NetworkDisabledError):
        fetcher.fetch("https://cloudflare.com/data.json")


# --------------------------------------------------------------------------- #
# FixtureFetcher still screens, never touches the network
# --------------------------------------------------------------------------- #
def test_fixture_fetcher_screens_host():
    fetcher = FixtureFetcher(
        {"https://cloudflare.com/a.json": b"{}"},
        FetchPolicy(allowlist=("cloudflare.com",)),
    )
    with pytest.raises(DisallowedHostError):
        fetcher.fetch("https://evil.example.com/a.json")


def test_fixture_fetcher_returns_hashed_result():
    body = b'{"x": 1}'
    fetcher = FixtureFetcher(
        {"https://cloudflare.com/a.json": FixtureResponse(body=body)},
        FetchPolicy(allowlist=("cloudflare.com",)),
    )
    result = fetcher.fetch("https://cloudflare.com/a.json")
    import hashlib

    assert result.body == body
    assert result.mime == "application/json"
    assert result.content_hash == hashlib.sha256(body).hexdigest()


def test_fixture_fetcher_enforces_size_cap():
    fetcher = FixtureFetcher(
        {"https://cloudflare.com/a.json": b"x" * 50},
        FetchPolicy(allowlist=("cloudflare.com",), max_bytes=10),
    )
    with pytest.raises(ResponseTooLargeError):
        fetcher.fetch("https://cloudflare.com/a.json")
