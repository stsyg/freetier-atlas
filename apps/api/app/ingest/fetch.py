"""Safe source fetching: the sole network seam for the ingestion pipeline.

Every adapter reaches the network *only* through a :class:`Fetcher`. This module
splits the fetch guard into two layers:

* **Pure policy functions** -- ``check_scheme``, ``host_is_allowlisted``,
  ``address_block_reason``, ``validate_mime``, ``check_redirect_budget`` and
  ``check_size`` -- take plain values and raise a typed :class:`FetchError` (or
  return a decision) with no I/O whatsoever, so every rule is independently
  unit-testable without a socket.
* **A thin I/O layer** -- :class:`OfflineFetcher` (the safe default, which never
  opens a socket), :class:`LiveFetcher` (a stdlib ``urllib`` transport gated
  behind an explicit ``enable_network`` flag that is **disabled by default**),
  and :class:`FixtureFetcher` (a deterministic offline test transport). The I/O
  layer only sequences the pure policy checks around the actual bytes.

Defence in depth against SSRF and egress abuse (docs/SECURITY_PRIVACY_ABUSE.md
"Source fetching"): an official-domain allowlist, private-network/metadata IP
blocking, an https-only scheme allowlist, MIME validation, a bounded redirect
count with the allowlist *and* SSRF checks re-run on **every** hop, connect/read
timeouts, and a streamed maximum-size cap that aborts early. No new runtime
dependency: everything here is the Python standard library.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

# --- Defaults --------------------------------------------------------------

DEFAULT_ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})

# A conservative set of official-source content types (structured data, feeds,
# and documentation). Deliberately excludes executables/archives/octet-stream.
DEFAULT_ALLOWED_MIME_TYPES: tuple[str, ...] = (
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)

DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0
_STREAM_CHUNK = 65_536


# --- Typed errors ----------------------------------------------------------


class FetchError(Exception):
    """Base class for every fetch-guard rejection.

    ``reason`` is a stable machine-readable code; the message is human readable.
    """

    reason = "fetch_error"


class NetworkDisabledError(FetchError):
    """Raised when a live network fetch is attempted but not enabled."""

    reason = "network_disabled"


class DisallowedSchemeError(FetchError):
    """URL scheme is not in the allowlist (default: https only)."""

    reason = "disallowed_scheme"


class DisallowedHostError(FetchError):
    """Host is not covered by the provider's official-domain allowlist."""

    reason = "disallowed_host"


class BlockedAddressError(FetchError):
    """Host resolves to a private, loopback, link-local or metadata address."""

    reason = "blocked_address"


class DisallowedMimeError(FetchError):
    """Response MIME type is not in the allowlist."""

    reason = "disallowed_mime"


class TooManyRedirectsError(FetchError):
    """Redirect chain exceeded the configured budget."""

    reason = "too_many_redirects"


class ResponseTooLargeError(FetchError):
    """Response body exceeded the configured maximum size."""

    reason = "response_too_large"


class FetchTimeoutError(FetchError):
    """The connect/read budget elapsed before the response completed."""

    reason = "timeout"


class InvalidRedirectError(FetchError):
    """A redirect response was missing or had an unusable Location header."""

    reason = "invalid_redirect"


class NotFoundError(FetchError):
    """A fixture/offline transport has no content for the requested URL."""

    reason = "not_found"


# --- Configuration ---------------------------------------------------------


@dataclass(frozen=True)
class FetchPolicy:
    """The network policy a fetcher enforces.

    ``official_domains`` is the provider's approved-domain allowlist; a request
    host must equal one of them or be a subdomain of one. ``allow_loopback`` is
    an escape hatch used **only** by the loopback unit test (default ``False``)
    so that no test performs external network egress.
    """

    official_domains: tuple[str, ...] = ()
    allowed_schemes: frozenset[str] = DEFAULT_ALLOWED_SCHEMES
    allowed_mime_types: tuple[str, ...] = DEFAULT_ALLOWED_MIME_TYPES
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    max_bytes: int = DEFAULT_MAX_BYTES
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    allow_loopback: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "official_domains", tuple(normalise_host(d) for d in self.official_domains)
        )
        object.__setattr__(
            self, "allowed_schemes", frozenset(s.lower() for s in self.allowed_schemes)
        )


@dataclass(frozen=True)
class FetchResult:
    """A successfully fetched, policy-validated document."""

    content: bytes
    mime: str
    final_url: str
    content_hash: str
    fetched_at: datetime
    status: int = 200

    @property
    def text(self) -> str:
        """Decode the body as UTF-8 (best effort, replacing undecodable bytes)."""

        return self.content.decode("utf-8", errors="replace")


# --- Fetcher protocol ------------------------------------------------------


@runtime_checkable
class Fetcher(Protocol):
    """The network seam. Adapters depend on this, never on an HTTP client."""

    def fetch(self, url: str) -> FetchResult:  # pragma: no cover - protocol
        ...


# --- Pure policy functions -------------------------------------------------


def normalise_host(host: str) -> str:
    """Lowercase a host and strip a single trailing dot."""

    return host.strip().rstrip(".").lower()


def check_scheme(url: str, allowed_schemes: Iterable[str]) -> str:
    """Return the URL scheme if allowed, else raise :class:`DisallowedSchemeError`."""

    scheme = urlsplit(url).scheme.lower()
    allowed = {s.lower() for s in allowed_schemes}
    if scheme not in allowed:
        raise DisallowedSchemeError(
            f"URL scheme '{scheme or '(none)'}' is not allowed; permitted: {sorted(allowed)}."
        )
    return scheme


def host_is_allowlisted(host: str, official_domains: Iterable[str]) -> bool:
    """True if ``host`` equals or is a subdomain of an allowlisted domain."""

    h = normalise_host(host)
    if not h:
        return False
    for domain in official_domains:
        d = normalise_host(domain)
        if h == d or h.endswith("." + d):
            return True
    return False


def check_host(url: str, official_domains: Iterable[str]) -> str:
    """Return the URL host if allowlisted, else raise :class:`DisallowedHostError`.

    This is evaluated *before* any DNS resolution or socket use so a
    non-allowlisted host can never trigger a connection.
    """

    host = urlsplit(url).hostname or ""
    if not host_is_allowlisted(host, official_domains):
        raise DisallowedHostError(
            f"Host '{host or '(none)'}' is not in the official-domain allowlist."
        )
    return normalise_host(host)


def address_block_reason(ip: str, *, allow_loopback: bool = False) -> str | None:
    """Return a block reason for ``ip`` if it is unsafe to connect to, else ``None``.

    Blocks loopback, RFC1918 private ranges, link-local (169.254.0.0/16 incl.
    the 169.254.169.254 cloud-metadata address, and IPv6 fe80::/10), unique local
    addresses (fc00::/7), the unspecified address, reserved/multicast ranges, and
    unmasks IPv4-mapped IPv6 so an attacker cannot smuggle a private v4 address.
    """

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return f"'{ip}' is not a valid IP address."

    # Unmask IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) so it is judged as the v4.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped

    if addr.is_loopback:
        if allow_loopback:
            return None
        return f"'{ip}' is a loopback address."
    if addr.is_unspecified:
        return f"'{ip}' is the unspecified address."
    if addr.is_link_local:
        # Covers 169.254.0.0/16 (incl. 169.254.169.254 metadata) and fe80::/10.
        return f"'{ip}' is a link-local address (blocks cloud metadata)."
    if addr.is_private:
        # Covers RFC1918 (10/8, 172.16/12, 192.168/16) and IPv6 ULA fc00::/7.
        return f"'{ip}' is a private address."
    if addr.is_multicast:
        return f"'{ip}' is a multicast address."
    if addr.is_reserved:
        return f"'{ip}' is a reserved address."
    return None


def check_addresses(addresses: Iterable[str], *, allow_loopback: bool = False) -> tuple[str, ...]:
    """Raise :class:`BlockedAddressError` if any resolved address is unsafe.

    Returns the validated addresses as a tuple. An empty address set is itself an
    error (nothing safe to connect to).
    """

    addrs = tuple(addresses)
    if not addrs:
        raise BlockedAddressError("Host did not resolve to any address.")
    for ip in addrs:
        reason = address_block_reason(ip, allow_loopback=allow_loopback)
        if reason is not None:
            raise BlockedAddressError(f"Refusing to connect: {reason}")
    return addrs


def parse_mime(content_type: str | None) -> str:
    """Extract the bare lowercased MIME type from a Content-Type header value."""

    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def validate_mime(content_type: str | None, allowed_mime_types: Iterable[str]) -> str:
    """Return the bare MIME type if allowed, else raise :class:`DisallowedMimeError`."""

    mime = parse_mime(content_type)
    allowed = {m.lower() for m in allowed_mime_types}
    if mime not in allowed:
        raise DisallowedMimeError(
            f"Response MIME '{mime or '(none)'}' is not allowed; permitted: {sorted(allowed)}."
        )
    return mime


def check_redirect_budget(redirect_count: int, max_redirects: int) -> None:
    """Raise :class:`TooManyRedirectsError` if the redirect budget is exceeded."""

    if redirect_count > max_redirects:
        raise TooManyRedirectsError(f"Exceeded the maximum of {max_redirects} redirect(s).")


def check_size(byte_count: int, max_bytes: int) -> None:
    """Raise :class:`ResponseTooLargeError` if ``byte_count`` exceeds the cap."""

    if byte_count > max_bytes:
        raise ResponseTooLargeError(f"Response exceeded the maximum size of {max_bytes} bytes.")


def content_hash(content: bytes) -> str:
    """Return the SHA-256 hex digest of ``content`` (stable provenance hash)."""

    return hashlib.sha256(content).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_request_url(url: str, policy: FetchPolicy) -> None:
    """Run the pre-connection URL policy checks (scheme + host allowlist)."""

    check_scheme(url, policy.allowed_schemes)
    check_host(url, policy.official_domains)


# --- Transports ------------------------------------------------------------


class OfflineFetcher:
    """The safe default fetcher: it never opens a socket.

    Calling :meth:`fetch` always raises :class:`NetworkDisabledError`. Use this
    wherever a :class:`Fetcher` is required but live network access is not
    explicitly enabled, so the default posture of the system is "no egress".
    """

    def __init__(self, policy: FetchPolicy | None = None) -> None:
        self.policy = policy or FetchPolicy()

    def fetch(self, url: str) -> FetchResult:
        raise NetworkDisabledError(
            "Live network fetching is disabled. Use a LiveFetcher(enable_network=True) "
            "or a FixtureFetcher for offline content."
        )


class FixtureFetcher:
    """A deterministic, offline test transport backed by an in-memory map.

    It still runs the *pure* URL policy checks (scheme + host allowlist) and MIME
    validation, so an adapter exercised against fixtures behaves exactly as it
    would against the live transport -- but no socket is ever opened. Content is
    keyed by URL; unknown URLs raise :class:`NotFoundError`.
    """

    def __init__(
        self,
        fixtures: Mapping[str, tuple[bytes, str]],
        policy: FetchPolicy | None = None,
        *,
        enforce_policy: bool = True,
    ) -> None:
        self._fixtures = dict(fixtures)
        self.policy = policy or FetchPolicy()
        self._enforce_policy = enforce_policy

    def fetch(self, url: str) -> FetchResult:
        if self._enforce_policy:
            _validate_request_url(url, self.policy)
        if url not in self._fixtures:
            raise NotFoundError(f"No fixture registered for '{url}'.")
        content, declared_mime = self._fixtures[url]
        mime = (
            validate_mime(declared_mime, self.policy.allowed_mime_types)
            if self._enforce_policy
            else parse_mime(declared_mime)
        )
        return FetchResult(
            content=content,
            mime=mime,
            final_url=url,
            content_hash=content_hash(content),
            fetched_at=_now(),
            status=200,
        )


class _NoRedirectNoErrorOpener:
    """Build a urllib opener that neither follows redirects nor raises on 3xx/4xx.

    We manage redirects manually so the allowlist and SSRF checks re-run on every
    hop, and we omit the proxy handler so requests can never be diverted through a
    proxy (another SSRF vector).
    """

    @staticmethod
    def build() -> urllib.request.OpenerDirector:
        opener = urllib.request.OpenerDirector()
        opener.add_handler(urllib.request.HTTPHandler())
        opener.add_handler(urllib.request.HTTPSHandler())
        # Intentionally NO HTTPRedirectHandler (manual redirects), NO
        # HTTPErrorProcessor (3xx/4xx returned as responses, not raised), and NO
        # ProxyHandler (no proxy diversion).
        return opener


class LiveFetcher:
    """A stdlib ``urllib`` transport, disabled by default.

    Constructing with ``enable_network=False`` (the default) makes every
    :meth:`fetch` raise :class:`NetworkDisabledError`, so a live socket is only
    ever possible when a caller *explicitly* opts in. When enabled, each request
    (and each redirect hop) is validated by the pure policy functions before any
    connection, the resolved addresses are SSRF-checked, the body is streamed with
    an early size abort, and connect/read timeouts are enforced.
    """

    def __init__(self, policy: FetchPolicy, *, enable_network: bool = False) -> None:
        self.policy = policy
        self.enable_network = enable_network
        self._opener = _NoRedirectNoErrorOpener.build()

    # -- resolution is the only DNS I/O; kept tiny and override-friendly --
    def _resolve(self, host: str) -> tuple[str, ...]:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        return tuple({info[4][0] for info in infos})

    def fetch(self, url: str) -> FetchResult:
        if not self.enable_network:
            raise NetworkDisabledError(
                "LiveFetcher network access is disabled; construct with "
                "enable_network=True to permit live fetches."
            )

        current = url
        for redirect_count in range(self.policy.max_redirects + 1):
            # 1. Pure pre-connection policy: scheme + official-domain allowlist.
            #    Runs on EVERY hop, so a redirect target is validated too.
            check_scheme(current, self.policy.allowed_schemes)
            host = check_host(current, self.policy.official_domains)
            # 2. SSRF: resolve and reject private/loopback/link-local/metadata.
            #    Re-checked on EVERY hop.
            check_addresses(self._resolve(host), allow_loopback=self.policy.allow_loopback)

            # 3. Perform the request without auto-following redirects.
            response = self._open(current)
            status = getattr(response, "status", None) or response.getcode()

            if status in (301, 302, 303, 307, 308):
                response.close()
                check_redirect_budget(redirect_count + 1, self.policy.max_redirects)
                current = self._redirect_target(current, response)
                continue

            # 4. Validate MIME, then stream the body with an early size cap.
            try:
                mime = validate_mime(
                    response.headers.get("Content-Type"), self.policy.allowed_mime_types
                )
                body = self._read_capped(response)
            finally:
                response.close()

            return FetchResult(
                content=body,
                mime=mime,
                final_url=current,
                content_hash=content_hash(body),
                fetched_at=_now(),
                status=int(status),
            )

        # Loop exhausted without returning: budget exceeded.
        raise TooManyRedirectsError(
            f"Exceeded the maximum of {self.policy.max_redirects} redirect(s)."
        )

    def _open(self, url: str):
        request = urllib.request.Request(url, method="GET")
        try:
            return self._opener.open(request, timeout=self.policy.read_timeout)
        except TimeoutError as exc:
            raise FetchTimeoutError(
                f"Timed out after {self.policy.read_timeout}s fetching '{url}'."
            ) from exc

    def _redirect_target(self, base_url: str, response) -> str:
        location = response.headers.get("Location")
        if not location:
            raise InvalidRedirectError(f"Redirect from '{base_url}' had no Location header.")
        return urljoin(base_url, location)

    def _read_capped(self, response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = response.read(_STREAM_CHUNK)
            except TimeoutError as exc:
                raise FetchTimeoutError(
                    f"Timed out after {self.policy.read_timeout}s reading the response body."
                ) from exc
            if not chunk:
                break
            total += len(chunk)
            # Abort as soon as the cap is crossed -- never buffer the whole body.
            check_size(total, self.policy.max_bytes)
            chunks.append(chunk)
        return b"".join(chunks)


__all__: Sequence[str] = (
    # config / result / protocol
    "FetchPolicy",
    "FetchResult",
    "Fetcher",
    # transports
    "OfflineFetcher",
    "LiveFetcher",
    "FixtureFetcher",
    # pure policy functions
    "normalise_host",
    "check_scheme",
    "host_is_allowlisted",
    "check_host",
    "address_block_reason",
    "check_addresses",
    "parse_mime",
    "validate_mime",
    "check_redirect_budget",
    "check_size",
    "content_hash",
    # defaults
    "DEFAULT_ALLOWED_SCHEMES",
    "DEFAULT_ALLOWED_MIME_TYPES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_MAX_BYTES",
    # errors
    "FetchError",
    "NetworkDisabledError",
    "DisallowedSchemeError",
    "DisallowedHostError",
    "BlockedAddressError",
    "DisallowedMimeError",
    "TooManyRedirectsError",
    "ResponseTooLargeError",
    "FetchTimeoutError",
    "InvalidRedirectError",
    "NotFoundError",
)
