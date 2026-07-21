"""Safe source-fetching transport for the ingestion subsystem (F004 slice 1).

This module is the **only** place in the ingestion subsystem that is permitted to
open a network connection, and it is closed by default. Everything else -- the
adapters -- depends on the :class:`Fetcher` protocol, never on an HTTP client
directly, so the network boundary is a single, hardened, injectable seam.

Design
------
The security decisions are **pure functions** (:func:`screen_url`,
:func:`is_blocked_address`, :func:`validate_content_type`, host-allowlist
matching) so the entire policy can be exercised offline, with no sockets. Only
:class:`SafeFetcher` performs I/O, and only when explicitly enabled
(``enable_network=True``); the default :class:`OfflineFetcher` and the test
:class:`FixtureFetcher` never touch the network.

Guards enforced (docs/SECURITY_PRIVACY_ABUSE.md "Source fetching"):

* **Official-domain allowlist** -- the host must match the provider's approved
  domains; re-checked on every redirect hop.
* **SSRF / private-network blocking** -- resolved addresses in loopback,
  RFC1918, link-local, ULA, reserved, multicast, or the cloud metadata address
  are rejected.
* **Scheme allowlist** -- ``https`` only by default.
* **Limits** -- connect/read timeout, streamed max-size cap with early abort,
  MIME validation, and a bounded redirect count.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPSConnection
from typing import Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

# The cloud instance-metadata address (AWS/GCP/Azure IMDS). Explicitly named even
# though it also falls under the link-local range, so the intent is unmistakable.
CLOUD_METADATA_IPS: frozenset[str] = frozenset({"169.254.169.254", "fd00:ec2::254"})

# Redirect status codes we will follow (after re-screening the target).
_REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})

# Default MIME types acceptable from an official documentation/data source.
DEFAULT_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "text/html",
        "text/plain",
        "text/xml",
        "application/xml",
        "application/json",
        "application/rss+xml",
        "application/atom+xml",
    }
)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class FetchError(Exception):
    """Base class for every fetch rejection or failure."""


class NetworkDisabledError(FetchError):
    """Raised when a fetch is attempted but live network access is not enabled."""


class DisallowedSchemeError(FetchError):
    """The URL scheme is not in the allowed set (https only by default)."""


class DisallowedHostError(FetchError):
    """The URL host is not in the provider's official-domain allowlist."""


class BlockedAddressError(FetchError):
    """The host resolved to a private/loopback/link-local/metadata address."""


class DisallowedContentTypeError(FetchError):
    """The response MIME type is not in the allowed set."""


class ResponseTooLargeError(FetchError):
    """The response exceeded the configured maximum size."""


class FetchTimeoutError(FetchError):
    """The request did not complete within the configured timeout."""


class TooManyRedirectsError(FetchError):
    """The redirect chain exceeded the configured maximum."""


class TransportError(FetchError):
    """A lower-level connection/transport failure (DNS, TLS, reset, ...)."""


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FetchPolicy:
    """Immutable per-source fetch policy.

    ``allowlist`` holds the provider's approved official domains. A host matches
    when it equals an entry or is a subdomain of one (``developers.cloudflare.com``
    matches ``cloudflare.com``; ``notcloudflare.com`` does not).

    ``allow_loopback`` / ``allow_private`` default to ``False`` and exist only so
    a test can exercise the timeout/size mechanics against a ``127.0.0.1`` server;
    production policies must leave them off.
    """

    allowlist: tuple[str, ...]
    allowed_schemes: tuple[str, ...] = ("https",)
    allowed_content_types: frozenset[str] = DEFAULT_ALLOWED_CONTENT_TYPES
    max_bytes: int = 5_000_000
    timeout_seconds: float = 10.0
    max_redirects: int = 5
    allow_loopback: bool = False
    allow_private: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowlist", tuple(_normalise_host(d) for d in self.allowlist))
        object.__setattr__(self, "allowed_schemes", tuple(s.lower() for s in self.allowed_schemes))


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FetchResult:
    """The outcome of a successful, fully-screened fetch."""

    final_url: str
    status: int
    mime: str
    body: bytes
    content_hash: str
    fetched_at: datetime
    redirect_chain: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Pure policy functions (no I/O)
# --------------------------------------------------------------------------- #
def _normalise_host(host: str | None) -> str:
    """Lower-case and strip a host, dropping any trailing dot and brackets."""

    if not host:
        return ""
    return host.strip().rstrip(".").strip("[]").lower()


def host_matches_allowlist(host: str, allowlist: Sequence[str]) -> bool:
    """True if ``host`` equals or is a subdomain of an allowlisted domain."""

    host = _normalise_host(host)
    if not host:
        return False
    for entry in allowlist:
        entry = _normalise_host(entry)
        if host == entry or host.endswith(f".{entry}"):
            return True
    return False


def is_blocked_address(ip: str, policy: FetchPolicy) -> bool:
    """True if ``ip`` is a private/loopback/link-local/metadata/reserved address.

    IPv4-mapped IPv6 addresses are unwrapped so an attacker cannot smuggle a
    private v4 address through a v6 literal.
    """

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # Not a literal address; treat as unresolvable/unsafe.
        return True

    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped

    if str(addr) in CLOUD_METADATA_IPS:
        return True
    if addr.is_loopback:
        return not policy.allow_loopback
    if (
        addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return not policy.allow_private
    return False


def validate_content_type(content_type: str | None, allowed: frozenset[str]) -> str:
    """Return the bare media type if allowed, else raise.

    The ``charset``/boundary parameters are stripped before comparison.
    """

    media = (content_type or "").split(";", 1)[0].strip().lower()
    if media not in allowed:
        raise DisallowedContentTypeError(
            f"content type {media or '(missing)'!r} is not allowed (allowed: {sorted(allowed)})"
        )
    return media


def screen_url(url: str, policy: FetchPolicy) -> str:
    """Screen a URL's scheme and host *before any connection*.

    Returns the normalised host on success; raises a :class:`FetchError`
    subclass naming the specific reason otherwise.
    """

    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in policy.allowed_schemes:
        raise DisallowedSchemeError(
            f"scheme {scheme or '(none)'!r} is not allowed "
            f"(allowed: {list(policy.allowed_schemes)}) for {url!r}"
        )
    host = _normalise_host(parts.hostname)
    if not host_matches_allowlist(host, policy.allowlist):
        raise DisallowedHostError(
            f"host {host or '(none)'!r} is not in the official-domain allowlist "
            f"{list(policy.allowlist)} for {url!r}"
        )
    return host


# --------------------------------------------------------------------------- #
# Fetcher protocol
# --------------------------------------------------------------------------- #
@runtime_checkable
class Fetcher(Protocol):
    """The network seam adapters depend on. Implementations must screen URLs."""

    def fetch(self, url: str) -> FetchResult: ...


Resolver = Callable[[str], list[str]]
"""Resolve a host to a list of IP-address strings (injectable for tests)."""


def _default_resolver(host: str) -> list[str]:
    """Resolve ``host`` to every address it maps to via ``getaddrinfo``."""

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:  # pragma: no cover - network/DNS failure path
        raise TransportError(f"could not resolve host {host!r}: {exc}") from exc
    return [info[4][0] for info in infos]


# --------------------------------------------------------------------------- #
# Offline (default) fetcher -- never opens a socket
# --------------------------------------------------------------------------- #
class OfflineFetcher:
    """The default fetcher: refuses every fetch without touching the network."""

    def fetch(self, url: str) -> FetchResult:
        raise NetworkDisabledError(
            "network access is disabled; the default fetcher performs no I/O. "
            "Use a FixtureFetcher in tests, or an explicitly enabled SafeFetcher."
        )


def default_fetcher() -> Fetcher:
    """Return the safe default fetcher (offline)."""

    return OfflineFetcher()


# --------------------------------------------------------------------------- #
# Fixture fetcher -- deterministic, offline, still fully screened
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FixtureResponse:
    """A recorded response served by :class:`FixtureFetcher`."""

    body: bytes
    content_type: str = "application/json"
    status: int = 200


class FixtureFetcher:
    """Serve recorded responses for tests, enforcing scheme + host allowlist.

    It never resolves DNS or opens a socket, but it *does* run :func:`screen_url`
    so tests prove adapters reject non-allowlisted or non-https URLs even though
    no network is involved. Content-type and size limits are enforced too, so the
    same fixture drives the same validation an adapter would see live.
    """

    def __init__(
        self,
        responses: Mapping[str, FixtureResponse | bytes],
        policy: FetchPolicy,
    ) -> None:
        self._responses: dict[str, FixtureResponse] = {
            url: (resp if isinstance(resp, FixtureResponse) else FixtureResponse(body=resp))
            for url, resp in responses.items()
        }
        self._policy = policy

    def fetch(self, url: str) -> FetchResult:
        screen_url(url, self._policy)
        response = self._responses.get(url)
        if response is None:
            raise TransportError(f"no fixture recorded for {url!r}")
        if len(response.body) > self._policy.max_bytes:
            raise ResponseTooLargeError(
                f"fixture body of {len(response.body)} bytes exceeds "
                f"max_bytes={self._policy.max_bytes} for {url!r}"
            )
        mime = validate_content_type(response.content_type, self._policy.allowed_content_types)
        return FetchResult(
            final_url=url,
            status=response.status,
            mime=mime,
            body=response.body,
            content_hash=hashlib.sha256(response.body).hexdigest(),
            fetched_at=datetime.now(UTC),
            redirect_chain=(),
        )


# --------------------------------------------------------------------------- #
# Safe live fetcher -- the only code that opens a socket, disabled by default
# --------------------------------------------------------------------------- #
class SafeFetcher:
    """Hardened live HTTP(S) fetcher. Performs I/O only when ``enable_network``."""

    def __init__(
        self,
        policy: FetchPolicy,
        *,
        enable_network: bool = False,
        resolver: Resolver | None = None,
    ) -> None:
        self._policy = policy
        self._enable_network = enable_network
        self._resolver = resolver or _default_resolver

    def fetch(self, url: str) -> FetchResult:
        if not self._enable_network:
            raise NetworkDisabledError(
                "live network access is disabled for this fetcher; construct it "
                "with enable_network=True to permit egress."
            )

        policy = self._policy
        redirect_chain: list[str] = []
        current = url
        for _hop in range(policy.max_redirects + 1):
            # Re-screen scheme + host on *every* hop (allowlist enforced mid-chain).
            screen_url(current, policy)
            self._screen_resolved_addresses(current)

            status, headers, body_reader = self._open(current)
            location = headers.get("Location") or headers.get("location")
            if status in _REDIRECT_STATUSES and location:
                body_reader(0)  # drain/close without reading the redirect body
                redirect_chain.append(current)
                current = urljoin(current, location)
                continue

            body = body_reader(policy.max_bytes + 1)
            if len(body) > policy.max_bytes:
                raise ResponseTooLargeError(
                    f"response from {current!r} exceeds max_bytes={policy.max_bytes}"
                )
            mime = validate_content_type(
                headers.get("Content-Type") or headers.get("content-type"),
                policy.allowed_content_types,
            )
            return FetchResult(
                final_url=current,
                status=status,
                mime=mime,
                body=body,
                content_hash=hashlib.sha256(body).hexdigest(),
                fetched_at=datetime.now(UTC),
                redirect_chain=tuple(redirect_chain),
            )

        raise TooManyRedirectsError(
            f"redirect chain from {url!r} exceeded max_redirects={policy.max_redirects}"
        )

    def _screen_resolved_addresses(self, url: str) -> None:
        host = _normalise_host(urlsplit(url).hostname)
        for ip in self._resolver(host):
            if is_blocked_address(ip, self._policy):
                raise BlockedAddressError(
                    f"host {host!r} resolves to blocked address {ip!r} "
                    "(private/loopback/link-local/metadata)"
                )

    def _open(self, url: str) -> tuple[int, Mapping[str, str], Callable[[int], bytes]]:
        parts = urlsplit(url)
        host = parts.hostname or ""
        timeout = self._policy.timeout_seconds
        if parts.scheme == "https":
            conn: HTTPConnection = HTTPSConnection(
                host, parts.port or 443, timeout=timeout, context=ssl.create_default_context()
            )
        else:
            conn = HTTPConnection(host, parts.port or 80, timeout=timeout)

        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        try:
            conn.request("GET", path, headers={"User-Agent": "freetier-atlas-ingest/0.1"})
            response = conn.getresponse()
        except TimeoutError as exc:
            conn.close()
            raise FetchTimeoutError(f"request to {url!r} timed out after {timeout}s") from exc
        except OSError as exc:
            conn.close()
            raise TransportError(f"transport failure for {url!r}: {exc}") from exc

        headers = {k: v for k, v in response.getheaders()}
        status = response.status

        def read(limit: int) -> bytes:
            try:
                data = response.read(limit) if limit > 0 else b""
            except TimeoutError as exc:
                raise FetchTimeoutError(f"reading {url!r} timed out after {timeout}s") from exc
            finally:
                conn.close()
            return data

        return status, headers, read


__all__ = (
    "CLOUD_METADATA_IPS",
    "DEFAULT_ALLOWED_CONTENT_TYPES",
    "FetchError",
    "NetworkDisabledError",
    "DisallowedSchemeError",
    "DisallowedHostError",
    "BlockedAddressError",
    "DisallowedContentTypeError",
    "ResponseTooLargeError",
    "FetchTimeoutError",
    "TooManyRedirectsError",
    "TransportError",
    "FetchPolicy",
    "FetchResult",
    "Fetcher",
    "Resolver",
    "OfflineFetcher",
    "FixtureFetcher",
    "FixtureResponse",
    "SafeFetcher",
    "default_fetcher",
    "host_matches_allowlist",
    "is_blocked_address",
    "validate_content_type",
    "screen_url",
)
