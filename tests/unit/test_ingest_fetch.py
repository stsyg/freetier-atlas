"""Safe-fetch guard tests (offline).

The pure policy functions are tested directly with plain values. The live
transport is exercised only against a loopback (127.0.0.1) HTTP server that is
explicitly allowlisted for these tests; nothing here performs external network
egress. Numeric IP literals (127.0.0.1, 10.0.0.1) resolve without DNS.

The final section is adversarial: it drives :class:`LiveFetcher` through a
*hostile resolver* that answers the validation lookup and the connect-time
lookup differently -- the DNS-rebinding shape. Those tests never leave the
machine either, because the hostile resolver answers every lookup after the
first with the loopback address of a server the test started itself.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
import urllib.error
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
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
    check_addresses,
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


# --------------------------------------------------------------------------
# DNS rebinding: the window between the validation lookup and the connect
# --------------------------------------------------------------------------
#
# Validating the addresses a hostname resolves to and then handing the *URL* to
# an HTTP client leaves the client to resolve the name a second time at TCP
# connect. The address that was vetted is therefore not necessarily the address
# that is connected to, and a hostile authoritative server can answer the two
# lookups differently. These tests reproduce that exactly.
#
# No test below performs external network egress: the hostile resolver answers
# every lookup after the first with the loopback address of a server the test
# started itself, so even the "public" address is never contacted.

#: A publicly-routable literal used only as the *validation* answer. The same
#: stub answers the connect-time lookup with loopback, so no packet is ever
#: addressed to it.
_PUBLIC_IP = "8.8.8.8"

#: RFC 6761 reserved TLD: `.test` is never delegated, so these names cannot
#: resolve for real even if a stub were somehow bypassed.
_PINNED_HOST = "pinned.test"
_FIRST_HOP_HOST = "hop-one.test"

_LOOPBACK = "127.0.0.1"

#: The body the stand-in internal service returns. Its arrival in a FetchResult
#: is proof that a blocked address was reached.
_INTERNAL_BODY = b'{"internal":"reached"}'


def _addrinfo(ip: str, port) -> tuple:
    """Build one ``getaddrinfo`` 5-tuple for ``ip``, matching its family."""

    address = ipaddress.ip_address(ip)
    if address.version == 6:
        sockaddr = (ip, port or 0, 0, 0)
        family = socket.AF_INET6
    else:
        sockaddr = (ip, port or 0)
        family = socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


class _HostileResolver:
    """A ``socket.getaddrinfo`` stand-in that changes its answer over time.

    ``first`` is returned to the very first lookup -- the validation lookup --
    and ``later`` to every lookup after it. That is precisely what a hostile or
    compromised authoritative server can do, and it is also what keeps these
    tests offline: whatever address is validated, the connection is steered to
    the loopback server the test owns.
    """

    def __init__(self, first: Sequence[str], later: Sequence[str]) -> None:
        self.first = tuple(first)
        self.later = tuple(later)
        self.queries: list[str] = []

    def __call__(self, host, port=None, *args, **kwargs):
        self.queries.append(host)
        answers = self.first if len(self.queries) == 1 else self.later
        return [_addrinfo(ip, port) for ip in answers]


class _ScriptedResolver:
    """A ``socket.getaddrinfo`` stand-in scripted per hostname.

    ``script[host]`` is the sequence of answers handed to successive lookups of
    that host; the final entry repeats. A host that is not scripted must be an
    address literal and resolves to itself, which is how a real resolver treats
    a numeric host -- so a pinned connect is not given special treatment here.
    """

    def __init__(self, script: Mapping[str, Sequence[Sequence[str]]]) -> None:
        self._script = {host: list(answers) for host, answers in script.items()}
        self.queries: list[str] = []

    def __call__(self, host, port=None, *args, **kwargs):
        self.queries.append(host)
        answers = self._script.get(host)
        if answers is None:
            # Not scripted: it must be a literal. Anything else would mean the
            # test silently invented a lookup, so fail loudly instead.
            ipaddress.ip_address(host)
            return [_addrinfo(host, port)]
        chosen = answers[0] if len(answers) == 1 else answers.pop(0)
        return [_addrinfo(ip, port) for ip in chosen]


class _InternalService:
    """Records what a stand-in internal service was asked for."""

    def __init__(self) -> None:
        self.port = 0
        self.requests: list[tuple[str, str | None]] = []

    @property
    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]


@pytest.fixture
def internal_service():
    """An HTTP server on loopback, standing in for a private internal service.

    The rebinding policies below set ``allow_loopback=False``, so loopback *is*
    the blocked address in those tests and any recorded request proves the SSRF
    guard was defeated. The server also records the ``Host`` header, so a test
    can prove virtual hosting survives connecting by IP.
    """

    service = _InternalService()

    class _InternalHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence test server logging
            pass

        def do_GET(self):  # noqa: N802 - required BaseHTTPRequestHandler name
            service.requests.append((self.path, self.headers.get("Host")))
            if self.path == "/redirect-to-second-host":
                self.send_response(302)
                self.send_header("Location", f"http://{_PINNED_HOST}:{service.port}/internal")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_INTERNAL_BODY)))
            self.end_headers()
            self.wfile.write(_INTERNAL_BODY)

    server = ThreadingHTTPServer((_LOOPBACK, 0), _InternalHandler)
    service.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service
    finally:
        server.shutdown()
        server.server_close()


def _rebinding_policy(**overrides) -> FetchPolicy:
    base = {
        "official_domains": (_PINNED_HOST, _FIRST_HOP_HOST),
        "allowed_schemes": frozenset({"http"}),
        # Loopback is the blocked "internal" address in these tests, so the
        # escape hatch stays OFF unless a test needs a reachable safe target.
        "allow_loopback": False,
        "read_timeout": 5.0,
        "max_bytes": 4096,
        "max_redirects": 2,
    }
    base.update(overrides)
    return FetchPolicy(**base)


# --- Positive control: can this harness observe the bad outcome at all? -----


def test_positive_control_harness_can_observe_a_private_connection(
    internal_service, monkeypatch
) -> None:
    """A test that cannot detect the bad outcome proves nothing when it is green.

    This reproduces the rebinding sequence with **no fetch guard in the way** and
    asserts every observation the guard tests below depend on: that the stub
    really does control the connect-time lookup, that the socket really lands on
    the blocked address, and that the service really records the request.
    """

    resolver = _HostileResolver(first=(_PUBLIC_IP,), later=(_LOOPBACK,))
    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    # 1. The validation-shaped lookup answers with a public address, which the
    #    unchanged SSRF policy accepts.
    validated = tuple(
        info[4][0] for info in socket.getaddrinfo(_PINNED_HOST, None, proto=socket.IPPROTO_TCP)
    )
    assert check_addresses(validated) == (_PUBLIC_IP,)

    # 2. The connect-time lookup answers with the blocked address instead, and
    #    the socket really does land there.
    sock = socket.create_connection((_PINNED_HOST, internal_service.port), 5.0)
    try:
        peer = sock.getpeername()[0]
        assert peer == _LOOPBACK
        assert address_block_reason(peer) is not None
    finally:
        sock.close()

    # 3. An unguarded HTTP request over that connection reaches the internal
    #    service, and the service records it. This is the outcome to prevent.
    connection = http.client.HTTPConnection(_PINNED_HOST, internal_service.port, timeout=5.0)
    try:
        connection.request("GET", "/internal")
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == _INTERNAL_BODY
    finally:
        connection.close()

    assert internal_service.requests == [("/internal", f"{_PINNED_HOST}:{internal_service.port}")]


# --- The peer re-check defers to the one address classifier ----------------


def test_peer_address_check_defers_to_the_address_classifier(internal_service) -> None:
    """The same socket, the same peer: only the shared policy changes the verdict.

    If the peer re-check restated the address rules instead of calling
    ``address_block_reason``, the two copies could drift apart. Flipping only
    ``allow_loopback`` proves it is the shared classifier deciding.

    Imported inside the test, not at module scope, so that on a tree without the
    fix this test fails on its own rather than breaking collection for the whole
    module and obscuring which behaviours actually regressed.
    """

    from app.ingest.fetch import check_peer_address

    sock = socket.create_connection((_LOOPBACK, internal_service.port), 5.0)
    try:
        with pytest.raises(BlockedAddressError):
            check_peer_address(sock)
        assert check_peer_address(sock, allow_loopback=True) == _LOOPBACK
    finally:
        sock.close()


def test_peer_address_check_fails_closed_on_an_unreadable_peer() -> None:
    from app.ingest.fetch import check_peer_address

    class _OpaquePeer:
        def getpeername(self):
            return ("not-an-ip", 443)

    with pytest.raises(BlockedAddressError):
        check_peer_address(_OpaquePeer())


# --- The core regression: rebinding between validation and connect ---------


def test_dns_rebinding_between_validation_and_connect_is_refused(
    internal_service, monkeypatch
) -> None:
    """The connection must be made to an address that passed validation.

    The resolver answers the validation lookup with a public address and every
    later lookup with the blocked loopback address. Validating a name and then
    letting the HTTP client re-resolve it reaches the internal service; pinning
    the validated address and re-checking the socket peer refuses it.
    """

    resolver = _HostileResolver(first=(_PUBLIC_IP,), later=(_LOOPBACK,))
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    fetcher = LiveFetcher(_rebinding_policy(), enable_network=True)

    with pytest.raises(BlockedAddressError):
        fetcher.fetch(f"http://{_PINNED_HOST}:{internal_service.port}/internal")

    # The decisive assertion: no request ever reached the blocked address.
    assert internal_service.requests == []
    assert len(resolver.queries) >= 2, (
        "only one lookup happened, so this run never opened the rebinding "
        "window and the assertion above proves nothing"
    )


def test_the_hostname_is_resolved_once_and_the_connection_is_pinned(
    internal_service, monkeypatch
) -> None:
    """One hostname lookup per hop: a second one is a second chance to rebind."""

    resolver = _HostileResolver(first=(_LOOPBACK,), later=(_LOOPBACK,))
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    # allow_loopback=True here: this test needs a *reachable safe* target so the
    # happy path through the pinned connection is exercised end to end.
    fetcher = LiveFetcher(_rebinding_policy(allow_loopback=True), enable_network=True)

    result = fetcher.fetch(f"http://{_PINNED_HOST}:{internal_service.port}/internal")

    assert result.status == 200
    assert result.content == _INTERNAL_BODY
    assert resolver.queries.count(_PINNED_HOST) == 1
    # Whatever lookup follows is for the already-validated literal, not the name.
    assert resolver.queries[1:] == [_LOOPBACK]
    # Virtual hosting survives: the Host header carries the hostname, not the IP.
    assert internal_service.requests == [("/internal", f"{_PINNED_HOST}:{internal_service.port}")]


@pytest.mark.parametrize(
    "hostile",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # RFC1918
        "172.16.0.1",  # RFC1918
        "192.168.1.1",  # RFC1918
        "169.254.1.1",  # link-local
        "169.254.169.254",  # link-local cloud metadata
        "0.0.0.0",  # unspecified
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:169.254.169.254",  # IPv4-mapped cloud metadata
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
    ],
)
def test_a_host_resolving_to_any_unsafe_address_fails_closed(
    internal_service, monkeypatch, hostile
) -> None:
    """Only *some* addresses safe must fail closed, not opportunistically connect.

    The safe address is listed first, so a guard that stopped at the first usable
    answer would happily connect while the hostile address remained reachable.
    """

    resolver = _HostileResolver(first=(_PUBLIC_IP, hostile), later=(_LOOPBACK,))
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    fetcher = LiveFetcher(_rebinding_policy(), enable_network=True)

    with pytest.raises(BlockedAddressError):
        fetcher.fetch(f"http://{_PINNED_HOST}:{internal_service.port}/internal")

    assert internal_service.requests == []


def test_an_empty_resolution_is_refused(internal_service, monkeypatch) -> None:
    resolver = _HostileResolver(first=(), later=(_LOOPBACK,))
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    fetcher = LiveFetcher(_rebinding_policy(), enable_network=True)

    with pytest.raises(BlockedAddressError):
        fetcher.fetch(f"http://{_PINNED_HOST}:{internal_service.port}/internal")

    assert internal_service.requests == []


# --- Every redirect hop, not just the first -------------------------------


def test_every_redirect_hop_is_resolved_once_and_pinned(internal_service, monkeypatch) -> None:
    """The pin is applied per hop; hop two must not be re-resolved either."""

    resolver = _ScriptedResolver({_FIRST_HOP_HOST: [(_LOOPBACK,)], _PINNED_HOST: [(_LOOPBACK,)]})
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    fetcher = LiveFetcher(_rebinding_policy(allow_loopback=True), enable_network=True)

    result = fetcher.fetch(
        f"http://{_FIRST_HOP_HOST}:{internal_service.port}/redirect-to-second-host"
    )

    assert result.status == 200
    assert result.final_url.endswith("/internal")
    assert resolver.queries.count(_FIRST_HOP_HOST) == 1
    assert resolver.queries.count(_PINNED_HOST) == 1
    # Two hops, one pinned connect each.
    assert resolver.queries.count(_LOOPBACK) == 2
    assert internal_service.paths == ["/redirect-to-second-host", "/internal"]


def test_a_redirect_target_resolving_to_a_private_address_is_blocked_mid_chain(
    internal_service, monkeypatch
) -> None:
    """The SSRF recheck still runs on hop two, with a hostname rather than a literal."""

    resolver = _ScriptedResolver({_FIRST_HOP_HOST: [(_LOOPBACK,)], _PINNED_HOST: [("10.0.0.1",)]})
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    fetcher = LiveFetcher(_rebinding_policy(allow_loopback=True), enable_network=True)

    with pytest.raises(BlockedAddressError):
        fetcher.fetch(f"http://{_FIRST_HOP_HOST}:{internal_service.port}/redirect-to-second-host")

    # Only the first hop was ever served.
    assert internal_service.paths == ["/redirect-to-second-host"]


def test_each_hop_is_pinned_to_its_own_validated_addresses(internal_service, monkeypatch) -> None:
    """Hop two is pinned and peer-checked exactly as hop one is.

    A hop-two *refusal* cannot be staged honestly: the only address a test can
    make reachable is loopback, and reaching it on hop one requires the very
    ``allow_loopback`` escape hatch that then legitimately permits it on hop two.
    So this asserts the wiring white-box instead -- every hop builds its own
    pinned opener from the addresses that hop just validated, carrying the
    policy's loopback setting -- while the refusal itself is covered on a single
    hop by ``test_dns_rebinding_between_validation_and_connect_is_refused``.
    """

    from app.ingest.fetch import _PinnedHTTPHandler

    resolver = _ScriptedResolver({_FIRST_HOP_HOST: [(_LOOPBACK,)], _PINNED_HOST: [(_LOOPBACK,)]})
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    fetcher = LiveFetcher(_rebinding_policy(allow_loopback=True), enable_network=True)

    pinned: list[tuple[tuple[str, ...], bool]] = []
    real_build = fetcher._build_opener

    def _record(addresses):
        opener = real_build(addresses)
        handler = next(h for h in opener.handlers if isinstance(h, _PinnedHTTPHandler))
        connector = handler._connect
        pinned.append((connector._addresses, connector._allow_loopback))
        return opener

    monkeypatch.setattr(fetcher, "_build_opener", _record)

    result = fetcher.fetch(
        f"http://{_FIRST_HOP_HOST}:{internal_service.port}/redirect-to-second-host"
    )

    assert result.status == 200
    assert internal_service.paths == ["/redirect-to-second-host", "/internal"]
    # Two hops, two independently pinned openers, both honouring the policy.
    assert pinned == [((_LOOPBACK,), True), ((_LOOPBACK,), True)]


# --- TLS must not be traded away for the SSRF fix -------------------------


def test_the_default_tls_context_still_verifies_certificates() -> None:
    context = LiveFetcher(_rebinding_policy())._ssl_context()
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


def test_tls_is_negotiated_for_the_hostname_not_the_pinned_address(
    internal_service, monkeypatch
) -> None:
    """Pinning the IP must not move certificate verification onto the IP.

    ``ssl`` verifies the peer certificate against ``server_hostname``, so proving
    that ``server_hostname`` is the URL's hostname -- on a context that keeps
    ``check_hostname`` on -- proves the certificate is still checked against the
    name, while the TCP connection targets the validated address.
    """

    class _HandshakeReached(Exception):
        pass

    seen: dict[str, object] = {}

    class _RecordingContext:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED

        def wrap_socket(self, sock, *, server_hostname=None, **kwargs):
            seen["server_hostname"] = server_hostname
            seen["peer"] = sock.getpeername()[0]
            raise _HandshakeReached

    resolver = _ScriptedResolver({_PINNED_HOST: [(_LOOPBACK,)]})
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    fetcher = LiveFetcher(
        _rebinding_policy(allowed_schemes=frozenset({"https"}), allow_loopback=True),
        enable_network=True,
    )
    monkeypatch.setattr(fetcher, "_ssl_context", _RecordingContext)

    with pytest.raises(_HandshakeReached):
        fetcher.fetch(f"https://{_PINNED_HOST}:{internal_service.port}/internal")

    assert seen["server_hostname"] == _PINNED_HOST
    assert seen["peer"] == _LOOPBACK


# --- The opener keeps every handler it deliberately omits -----------------


def test_the_opener_still_omits_redirect_error_and_proxy_handlers() -> None:
    """Pinning changed how the opener is built; it must not have gained handlers.

    Manual redirects (so the allowlist and SSRF checks re-run per hop), 3xx/4xx
    returned as responses, and no proxy diversion are all load-bearing.
    """

    import urllib.request

    fetcher = LiveFetcher(_rebinding_policy(), enable_network=True)
    opener = fetcher._build_opener(("93.184.216.34",))
    forbidden = (
        urllib.request.HTTPRedirectHandler,
        urllib.request.HTTPErrorProcessor,
        urllib.request.ProxyHandler,
    )
    assert not [h for h in opener.handlers if isinstance(h, forbidden)]


# --- A real TLS handshake, pinned to an IP, verified against the name ------
#
# `cryptography` IS a declared test dependency (requirements-dev.txt and the
# pyproject dev extra, which are mirrored and enforced by
# tests/unit/test_requirements_sync.py), so these two tests EXECUTE in CI.
#
# They were previously skipped there, and the skip was rationalised on the
# grounds that the property is enforced by the standard-library-only tests
# above. That reasoning was measured and found wrong. Those tests assert
# `server_hostname`, `check_hostname` and `CERT_REQUIRED` and INFER the rest
# from the stdlib contract; two plausible defects satisfy every one of those
# assertions while defeating certificate verification completely -- weakening
# the injected context, and retrying unverified when an SSL error is raised.
# Both stayed green in CI. Only a real handshake catches them. The
# `importorskip` guards remain so a developer without the extra installed still
# gets a clean skip rather than a collection error.


def _tls_chain(tmp_path, hostname: str) -> tuple[str, str, str]:
    """Return (ca, certificate, key) PEM paths for a throwaway chain.

    Generated fresh into the test's temporary directory on every run and never
    committed, so nothing here is a credential that could leak into the tree.
    The leaf names ``hostname`` only -- it carries no IP SAN, which is what makes
    the positive test discriminating.
    """

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.now(UTC).replace(tzinfo=None)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "atlas-test-ca")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        # OpenSSL 3 will not build a chain without the key identifiers and a CA
        # key usage that permits certificate signing.
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_pem = tmp_path / "ca.pem"
    certificate_pem = tmp_path / "leaf.pem"
    key_pem = tmp_path / "leaf.key"
    ca_pem.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
    certificate_pem.write_bytes(leaf_certificate.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(ca_pem), str(certificate_pem), str(key_pem)


@contextlib.contextmanager
def _tls_service(certificate_pem: str, key_pem: str):
    """Serve JSON over TLS on loopback and yield the port."""

    class _TlsHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802 - required BaseHTTPRequestHandler name
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_INTERNAL_BODY)))
            self.end_headers()
            self.wfile.write(_INTERNAL_BODY)

    server = ThreadingHTTPServer((_LOOPBACK, 0), _TlsHandler)
    port = server.server_address[1]
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate_pem, key_pem)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


def _tls_fetcher(monkeypatch, ca_pem: str) -> LiveFetcher:
    resolver = _ScriptedResolver({_PINNED_HOST: [(_LOOPBACK,)]})
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    fetcher = LiveFetcher(
        _rebinding_policy(allowed_schemes=frozenset({"https"}), allow_loopback=True),
        enable_network=True,
    )
    monkeypatch.setattr(fetcher, "_ssl_context", lambda: ssl.create_default_context(cafile=ca_pem))
    return fetcher


def test_https_certificate_is_verified_against_the_hostname_end_to_end(
    tmp_path, monkeypatch
) -> None:
    """The connection dials 127.0.0.1; the certificate names only ``pinned.test``.

    It carries no IP SAN, so this handshake could not succeed if pinning had
    moved verification onto the address that was dialled. This runs in CI: it is
    the only test here that catches a weakened or bypassed TLS context, because
    such a defect leaves every standard-library-only assertion above intact.
    """

    pytest.importorskip("cryptography")
    ca_pem, certificate_pem, key_pem = _tls_chain(tmp_path, _PINNED_HOST)
    fetcher = _tls_fetcher(monkeypatch, ca_pem)

    with _tls_service(certificate_pem, key_pem) as port:
        result = fetcher.fetch(f"https://{_PINNED_HOST}:{port}/internal")

    assert result.status == 200
    assert result.content == _INTERNAL_BODY


def test_https_still_rejects_a_certificate_for_a_different_hostname(tmp_path, monkeypatch) -> None:
    """Verification is genuinely on: the same setup with a mismatched name fails."""

    pytest.importorskip("cryptography")
    ca_pem, certificate_pem, key_pem = _tls_chain(tmp_path, "impostor.test")
    fetcher = _tls_fetcher(monkeypatch, ca_pem)

    with _tls_service(certificate_pem, key_pem) as port:
        with pytest.raises(urllib.error.URLError) as excinfo:
            fetcher.fetch(f"https://{_PINNED_HOST}:{port}/internal")

    assert isinstance(excinfo.value.reason, ssl.SSLCertVerificationError)
