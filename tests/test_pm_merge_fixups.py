"""Merge fix-ups for the PM-review fix-all pass (2026-11-18).

Proves the three items workstream B flagged for the merger:
- the /notifications route is registered (covered by the Playwright
  smoke spec, which visits the page as Chairman);
- the BU-detail anomaly display is wired (frontend-only; verified by
  `npm run build` + the backend payload tests in
  tests/test_pm_review_b_batch.py);
- `GET /api/admin/users` is Chairman + HO, so the Daily Ops assignee
  picker works for HO too (PM-1 fix-up: widening the read-only roster).
"""
import pyotp
import pytest

from src.auth import hash_password
from src.crypto import encrypt_text
from src.models import BusinessUnit, User

PASSWORD = "MergeFix123!"
# Same pattern as tests/test_http_write_endpoints.py: a Chairman with
# TOTP already enrolled, so password + current code earns a session.
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture(autouse=True)
def _seed_db(http_db):
    # NOTE: the `client` fixture talks to `http_db` (file-backed; the
    # in-memory `db` fixture is a different database), which already
    # seeds a chairman ("Chairman123!"). Add only the HO + CEO users.
    http_db.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        User(id=2, username="ho_user", hashed_password=hash_password(PASSWORD),
             role="HO", is_active=True),
        User(id=10, username="ceo_one", hashed_password=hash_password(PASSWORD),
             role="CEO", business_unit_id=1, is_active=True),
        User(id=11, username="chairman2", hashed_password=hash_password(PASSWORD),
             role="Chairman", is_active=True, require_password_change=False,
             is_profile_completed=True, totp_secret=encrypt_text(TOTP_SECRET),
             totp_enabled=True),
    ])
    http_db.commit()


def test_ho_can_list_users_for_assignee_picker(client):
    """PM-1 fix-up: HO opens Daily Ops, the assignee picker needs the
    roster — GET /api/admin/users must answer 200 for HO, not 403."""
    client.login("ho_user", PASSWORD)
    resp = client.get("/api/admin/users")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["users"], list)


def test_ceo_cannot_list_users(client):
    """The widening is Chairman + HO only — a CEO still gets 403."""
    client.login("ceo_one", PASSWORD)
    assert client.get("/api/admin/users").status_code == 403


def test_chairman_still_lists_users(client):
    # The conftest-seeded chairman has no TOTP enrolled (password-only
    # login answers 403 TOTP_ENROLLMENT_REQUIRED), so use the enrolled
    # chairman2 seeded above with a current code as second factor.
    client.login("chairman2", PASSWORD, totp_code=pyotp.TOTP(TOTP_SECRET).now())
    resp = client.get("/api/admin/users")
    assert resp.status_code == 200, resp.text
    assert resp.json()["users"]
