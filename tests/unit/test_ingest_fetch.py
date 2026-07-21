"""Safe-fetch guard tests (offline).

The pure policy functions are tested directly with plain values. The live
transport is exercised only against a loopback (127.0.0.1) HTTP server that is
explicitly allowlisted for these tests; nothing here performs external network
egress. Numeric IP literals (127.0.0.1, 10.0.0.1) resolve without DNS.
"""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.ingest.fetch import (
    BlockedAddressError,
    DisallowedHostError,
    DisallowedMimeError,
    DisallowedSchemeError,
    FetchPolicy,
    FetchTimeoutError,
    FixtureFetcher,
    LiveFetcher,
    NetworkDisabledError,
    OfflineFetcher,
    ResponseTooLargeError,
    TooManyRedirectsError,
    address_block_reason,
    check_redirect_budget,
    check_scheme,
    check_size,
    content_hash,
    host_is_allowlisted,
    validate_mime,
)

# --------------------------------------------------------------------------
# Pure policy functions
# --------------------------------------------------------------------------


def test_scheme_allowlist_rejects_http_by_default() -> None:
    assert check_scheme("https://x.example/", {"https"}) == "https"
    with pytest.raises(DisallowedSchemeError):
        check_scheme("http://x.example/", {"https"})
    with pytest.raises(DisallowedSchemeError):
        check_scheme("ftp://x.example/", {"https"})
    with pytest.raises(DisallowedSchemeError):
        check_scheme("file:///etc/passwd", {"https"})


@pytest.mark.parametrize(
    ("host", "allowed", "expected"),
    [
        ("example.com", ("example.com",), True),
        ("api.example.com", ("example.com",), True),
        ("EXAMPLE.COM", ("example.com",), True),
        ("example.com.", ("example.com",), True),
        ("notexample.com", ("example.com",), False),
        ("evil.com", ("example.com",), False),
        ("example.com.evil.com", ("example.com",), False),
        ("", ("example.com",), False),
    ],
)
def test_host_allowlist_subdomain_matching(host, allowed, expected) -> None:
    assert host_is_allowlisted(host, allowed) is expected


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "::1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "169.254.169.254",  # cloud metadata
        "fe80::1",
        "fc00::1",
        "0.0.0.0",
        "::",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "224.0.0.1",  # multicast
    ],
)
def test_address_classifier_blocks_unsafe_ranges(ip) -> None:
    assert address_block_reason(ip) is not None


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1"])
def test_address_classifier_allows_public_addresses(ip) -> None:
    assert address_block_reason(ip) is None


def test_address_classifier_loopback_optin() -> None:
    assert address_block_reason("127.0.0.1", allow_loopback=True) is None
    # The loopback escape hatch does NOT cover private ranges.
    assert address_block_reason("10.0.0.1", allow_loopback=True) is not None


def test_address_classifier_rejects_garbage() -> None:
    assert address_block_reason("not-an-ip") is not None


def test_mime_validation() -> None:
    assert validate_mime("application/json; charset=utf-8", ("application/json",)) == (
        "application/json"
    )
    with pytest.raises(DisallowedMimeError):
        validate_mime("application/octet-stream", ("application/json",))
    with pytest.raises(DisallowedMimeError):
        validate_mime(None, ("application/json",))


def test_redirect_budget_and_size_caps() -> None:
    check_redirect_budget(5, 5)  # exactly at budget: ok
    with pytest.raises(TooManyRedirectsError):
        check_redirect_budget(6, 5)
    check_size(1000, 1000)  # exactly at cap: ok
    with pytest.raises(ResponseTooLargeError):
        check_size(1001, 1000)


def test_content_hash_is_sha256_hex() -> None:
    import hashlib

    payload = b"free-tier atlas"
    assert content_hash(payload) == hashlib.sha256(payload).hexdigest()
    assert len(content_hash(b"")) == 64


# --------------------------------------------------------------------------
# OfflineFetcher / FixtureFetcher (never open a socket)
# --------------------------------------------------------------------------


def _forbid_sockets(monkeypatch) -> None:
    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)


def test_offline_fetcher_opens_no_socket(monkeypatch) -> None:
    _forbid_sockets(monkeypatch)
    fetcher = OfflineFetcher()
    with pytest.raises(NetworkDisabledError):
        fetcher.fetch("https://example.com/")


def test_fixture_fetcher_is_offline_and_deterministic(monkeypatch) -> None:
    _forbid_sockets(monkeypatch)
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher(
        {"https://example.com/data.json": (b'{"a":1}', "application/json")}, policy
    )
    first = fetcher.fetch("https://example.com/data.json")
    second = fetcher.fetch("https://example.com/data.json")
    assert first.mime == "application/json"
    assert first.content == b'{"a":1}'
    assert first.content_hash == second.content_hash


def test_fixture_fetcher_enforces_policy(monkeypatch) -> None:
    _forbid_sockets(monkeypatch)
    policy = FetchPolicy(official_domains=("example.com",))
    fetcher = FixtureFetcher({"http://example.com/x": (b"{}", "application/json")}, policy)
    # http rejected by the https-only scheme policy, before any lookup.
    with pytest.raises(DisallowedSchemeError):
        fetcher.fetch("http://example.com/x")


# --------------------------------------------------------------------------
# LiveFetcher: gating + pre-connect rejections (no socket reached)
# --------------------------------------------------------------------------


def test_live_fetcher_disabled_by_default() -> None:
    fetcher = LiveFetcher(FetchPolicy(official_domains=("example.com",)))
    with pytest.raises(NetworkDisabledError):
        fetcher.fetch("https://example.com/")


def test_non_allowlisted_host_rejected_pre_connect(monkeypatch) -> None:
    _forbid_sockets(monkeypatch)
    fetcher = LiveFetcher(FetchPolicy(official_domains=("example.com",)), enable_network=True)
    # evil.example is not allowlisted -> DisallowedHostError before any DNS/socket.
    with pytest.raises(DisallowedHostError):
        fetcher.fetch("https://evil.example/")


def test_non_https_rejected_pre_connect(monkeypatch) -> None:
    _forbid_sockets(monkeypatch)
    fetcher = LiveFetcher(FetchPolicy(official_domains=("example.com",)), enable_network=True)
    with pytest.raises(DisallowedSchemeError):
        fetcher.fetch("http://example.com/")


def test_allowlisted_host_resolving_to_private_ip_is_blocked(monkeypatch) -> None:
    # Host "10.0.0.1" is allowlisted and resolves numerically to a private IP;
    # the SSRF check must still block it (loopback opt-in does not cover RFC1918).
    def _open_boom(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("connection attempted to a private address")

    fetcher = LiveFetcher(
        FetchPolicy(
            official_domains=("10.0.0.1",),
            allowed_schemes=frozenset({"http"}),
            allow_loopback=True,
        ),
        enable_network=True,
    )
    monkeypatch.setattr(fetcher, "_open", _open_boom)
    with pytest.raises(BlockedAddressError):
        fetcher.fetch("http://10.0.0.1/")


# --------------------------------------------------------------------------
# LiveFetcher against a loopback server (explicitly allowlisted for the test)
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test server logging
        pass

    def do_GET(self):  # noqa: N802 - required BaseHTTPRequestHandler name
        path = self.path
        if path == "/ok":
            body = b'{"provider":"loop","offers":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/slow":
            time.sleep(1.5)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")
        elif path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            # 1 MiB, far above the tiny cap the test configures.
            self.wfile.write(b"x" * (1024 * 1024))
        elif path == "/badmime":
            body = b"\x00\x01\x02"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/redirect-disallowed":
            self.send_response(302)
            self.send_header("Location", "http://evil.example/ok")
            self.end_headers()
        elif path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1/ok")
            self.end_headers()
        elif path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def loopback_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _loopback_policy(**overrides) -> FetchPolicy:
    base = {
        "official_domains": ("127.0.0.1", "10.0.0.1"),
        "allowed_schemes": frozenset({"http"}),
        "allow_loopback": True,
        "read_timeout": 0.5,
        "max_bytes": 4096,
        "max_redirects": 2,
    }
    base.update(overrides)
    return FetchPolicy(**base)


def test_live_fetch_happy_path(loopback_server) -> None:
    fetcher = LiveFetcher(_loopback_policy(), enable_network=True)
    result = fetcher.fetch(f"{loopback_server}/ok")
    assert result.status == 200
    assert result.mime == "application/json"
    assert result.content == b'{"provider":"loop","offers":[]}'
    assert len(result.content_hash) == 64


def test_live_fetch_timeout_aborts_within_budget(loopback_server) -> None:
    fetcher = LiveFetcher(_loopback_policy(read_timeout=0.3), enable_network=True)
    start = time.monotonic()
    with pytest.raises(FetchTimeoutError):
        fetcher.fetch(f"{loopback_server}/slow")
    # The 0.3s budget must abort well before the handler's 1.5s sleep.
    assert time.monotonic() - start < 1.2


def test_live_fetch_oversize_aborted(loopback_server) -> None:
    fetcher = LiveFetcher(_loopback_policy(max_bytes=1024), enable_network=True)
    with pytest.raises(ResponseTooLargeError):
        fetcher.fetch(f"{loopback_server}/big")


def test_live_fetch_disallowed_mime(loopback_server) -> None:
    fetcher = LiveFetcher(_loopback_policy(), enable_network=True)
    with pytest.raises(DisallowedMimeError):
        fetcher.fetch(f"{loopback_server}/badmime")


def test_redirect_to_disallowed_host_rejected_mid_chain(loopback_server) -> None:
    fetcher = LiveFetcher(_loopback_policy(), enable_network=True)
    # First hop is allowlisted 127.0.0.1; the 302 target host is not allowlisted.
    with pytest.raises(DisallowedHostError):
        fetcher.fetch(f"{loopback_server}/redirect-disallowed")


def test_redirect_to_private_host_rejected_mid_chain(loopback_server) -> None:
    fetcher = LiveFetcher(_loopback_policy(), enable_network=True)
    # 10.0.0.1 IS allowlisted, so the host check passes on the redirect target,
    # but the SSRF address check must still block the private IP mid-chain.
    with pytest.raises(BlockedAddressError):
        fetcher.fetch(f"{loopback_server}/redirect-private")


def test_redirect_budget_exhausted(loopback_server) -> None:
    fetcher = LiveFetcher(_loopback_policy(max_redirects=2), enable_network=True)
    with pytest.raises(TooManyRedirectsError):
        fetcher.fetch(f"{loopback_server}/loop")
