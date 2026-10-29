"""Rules for URLs the kit fetches but did not hard-code.

A URL taken from a provider response, a config file or a CLI argument is untrusted data:
it must be http(s) with a host and no embedded credentials, and (for response-supplied
URLs) must not point at the machine's own or a private network, where it could reach a
metadata service or a local daemon.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable


def require_http_url(url: str) -> urllib.parse.SplitResult:
    """The parsed URL, or ``ValueError`` unless it is http(s) with a host and no userinfo."""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("URL must be http(s) with a host and no embedded credentials")
    return parsed


# IPv6 ranges that carry an IPv4 address: a NAT64 gateway or a translator would deliver the
# request to that IPv4 host, so none of them can be allowed on the strength of `is_global`.
_EMBEDDING_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in ("64:ff9b::/96", "64:ff9b:1::/48", "::/96", "::ffff:0:0:0/96")
)


def is_internal_host(host: str) -> bool:
    """True if any address ``host`` resolves to is not a public, routable address.

    A name that does not resolve is not reported as internal: the fetch will fail on its
    own. The check and the later connection resolve separately, so DNS rebinding between
    them is not covered.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if not ip.is_global or ip.is_multicast:
            return True
        if ip.version == 6 and any(ip in net for net in _EMBEDDING_NETWORKS):
            return True
    return False


Origin = tuple[str, int]  # (lower-case host, port)


def origin_of(url: str | None) -> Origin | None:
    """The (host, port) of an http(s) URL, with the scheme's default port filled in."""
    if not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not host or parts.scheme not in ("http", "https"):
        return None
    return host.lower(), port or (443 if parts.scheme == "https" else 80)


def require_public_url(
    url: str, allow_origins: Iterable[Origin | None] = ()
) -> urllib.parse.SplitResult:
    """``require_http_url`` plus: the host must not be internal unless its origin is allowed.

    ``allow_origins`` holds the origins the operator configured (an adapter's own base URL),
    so a LAN-hosted provider keeps working while a response cannot steer the fetch to another
    host, or to another port of that host.
    """
    parsed = require_http_url(url)
    host = (parsed.hostname or "").lower()
    if origin_of(url) not in {o for o in allow_origins if o} and is_internal_host(host):
        raise ValueError(f"URL resolves to an internal address: {host}")
    return parsed


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError(
            "redirects are not followed for authenticated requests"
        )


def open_without_redirects(req: urllib.request.Request, timeout: float):
    """Open an http(s) request that carries credentials, following no redirect.

    The stock opener replays request headers, ``Authorization`` included, to whatever host a
    redirect names. It also has ``file:`` and ``ftp:`` handlers; this one speaks http(s) only.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        _RefuseRedirects(),
        urllib.request.UnknownHandler(),  # without it a file: URL returns None, not an error
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener.open(req, timeout=timeout)
