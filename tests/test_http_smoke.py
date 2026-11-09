"""HTTP-level smoke tests (T-4): the serving stack, via httpx + ASGITransport.

The venv previously had no httpx/TestClient (see tests/test_security_fixes.py),
so nothing ever hit /healthz, the login cookie, the SPA fallback or the
session-gated /uploads route over HTTP. These tests drive the real FastAPI
app (no lifespan: ASGITransport does not run it, so no migrations or
seeding happen here) with get_db overridden to a private in-memory DB.

Cookie attributes: in test mode the cookie is `access_token` (no __Host-
prefix, Secure off) with HttpOnly + SameSite=lax. The production
attributes (Secure + __Host- prefix) cannot be observed over this
transport without a production database, so they are checked in a
subprocess that builds a production Settings and calls the cookie issuer
directly — see test_prod_cookie_attributes_in_subprocess.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth import hash_password
from src.database import get_db
from src.main import app
from src.models import Base, User

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def http_db(tmp_path):
    """File-backed SQLite DB: FastAPI runs sync endpoints in a worker
    thread, so the in-memory engine (one DB per connection/thread) cannot
    be shared between the fixture thread and the endpoint thread."""
    db_path = tmp_path / "http.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(username="chairman", hashed_password=hash_password("Chairman123!"),
                     role="Chairman", is_active=True, require_password_change=False,
                     is_profile_completed=True))
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def client(http_db):
    """httpx client bound to the app with get_db overridden to http_db."""
    def _override():
        try:
            yield http_db
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    try:
        class SyncClient:
            def request(self, method, url, **kw):
                async def _go():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url="http://testserver") as ac:
                        return await ac.request(method, url, **kw)
                return asyncio.run(_go())

            def get(self, url, **kw):
                return self.request("GET", url, **kw)

            def post(self, url, **kw):
                return self.request("POST", url, **kw)

        yield SyncClient()
    finally:
        app.dependency_overrides.pop(get_db, None)


def _set_cookie_header(response):
    return response.headers.get("set-cookie", "")


class TestHealthAndRouting:
    def test_healthz_200(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_unknown_api_path_returns_json_404_not_index(self, client):
        r = client.get("/api/does-not-exist")
        assert r.status_code == 404
        assert "application/json" in r.headers["content-type"]
        assert "text/html" not in r.headers["content-type"]
        assert r.json()["detail"] == "Not found"

    def test_spa_route_returns_index_html(self, client):
        # frontend/dist is built in this repo; any non-API path serves it.
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "<!doctype html>" in r.text.lower()

    def test_api_responses_carry_no_store(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 401
        assert r.headers.get("cache-control") == "no-store"


class TestLoginCookie:
    def test_login_sets_httponly_lax_cookie(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "chairman", "password": "Chairman123!"})
        assert r.status_code == 200, r.text
        cookie = _set_cookie_header(r)
        assert "access_token=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        # Test mode: Secure is off and there is no __Host- prefix (both
        # are production-only; see the subprocess test below).
        assert "Secure" not in cookie
        assert "__Host-" not in cookie

    def test_login_wrong_password_401_and_no_cookie(self, client):
        r = client.post("/api/auth/login",
                        json={"username": "chairman", "password": "wrong-password"})
        assert r.status_code == 401
        assert "access_token=" not in _set_cookie_header(r)

    def test_prod_cookie_attributes_in_subprocess(self, tmp_path):
        """Production mode: Secure + __Host- prefix + HttpOnly + lax.

        Runs in a subprocess (settings are module-global): builds a
        production Settings, then calls the cookie issuer directly with
        a stub user — no database is needed, the issuer only signs a
        token and sets the cookie. The DATABASE_URL here is a throwaway
        SQLite file: the cookie flags do not depend on the dialect, and
        a Postgres URL currently cannot be imported at all — the
        in-flight D-3 rewrite of src/database.py dropped the `make_url`
        import it still uses (its owner's bug to fix, not this test's).
        """
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENVIRONMENT", "DATABASE_URL", "MIGRATION_DATABASE_URL",
                            "JWT_SECRET_KEY", "DATA_ENCRYPTION_KEY", "DATABASE_SSLMODE",
                            "DATABASE_CA_CERT", "ALLOW_INSECURE_DATABASE")}
        # A real Fernet key: src/crypto.py builds its cipher at import
        # from DATA_ENCRYPTION_KEY, so a placeholder string fails there.
        from cryptography.fernet import Fernet
        env.update({
            "ENVIRONMENT": "production",
            "DATABASE_URL": f"sqlite:///{tmp_path}/prod-cookie.db",
            "JWT_SECRET_KEY": "prod-jwt-secret-that-is-long-enough-0123",
            "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "PYTHONPATH": str(REPO_ROOT),
        })
        script = (
            "from starlette.responses import Response\n"
            "from types import SimpleNamespace\n"
            "from src.config import settings\n"
            "from src.routers.auth import _issue_session_cookie\n"
            "assert settings.COOKIE_NAME == '__Host-access_token', settings.COOKIE_NAME\n"
            "assert settings.SECURE_COOKIES is True\n"
            "resp = Response()\n"
            "_issue_session_cookie(resp, SimpleNamespace(id=1, username='chairman', token_version=0))\n"
            "print(resp.headers['set-cookie'])\n"
        )
        proc = subprocess.run([sys.executable, "-c", script], env=env,
                              cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        cookie = proc.stdout.strip()
        assert "__Host-access_token=" in cookie
        assert "Secure" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie


class TestUploadsGating:
    def test_uploads_requires_auth(self, client):
        r = client.get("/uploads/expense-receipts/abc.png")
        assert r.status_code == 401

    def test_uploads_serves_file_to_authenticated_user(self, client, tmp_path, monkeypatch):
        from src.config import settings

        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        (upload_dir / "a.txt").write_text("hello")
        monkeypatch.setattr(settings, "LOCAL_UPLOAD_DIR", str(upload_dir))

        login = client.post("/api/auth/login",
                            json={"username": "chairman", "password": "Chairman123!"})
        assert login.status_code == 200
        cookie = _set_cookie_header(login).split(";")[0]
        r = client.get("/uploads/a.txt", headers={"cookie": cookie})
        assert r.status_code == 200
        assert r.text == "hello"
