"""
Resolves the real client IP for audit logging and throttling.

The one rule that matters: never trust a value the client can choose. The
left-most X-Forwarded-For entry is whatever the caller typed, so it is never
used. Either the platform's authoritative header is named in CLIENT_IP_HEADER
(e.g. do-connecting-ip, cf-connecting-ip), or X-Forwarded-For is read from the
RIGHT, TRUSTED_PROXY_HOPS entries in — those are the entries appended by
proxies we operate/trust — and otherwise the TCP peer address is used.

Deployment note: run uvicorn with --no-proxy-headers. Its own proxy-header
handling rewrites request.client from X-Forwarded-For (with
--forwarded-allow-ips='*' it picks the LEFT-most, client-controlled entry),
which would make the "peer" fallback below spoofable too.
"""
import ipaddress
from typing import Optional

from fastapi import Request

from src.config import settings


def _valid_ip(value: Optional[str]) -> Optional[str]:
    """Returns the canonical text form of `value` if it is a real IP address,
    else None. Rejects anything else (hostnames, garbage, injected text)."""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _peer_ip(request: Request) -> Optional[str]:
    client = request.client
    return _valid_ip(client.host) if client else None


def _from_named_header(request: Request, header: str) -> Optional[str]:
    raw = request.headers.get(header)
    if not raw:
        return None
    return _valid_ip(raw.split(",")[0])


def _from_forwarded_for(request: Request, hops: int) -> Optional[str]:
    # Several X-Forwarded-For header lines are equivalent to one comma-joined
    # line (RFC 9110); a proxy may append its entry as a new line.
    lines = request.headers.getlist("x-forwarded-for")
    if not lines:
        return None
    entries = [part.strip() for line in lines for part in line.split(",") if part.strip()]
    if len(entries) < hops:
        return None
    return _valid_ip(entries[-hops])


def get_client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    candidate = None
    if settings.CLIENT_IP_HEADER:
        candidate = _from_named_header(request, settings.CLIENT_IP_HEADER)
    elif request.headers.get("x-forwarded-for") is not None:
        candidate = _from_forwarded_for(request, settings.TRUSTED_PROXY_HOPS)
    return candidate or _peer_ip(request)
