"""Web-layer hardening as a single pure-ASGI middleware.

Runs outermost (see main.py), so it sees every request before routing,
authentication or multipart parsing and every response after gzip. It is
deliberately NOT a BaseHTTPMiddleware: that class buffers/copies bodies and
would defeat the streaming size cap below.

Per request, in order:
  1. Host allowlist          (settings.ALLOWED_HOSTS; /healthz exempt)
  2. Origin / CSRF check     (POST/PUT/PATCH/DELETE only)
  3. Request body size cap   (Content-Length up front + counted while streaming)
  4. Export rate budget      (expensive report/export paths only)
  5. the app, with security headers + Cache-Control applied to its response,
     and any unhandled exception turned into a generic JSON 500.
"""
import asyncio
import json
import logging
import re
import secrets
import time
from collections import deque
from urllib.parse import urlsplit

from starlette.requests import Request

from src.auth import decode_access_token
from src.client_ip import get_client_ip
from src.config import settings

_error_log = logging.getLogger("business_ops_tracker.error")

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_MIB = 1024 * 1024
_DEFAULT_BODY_CAP = 1 * _MIB
_UPLOAD_BODY_CAP = 12 * _MIB
# Every route that accepts an UploadFile must appear here (exact path) or in
# _UPLOAD_PATH_PATTERNS, with this cap at or above the route's own app-side
# limit — otherwise the 1 MB default cap rejects legitimate uploads before
# the app ever sees them (audit finding L5: a 2 MB receipt photo got a 413
# although the receipt endpoint allows 10 MB). tests/test_security_fixes.py
# enumerates the UploadFile routes and asserts each one is covered.
_UPLOAD_PATHS = {
    "/api/ceo/submit-report",   # attachment, app limit 10 MB
    "/api/ceo/profile",         # profile photo, app limit 5 MB
    "/api/ho/submit-report",    # attachment, app limit 10 MB
    "/api/accounting/opening-balance",  # Excel import, no app-side limit
}
_UPLOAD_PATH_PATTERNS = (
    re.compile(r"^/api/ops/expenses/\d+/receipt$"),  # receipt, app limit 10 MB
)


def is_upload_path(path: str) -> bool:
    return path in _UPLOAD_PATHS or any(pattern.match(path) for pattern in _UPLOAD_PATH_PATTERNS)


_EXPORT_PATH_PATTERNS = (
    re.compile(r"^/api/ops/sales-customers/\d+/statement$"),
    re.compile(r"^/api/ops/payroll-runs/\d+/lines/\d+/payslip$"),
)


def is_export_path(path: str) -> bool:
    """The expensive report/export endpoints covered by the per-caller edge
    budget (audit finding L2): every accounting report, any path containing
    /export, the monthly chairman PDF, customer statements and payslip PDFs.
    Normal API/dashboard traffic is deliberately NOT matched."""
    return (
        path.startswith("/api/accounting/reports/")
        or "/export" in path
        or path == "/api/ops/monthly-report"
        or any(pattern.match(path) for pattern in _EXPORT_PATH_PATTERNS)
    )

# After an early rejection the unread request body is read and discarded
# (bounded) before the connection closes. Closing a socket that still has
# unread inbound data makes the OS answer with a reset, which can destroy the
# 4xx the client has not read yet — the client then sees a dropped connection
# instead of the JSON error.
_DRAIN_MAX_BYTES = 32 * _MIB
_DRAIN_TIMEOUT_SECONDS = 10.0

_NO_STORE = b"no-store"
_IMMUTABLE = b"public, max-age=31536000, immutable"
_NO_CACHE = b"no-cache"

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_of(url: str) -> str | None:
    """Reduce a URL (an Origin or Referer value) to a lowercase
    'scheme://host[:port]' with the scheme's default port removed. Returns
    None for anything that is not a plain http(s) origin: 'null', junk, other
    schemes, or URLs carrying userinfo."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port = parts.port
        if parts.username is not None or parts.password is not None:
            return None
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    return _join_origin(scheme, host, port)


def _join_origin(scheme: str, host: str, port: int | None) -> str:
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    if port and port != _DEFAULT_PORTS[scheme]:
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def _request_host(value: str) -> str:
    """Hostname of a Host header: lowercase, port and trailing dot removed."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.partition(":")[0].rstrip(".")


def _host_allowed(host: str, exact: set[str], wildcard_suffixes: list[str], allow_any: bool) -> bool:
    if allow_any or host in exact:
        return True
    return any(host.endswith(suffix) and len(host) > len(suffix) for suffix in wildcard_suffixes)


def build_csp(production: bool, image_sources: list[str]) -> str:
    """Content-Security-Policy for the SPA.

    The built bundle has no inline script and no eval, so script-src is
    'self' only. style-src keeps 'unsafe-inline' because apexcharts injects a
    <style> element and inline SVG styles; that can only enable CSS injection,
    never script execution. Google Fonts is loaded by index.html. blob: is the
    profile-photo preview, data: is apexcharts' export. img-src also lists the
    R2 origins that serve uploaded photos/attachments."""
    origins: list[str] = []
    for raw in image_sources:
        if not raw:
            continue
        origin = _origin_of(raw)
        if origin and origin not in origins:
            origins.append(origin)
    img_src = " ".join(["'self'", "data:", "blob:", *origins])
    policy = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        f"img-src {img_src}; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )
    if production:
        policy += "; upgrade-insecure-requests"
    return policy


class _BodyTooLarge(Exception):
    """Raised out of the wrapped `receive` once a streamed body passes its cap."""


class _RequestState:
    __slots__ = ("started", "exceeded", "suppress", "body_complete")

    def __init__(self) -> None:
        self.started = False  # a response start message has gone to the server
        self.exceeded = False  # the body cap was hit while streaming
        self.suppress = False  # drop whatever the app tries to send from now on
        self.body_complete = False  # the last request body chunk has been read


class EdgeMiddleware:
    def __init__(self, app) -> None:
        self.app = app

        security = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"cross-origin-resource-policy", b"same-origin"),
            (b"x-robots-tag", b"noindex, nofollow"),
        ]
        if settings.IS_PRODUCTION:
            security.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        # Unknown CSP_MODE values fail safe to enforcing.
        csp_mode = settings.CSP_MODE
        if csp_mode != "off":
            csp = build_csp(settings.IS_PRODUCTION, [settings.R2_PUBLIC_BASE_URL, settings.R2_ENDPOINT_URL])
            name = b"content-security-policy-report-only" if csp_mode == "report-only" else b"content-security-policy"
            security.append((name, csp.encode("latin-1")))
        self._security = security
        self._security_names = {name for name, _ in security}

        hosts = [_request_host(h) for h in settings.ALLOWED_HOSTS if h.strip()]
        self._allow_any_host = "*" in hosts
        self._exact_hosts = {h for h in hosts if not h.startswith("*")}
        self._wildcard_suffixes = [h[1:] for h in hosts if h.startswith("*.")]
        self._enforce_hosts = bool(hosts)

        self._allowed_origins = {
            origin for origin in (_origin_of(o) for o in settings.ALLOWED_ORIGINS) if origin
        }

        # Export/report budget state (see _export_budget_retry).
        self._export_limit = settings.EDGE_EXPORT_RATE_LIMIT
        self._export_window = settings.EDGE_EXPORT_RATE_WINDOW
        self._export_hits: dict[str, deque] = {}

        # Write-path budget state (D-10, see _write_budget_retry): a
        # generous per-user/per-minute budget on mutating /api/* calls.
        self._write_limit = settings.EDGE_WRITE_RATE_LIMIT
        self._write_window = settings.EDGE_WRITE_RATE_WINDOW
        self._write_hits: dict[str, deque] = {}
        # Timestamps of 429s per caller, for the sustained-429 warning.
        self._write_429_hits: dict[str, deque] = {}
        self._write_429_warned_at: dict[str, float] = {}

        # Daily upload budget state (D-m7, see _upload_budget_retry): same
        # in-process sliding-window mechanism as the D-10 write budget,
        # keyed by the same caller key, but over a 24 h window on upload
        # paths only. Each entry is (monotonic timestamp, Content-Length
        # bytes) so both a count and a total-size budget are enforced.
        self._upload_count_limit = settings.EDGE_UPLOAD_DAILY_COUNT_LIMIT
        self._upload_bytes_limit = settings.EDGE_UPLOAD_DAILY_BYTES_LIMIT
        self._upload_window = 24 * 60 * 60
        self._upload_hits: dict[str, deque] = {}

    # ------------------------------------------------------------------ ASGI

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        # "//api/x" is not routed as /api/x, but treat it as API for caching
        # and body-cap decisions so a doubled slash can never loosen them.
        path = "/" + scope["path"].lstrip("/")

        headers: dict[bytes, bytes] = {}
        for name, value in scope["headers"]:
            headers.setdefault(name.lower(), value)

        # Only a request that may carry a body has anything to drain when it
        # is rejected before the app has read it.
        unread = receive if method in _UNSAFE_METHODS else None

        # 1. Host allowlist (the container HEALTHCHECK hits 127.0.0.1).
        if self._enforce_hosts and path not in ("/healthz", "/readyz"):
            host = _request_host(headers.get(b"host", b"").decode("latin-1"))
            if not _host_allowed(host, self._exact_hosts, self._wildcard_suffixes, self._allow_any_host):
                await self._reject(send, path, 400, "Invalid host header", unread)
                return

        if method in _UNSAFE_METHODS:
            # 2. Origin / CSRF check.
            if not self._origin_ok(headers):
                await self._reject(send, path, 403, "Cross-origin request blocked", unread)
                return

            # 3. Body size cap — before any framework parsing or auth.
            cap = _UPLOAD_BODY_CAP if is_upload_path(path) else _DEFAULT_BODY_CAP
            length = headers.get(b"content-length")
            if length is not None:
                if not length.strip().isdigit():
                    await self._reject(send, path, 400, "Invalid Content-Length header", unread)
                    return
                if int(length) > cap:
                    await self._reject(send, path, 413, "Request body too large", unread)
                    return
        else:
            cap = 0

        # 4. Export/report rate budget — expensive endpoints only; normal
        # API and dashboard traffic (including the notification polling)
        # never reaches this check.
        if is_export_path(path):
            retry = self._export_budget_retry(scope, headers)
            if retry is not None:
                await self._reject(
                    send,
                    path,
                    429,
                    "Too many report or export requests — please slow down",
                    unread,
                    extra_headers=[(b"retry-after", str(retry).encode("ascii"))],
                )
                return

        # 4b. Write-path rate budget (D-10) — every mutating /api/* call.
        # Generous (120/min per user) so it only stops scripted abuse, never
        # a human. Checked after the origin/body steps so rejected requests
        # don't consume budget.
        if method in _UNSAFE_METHODS and path.startswith("/api/"):
            retry = self._write_budget_retry(scope, headers)
            if retry is not None:
                await self._reject(
                    send,
                    path,
                    429,
                    "Too many write requests — please slow down",
                    unread,
                    extra_headers=[(b"retry-after", str(retry).encode("ascii"))],
                )
                return

        # 4c. Daily upload budget (D-m7) — upload paths only, per caller.
        # Checked after the origin/body/write checks so rejected requests
        # don't consume upload budget; a request that passes the checks
        # consumes one count slot and its Content-Length in bytes.
        if method in _UNSAFE_METHODS and is_upload_path(path):
            retry = self._upload_budget_retry(scope, headers)
            if retry is not None:
                await self._reject(
                    send,
                    path,
                    429,
                    "Daily upload budget exceeded — please slow down",
                    unread,
                    extra_headers=[(b"retry-after", str(retry).encode("ascii"))],
                )
                return

        state = _RequestState()
        received = 0

        async def receive_wrapper():
            nonlocal received
            if state.exceeded:
                raise _BodyTooLarge()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                state.body_complete = not message.get("more_body", False)
                if received > cap:
                    state.exceeded = True
                    raise _BodyTooLarge()
            return message

        async def send_wrapper(message) -> None:
            if state.suppress:
                return
            if message["type"] == "http.response.start":
                if state.exceeded:
                    # The app saw a failed body read and answered with whatever
                    # it makes of that (FastAPI turns it into a 400): replace
                    # it with the real reason and discard the rest of its reply.
                    state.suppress = True
                    state.started = True
                    await self._reject(
                        send, path, 413, "Request body too large", None if state.body_complete else receive
                    )
                    return
                state.started = True
                message = {
                    **message,
                    "headers": self._decorate(path, message["status"], message.get("headers") or []),
                }
            await send(message)

        try:
            await self.app(scope, receive_wrapper if cap else receive, send_wrapper)
        except Exception as exc:
            if state.started and not state.suppress:
                raise  # too late to change the response; let the server drop the connection
            if state.exceeded:
                if not state.started:
                    await self._reject(
                        send, path, 413, "Request body too large", None if state.body_complete else receive
                    )
                return
            error_id = secrets.token_hex(4)
            # Never log the body or headers. %r keeps a hostile path from
            # forging log lines.
            _error_log.error(
                "Unhandled exception error_id=%s %s %r", error_id, method, path, exc_info=exc
            )
            await self._respond(send, path, 500, {"detail": "Internal server error", "error_id": error_id})

    # ---------------------------------------------------------------- checks

    def _origin_ok(self, headers: dict[bytes, bytes]) -> bool:
        raw = headers.get(b"origin")
        if raw is None:
            referer = headers.get(b"referer", b"").decode("latin-1").strip()
            if not referer:
                # curl / server-to-server callers send neither and carry no
                # ambient cookie. A browser saying "cross-site" is the tell.
                fetch_site = headers.get(b"sec-fetch-site", b"").decode("latin-1").strip().lower()
                return fetch_site != "cross-site"
            raw_value = referer
        else:
            raw_value = raw.decode("latin-1")

        origin = _origin_of(raw_value)  # 'null' and junk come back None
        if origin is None:
            return False
        if origin in self._allowed_origins:
            return True
        # Same-origin: compare with the Host header, never X-Forwarded-Host
        # (client-controlled). TLS ends at the proxy so the scheme is not
        # compared; the browser's origin host[:port] must be ours.
        host_header = headers.get(b"host", b"").decode("latin-1").strip()
        if not host_header:
            return False
        # P3-8: when ALLOWED_HOSTS is configured, the same-origin fallback
        # must never trust a Host outside that list — Host is
        # client-controlled unless the reverse proxy overwrites it. The
        # step-1 host check already rejects a bad Host on API paths; this
        # is the defence-in-depth copy inside the CSRF decision itself, so
        # the origin comparison below can never be satisfied by a Host the
        # deployment does not answer to.
        if self._enforce_hosts and not _host_allowed(
            _request_host(host_header),
            self._exact_hosts,
            self._wildcard_suffixes,
            self._allow_any_host,
        ):
            return False
        scheme = origin.partition("://")[0]
        own = _origin_of(f"{scheme}://{host_header}")
        return own is not None and own == origin

    def _caller_key(self, scope, headers: dict[bytes, bytes]) -> str:
        """Who is calling, for the per-caller edge budgets. Keyed by the
        session username when the cookie carries a token that verifies —
        decoded with the same function (and the same pinned algorithm and
        required claims) the auth layer uses — else by client IP, resolved
        by the very same helper the throttles use so the two never disagree
        about who called. A forged cookie therefore only ever budgets the
        caller's own IP bucket, never someone else's. The key is an
        identifier for budgeting only, never authentication."""
        token = None
        for part in headers.get(b"cookie", b"").decode("latin-1").split(";"):
            name, _, value = part.strip().partition("=")
            if name == settings.COOKIE_NAME:
                token = value
                break
        if token:
            payload = decode_access_token(token)
            if payload and isinstance(payload.get("sub"), str):
                return f"user:{payload['sub']}"
        return f"ip:{get_client_ip(Request(scope)) or 'unknown'}"

    def _export_budget_retry(self, scope, headers: dict[bytes, bytes]) -> int | None:
        """Records one hit against the caller's export budget; returns the
        Retry-After seconds when over budget, else None.

        Sliding window held in process: the deploy is a single container,
        so per-process state is exact (if that ever changes, move this to
        the database like the auth throttles in src/throttle.py)."""
        key = self._caller_key(scope, headers)

        now = time.monotonic()
        hits = self._export_hits.setdefault(key, deque())
        cutoff = now - self._export_window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            # Evict the empty bucket so the map cannot grow without bound:
            # a caller whose window has fully expired leaves no entry
            # behind. (The current hit is recorded on the deque below, so
            # the key returns with exactly one entry.)
            self._export_hits.pop(key, None)
        if len(hits) >= self._export_limit:
            return max(1, int(hits[0] + self._export_window - now) + 1)
        hits.append(now)
        self._export_hits.setdefault(key, hits)
        return None

    # How many 429s from one authenticated caller inside the write window
    # count as "sustained" and earn a log line (D-10). A single burst that
    # trips the budget once is not, on its own, worth an alert.
    _WRITE_429_SUSTAINED_COUNT = 3

    def _write_budget_retry(self, scope, headers: dict[bytes, bytes]) -> int | None:
        """Same sliding-window mechanism as the export budget, for mutating
        /api/* calls (D-10). Returns Retry-After seconds when over budget,
        else None. Sustained 429s from an authenticated user are logged
        (at most one warning per caller per window) so a compromised
        account hammering the write path is visible in the logs."""
        key = self._caller_key(scope, headers)

        now = time.monotonic()
        hits = self._write_hits.setdefault(key, deque())
        cutoff = now - self._write_window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            self._write_hits.pop(key, None)
        if len(hits) >= self._write_limit:
            if key.startswith("user:"):
                recent_429s = self._write_429_hits.setdefault(key, deque())
                while recent_429s and recent_429s[0] <= cutoff:
                    recent_429s.popleft()
                recent_429s.append(now)
                # "Never warned" must not suppress the first warning: time.monotonic()
                # is uptime-based, so a default of 0.0 would silence the first
                # sustained burst for a whole window after every fresh start.
                warned_at = self._write_429_warned_at.get(key, float("-inf"))
                if (
                    len(recent_429s) >= self._WRITE_429_SUSTAINED_COUNT
                    and now - warned_at >= self._write_window
                ):
                    _error_log.warning(
                        "Sustained write rate limiting for %s: %d write 429s in %ds",
                        key, len(recent_429s), self._write_window,
                    )
                    self._write_429_warned_at[key] = now
            return max(1, int(hits[0] + self._write_window - now) + 1)
        hits.append(now)
        self._write_hits.setdefault(key, hits)
        return None

    def _upload_budget_retry(self, scope, headers: dict[bytes, bytes]) -> int | None:
        """Records one upload against the caller's daily upload budget
        (D-m7); returns Retry-After seconds when over budget, else None.

        Same sliding-window mechanism as the D-10 write budget
        (_write_budget_retry), keyed by the same caller key, but over a
        24 h window and enforcing both a count limit and a total-bytes
        limit. Size is the request's Content-Length, which the body-cap
        step already validated (absent/invalid counts as 0 — the cap step
        rejects a non-numeric Content-Length before this runs). Empty
        buckets are evicted so the map cannot grow without bound.
        """
        key = self._caller_key(scope, headers)

        now = time.monotonic()
        try:
            size = int((headers.get(b"content-length") or b"0").decode("latin-1").strip())
        except ValueError:
            size = 0
        if size < 0:
            size = 0
        hits = self._upload_hits.setdefault(key, deque())
        cutoff = now - self._upload_window
        while hits and hits[0][0] <= cutoff:
            hits.popleft()
        if not hits:
            self._upload_hits.pop(key, None)
        total_bytes = sum(entry[1] for entry in hits)
        if len(hits) >= self._upload_count_limit or total_bytes + size > self._upload_bytes_limit:
            if hits:
                return max(1, int(hits[0][0] + self._upload_window - now) + 1)
            return self._upload_window
        hits.append((now, size))
        self._upload_hits.setdefault(key, hits)
        return None

    # -------------------------------------------------------------- headers

    def _decorate(self, path: str, status: int, raw_headers) -> list[tuple[bytes, bytes]]:
        if path in ("/healthz", "/readyz") or path == "/api" or path.startswith("/api/"):
            forced: bytes | None = _NO_STORE
        elif path.startswith("/assets/") and (200 <= status < 300 or status == 304):
            forced = _IMMUTABLE  # hashed filenames, safe to cache forever
        else:
            forced = None

        out: list[tuple[bytes, bytes]] = []
        has_cache_control = False
        for name, value in raw_headers:
            lowered = name.lower()
            if lowered in self._security_names:
                continue
            if lowered == b"cache-control":
                if forced is not None:
                    continue
                has_cache_control = True
            out.append((name, value))
        out.extend(self._security)
        if forced is not None:
            out.append((b"cache-control", forced))
        elif not has_cache_control:
            out.append((b"cache-control", _NO_CACHE))  # index.html etc.
        return out

    # ------------------------------------------------------------- responses

    async def _reject(self, send, path: str, status: int, detail: str, drain=None, extra_headers=()) -> None:
        """Send a JSON error and close the connection. `drain` is the raw
        `receive` when part of the request body is still unread."""
        await self._respond(send, path, status, {"detail": detail}, close=True, drain=drain, extra_headers=extra_headers)

    async def _respond(self, send, path: str, status: int, payload: dict, close: bool = False, drain=None, extra_headers=()) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        raw_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", _NO_STORE),
            *extra_headers,
        ]
        if close:
            raw_headers.append((b"connection", b"close"))
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": self._decorate(path, status, raw_headers),
            }
        )
        if drain is None:
            await send({"type": "http.response.body", "body": body})
            return
        # Deliver the whole reply now, but hold the response open (the server
        # closes the socket when it completes) until the client has finished
        # sending, so the close is a clean FIN rather than a reset.
        await send({"type": "http.response.body", "body": body, "more_body": True})
        await self._drain(drain)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    @staticmethod
    async def _drain(receive) -> None:
        drained = 0

        async def read_rest() -> None:
            nonlocal drained
            while drained < _DRAIN_MAX_BYTES:
                message = await receive()
                if message["type"] != "http.request":
                    return  # client went away
                drained += len(message.get("body", b""))
                if not message.get("more_body", False):
                    return

        try:
            await asyncio.wait_for(read_rest(), _DRAIN_TIMEOUT_SECONDS)
        except Exception:  # timeout / receive failure: give up and close
            pass
