"""Edge security surface (T-5): body caps, Host allowlist, Origin/CSRF,
and response security headers — the half of the edge middleware that had
zero tests (the export budget and upload-path classification are covered
in tests/test_security_fixes.py).

Drives EdgeMiddleware with the same minimal raw-ASGI harness as
test_security_fixes.py (the venv has no TestClient; the httpx suite in
tests/test_http_smoke.py covers the app, this file covers the edge).
Settings are patched per test with monkeypatch; EdgeMiddleware reads
them in __init__, so a fresh middleware is built after each patch.
"""
import asyncio


from src.config import settings
from src.edge import EdgeMiddleware

_MIB = 1024 * 1024


def asgi_request(app, method, path, headers=None, body=b""):
    """Minimal raw-ASGI drive: returns (status, headers dict, body bytes)."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    sent = []
    request_messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return request_messages.pop(0) if request_messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    out_headers = {}
    for m in sent:
        if m["type"] == "http.response.start":
            for k, v in m["headers"]:
                out_headers[k.decode()] = v.decode()
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, out_headers, payload


async def _dummy_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


async def _reading_dummy_app(scope, receive, send):
    """Reads (and discards) the whole request body before answering —
    the streaming body cap only fires when the app actually reads."""
    while True:
        message = await receive()
        if message["type"] != "http.request" or not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


def _middleware(monkeypatch, **overrides):
    for name, value in overrides.items():
        monkeypatch.setattr(settings, name, value)
    return EdgeMiddleware(_dummy_app)


def _host_headers(host="testserver", **extra):
    return {"host": host, **extra}


class TestBodyCaps:
    def test_oversized_json_body_413(self, monkeypatch):
        app = _middleware(monkeypatch)
        body = b"x" * (_MIB + 1)
        status, headers, payload = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers={**_host_headers(), "content-length": str(len(body))}, body=body)
        assert status == 413
        assert "too large" in payload.decode().lower()

    def test_body_just_under_cap_passes(self, monkeypatch):
        app = _middleware(monkeypatch)
        body = b"x" * (_MIB - 1)
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers={**_host_headers(), "content-length": str(len(body))}, body=body)
        assert status == 200

    def test_oversized_upload_path_body_413(self, monkeypatch):
        app = _middleware(monkeypatch)
        body = b"x" * (12 * _MIB + 1)
        status, _, _ = asgi_request(
            app, "POST", "/api/ceo/submit-report",
            headers={**_host_headers(), "content-length": str(len(body))}, body=body)
        assert status == 413

    def test_upload_path_body_between_caps_passes(self, monkeypatch):
        # 2 MB: over the 1 MB default cap, under the 12 MB upload cap.
        app = _middleware(monkeypatch)
        body = b"x" * (2 * _MIB)
        status, _, _ = asgi_request(
            app, "POST", "/api/ceo/submit-report",
            headers={**_host_headers(), "content-length": str(len(body))}, body=body)
        assert status == 200

    def test_streamed_body_over_cap_413_without_content_length(self, monkeypatch):
        # No Content-Length: the cap fires while the app streams the body.
        app = EdgeMiddleware(_reading_dummy_app)
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers=_host_headers(), body=b"x" * (2 * _MIB))
        assert status == 413

    def test_streamed_body_under_cap_passes(self, monkeypatch):
        app = EdgeMiddleware(_reading_dummy_app)
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers=_host_headers(), body=b"x" * 1024)
        assert status == 200


class TestHostAllowlist:
    def test_disallowed_host_400(self, monkeypatch):
        app = _middleware(monkeypatch, ALLOWED_HOSTS=["app.example.com"])
        status, _, payload = asgi_request(
            app, "GET", "/api/ops/notifications", headers=_host_headers("evil.example.com"))
        assert status == 400
        assert "Invalid host" in payload.decode()

    def test_allowed_host_passes(self, monkeypatch):
        app = _middleware(monkeypatch, ALLOWED_HOSTS=["app.example.com"])
        status, _, _ = asgi_request(
            app, "GET", "/api/ops/notifications", headers=_host_headers("app.example.com"))
        assert status == 200

    def test_healthz_exempt_from_host_check(self, monkeypatch):
        app = _middleware(monkeypatch, ALLOWED_HOSTS=["app.example.com"])
        status, _, _ = asgi_request(
            app, "GET", "/healthz", headers=_host_headers("evil.example.com"))
        assert status == 200

    def test_no_allowlist_means_no_enforcement(self, monkeypatch):
        app = _middleware(monkeypatch, ALLOWED_HOSTS=[])
        status, _, _ = asgi_request(
            app, "GET", "/api/ops/notifications", headers=_host_headers("anything.example.com"))
        assert status == 200


class TestOriginCsrf:
    def test_cross_site_post_with_foreign_origin_403(self, monkeypatch):
        app = _middleware(monkeypatch)
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers=_host_headers(origin="https://evil.example.com"), body=b"{}")
        assert status == 403

    def test_same_origin_post_passes(self, monkeypatch):
        app = _middleware(monkeypatch)
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers=_host_headers(origin="http://testserver"), body=b"{}")
        assert status == 200

    def test_cross_site_fetch_site_without_origin_403(self, monkeypatch):
        # No Origin/Referer, but the browser says cross-site: blocked.
        app = _middleware(monkeypatch)
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers=_host_headers(**{"sec-fetch-site": "cross-site"}), body=b"{}")
        assert status == 403

    def test_no_origin_no_fetch_site_passes(self, monkeypatch):
        # curl / server-to-server callers send neither and carry no
        # ambient cookie: allowed.
        app = _middleware(monkeypatch)
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses", headers=_host_headers(), body=b"{}")
        assert status == 200

    def test_null_origin_403(self, monkeypatch):
        app = _middleware(monkeypatch)
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers=_host_headers(origin="null"), body=b"{}")
        assert status == 403

    def test_allowed_origin_passes(self, monkeypatch):
        app = _middleware(monkeypatch, ALLOWED_ORIGINS=["https://app.example.com"])
        status, _, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers=_host_headers("app.example.com", origin="https://app.example.com"), body=b"{}")
        assert status == 200

    def test_get_requests_skip_origin_check(self, monkeypatch):
        app = _middleware(monkeypatch)
        status, _, _ = asgi_request(
            app, "GET", "/api/ops/notifications",
            headers=_host_headers(origin="https://evil.example.com"))
        assert status == 200


class TestSecurityHeaders:
    def test_api_response_has_csp_and_no_store(self, monkeypatch):
        app = _middleware(monkeypatch)
        _, headers, _ = asgi_request(
            app, "GET", "/api/ops/notifications", headers=_host_headers())
        assert "content-security-policy" in headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert headers["cache-control"] == "no-store"
        assert headers["x-frame-options"] == "DENY"

    def test_prod_mode_adds_hsts_and_upgrade_insecure(self, monkeypatch):
        app = _middleware(monkeypatch, IS_PRODUCTION=True)
        _, headers, _ = asgi_request(
            app, "GET", "/api/ops/notifications", headers=_host_headers())
        assert headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
        assert "upgrade-insecure-requests" in headers["content-security-policy"]
        assert headers["cache-control"] == "no-store"

    def test_non_prod_mode_has_no_hsts_and_no_upgrade(self, monkeypatch):
        app = _middleware(monkeypatch, IS_PRODUCTION=False)
        _, headers, _ = asgi_request(
            app, "GET", "/api/ops/notifications", headers=_host_headers())
        assert "strict-transport-security" not in headers
        assert "upgrade-insecure-requests" not in headers["content-security-policy"]

    def test_rejection_responses_also_carry_security_headers(self, monkeypatch):
        app = _middleware(monkeypatch, IS_PRODUCTION=True)
        _, headers, _ = asgi_request(
            app, "POST", "/api/ops/expenses",
            headers=_host_headers(origin="https://evil.example.com"), body=b"{}")
        assert headers["content-security-policy"].startswith("default-src 'self'")
        assert headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
