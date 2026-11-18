"""PM-m13 / PM-m9: the password-reset queue lives in its own router
(src/routers/password_resets.py, split out of users.py) and
list_password_resets is paginated like report_logs.

Proves: the three routes still resolve at the same paths with the same
Chairman-only gates (the auth matrix in tests/test_auth_matrix.py also
covers the gates), the list defaults keep the Manage Users page working
(no params -> first 50, "requests" + "pending_count" keys), and
limit/offset page through the queue with a total count.
"""
import pyotp
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth import hash_password as make_hash
from src.models import Base, BusinessUnit, PasswordResetRequest, User

PW = "TestPass123!"
_PW_HASH = make_hash(PW)
TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # fixed test secret, never a real one (same as test_http_write_endpoints.py)


@pytest.fixture()
def http_db(tmp_path):
    db_path = tmp_path / "password_resets_router.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all(
        [
            BusinessUnit(id=1, code="BU1", name="Shop One"),
            BusinessUnit(id=2, code="BU2", name="Shop Two"),
            User(
                username="chairman",
                hashed_password=_PW_HASH,
                role="Chairman",
                is_active=True,
                require_password_change=False,
                is_profile_completed=True,
                totp_secret=TOTP_SECRET,
                totp_enabled=True,
            ),
            User(
                username="ho",
                hashed_password=_PW_HASH,
                role="HO",
                is_active=True,
                require_password_change=False,
                is_profile_completed=True,
            ),
            User(
                username="ceo_one",
                hashed_password=_PW_HASH,
                role="CEO",
                business_unit_id=1,
                is_active=True,
                require_password_change=False,
                is_profile_completed=True,
            ),
            User(
                username="ceo_two",
                hashed_password=_PW_HASH,
                role="CEO",
                business_unit_id=2,
                is_active=True,
                require_password_change=False,
                is_profile_completed=True,
            ),
        ]
    )
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _uid(http_db, username):
    return http_db.query(User).filter(User.username == username).one().id


@pytest.fixture()
def chairman(make_client):
    c = make_client()
    c.login("chairman", PW, totp_code=pyotp.TOTP(TOTP_SECRET).now())
    return c


@pytest.fixture()
def ho(make_client):
    c = make_client()
    c.login("ho", PW)
    return c


def _seed_requests(http_db, n=3):
    # One Pending request per user: the model enforces at most one open
    # request per user (uq_pwreset_one_pending_per_user).
    usernames = ["ceo_one", "ceo_two", "ho"][:n]
    reqs = [
        PasswordResetRequest(user_id=_uid(http_db, name), username=name, status="Pending")
        for name in usernames
    ]
    http_db.add_all(reqs)
    http_db.commit()
    return reqs


class TestPasswordResetsRouterSplit:
    def test_routes_serve_from_password_resets_module(self):
        """PM-m13: the three endpoints are defined in password_resets.py,
        and users.py no longer defines any /password-resets route."""
        from tests.test_auth_matrix import _iter_api_routes

        from src.main import app
        from src.routers import password_resets, users

        paths = {
            ("GET", "/api/admin/password-resets"),
            ("POST", "/api/admin/password-resets/{request_id}/approve"),
            ("POST", "/api/admin/password-resets/{request_id}/reject"),
        }
        seen = set()
        for path, methods, route in _iter_api_routes(app.routes):
            for method in methods:
                if (method, path) in paths:
                    seen.add((method, path))
                    assert route.endpoint.__module__ == "src.routers.password_resets", path
        assert seen == paths, f"missing routes: {paths - seen}"

        user_paths = {r.path for r in users.router.routes}
        assert not any(p.startswith("/api/admin/password-resets") for p in user_paths)
        reset_paths = {r.path for r in password_resets.router.routes}
        assert reset_paths == {
            "/api/admin/password-resets",
            "/api/admin/password-resets/{request_id}/approve",
            "/api/admin/password-resets/{request_id}/reject",
        }

    def test_list_defaults_keep_frontend_working(self, chairman, http_db):
        """PM-m9: no params -> same shape the Manage Users page reads
        ("requests" + "pending_count"), default limit 50, total present."""
        _seed_requests(http_db, 3)
        r = chairman.get("/api/admin/password-resets")
        assert r.status_code == 200, r.text
        body = r.json()
        # Newest first: last seeded ("ho") comes first.
        assert [req["username"] for req in body["requests"]] == ["ho", "ceo_two", "ceo_one"]
        assert body["pending_count"] == 3
        assert body["total"] == 3
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_list_paginates(self, chairman, http_db):
        """PM-m9: limit/offset page newest-first; total is the full count,
        never the page length."""
        _seed_requests(http_db, 3)
        first = chairman.get("/api/admin/password-resets?limit=2&offset=0").json()
        assert len(first["requests"]) == 2
        assert first["total"] == 3 and first["limit"] == 2 and first["offset"] == 0
        second = chairman.get("/api/admin/password-resets?limit=2&offset=2").json()
        assert len(second["requests"]) == 1
        assert second["total"] == 3 and second["offset"] == 2
        assert first["requests"][0]["id"] != second["requests"][0]["id"]
        # Newest first: ids descend across the pages.
        assert first["requests"][0]["id"] > first["requests"][1]["id"] > second["requests"][0]["id"]

    def test_list_pagination_clamps(self, chairman, http_db):
        """PM-m9: same clamping as report_logs — limit floors at 1 and caps
        at 500, offset floors at 0; the clamped values are echoed back."""
        _seed_requests(http_db, 1)
        body = chairman.get("/api/admin/password-resets?limit=0").json()
        assert body["limit"] == 1 and len(body["requests"]) == 1
        body = chairman.get("/api/admin/password-resets?limit=9999").json()
        assert body["limit"] == 500
        body = chairman.get("/api/admin/password-resets?offset=-5").json()
        assert body["offset"] == 0

    def test_list_role_gate_unchanged(self, ho, http_db):
        """PM-m13: the split did not loosen the gate — HO still gets 403."""
        _seed_requests(http_db, 1)
        assert ho.get("/api/admin/password-resets").status_code == 403
