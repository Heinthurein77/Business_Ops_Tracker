"""M-15: HTTP-level tests for the 17 previously untested endpoints.

Before this file, GET /api/admin/comparison, /operations, /ceo-profiles,
/health-dashboard, GET+POST /api/ceo/financial-notes, POST
/api/ops/notifications/read-all, the remind-missing / remind-actions
wrappers, POST+GET /api/ops/staff, POST+GET /api/ops/customers, GET
/api/ops/forecast and GET+POST+DELETE /api/ops/documents (+ check-expiry)
had no functional test — only direct run_* calls or allowlist mentions.
Each test below drives the real app over HTTP (httpx + ASGITransport,
file-backed SQLite via the test_http_smoke.py pattern) with a logged-in
cookie, and asserts both the happy path and the role denial.

Note on POST /api/ops/customers with a satisfaction score: that path is
C-2 (Decimal not imported in ops.py, fixed on another workstream) — the
score test here asserts the 400 validation branch, which runs before
the Decimal line, and the happy-path submit omits the score.
"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.auth import hash_password as make_hash
from src.models import (
    ActionItem,
    Base,
    BusinessUnit,
    DailyFinancial,
    FinancialNote,
    HealthStatus,
    Notification,
    User,
)
from src.utils import today_local

PW = "TestPass123!"
_PW_HASH = make_hash (PW)  # once per module: bcrypt cost 12 is slow


@pytest.fixture()
def http_db(tmp_path):
    """File-backed SQLite DB shared by the fixture thread and the
    endpoint threadpool (same pattern as tests/test_http_smoke.py)."""
    db_path = tmp_path / "m15.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
        User(username="chairman", hashed_password=_PW_HASH,
             role="Chairman", is_active=True, require_password_change=False,
             is_profile_completed=True),
        User(username="ho", hashed_password=_PW_HASH,
             role="HO", is_active=True, require_password_change=False,
             is_profile_completed=True),
        User(username="ceo_one", hashed_password=_PW_HASH,
             role="CEO", business_unit_id=1, is_active=True,
             require_password_change=False, is_profile_completed=True),
        User(username="ceo_two", hashed_password=_PW_HASH,
             role="CEO", business_unit_id=2, is_active=True,
             require_password_change=False, is_profile_completed=True),
    ])
    session.commit()
    today = today_local()
    session.add_all([
        DailyFinancial(business_unit_id=1, report_date=today,
                       revenue=1000, cash_flow=100, submitted_by=3),
        HealthStatus(business_unit_id=1, report_date=today,
                     stress_level="Low", energy_mood="Energetic",
                     daily_steps=8000, submitted_by=3),
        FinancialNote(business_unit_id=1, note_date=today,
                      note_text="Big client paid early", created_by=3),
        Notification(user_id=3, type="missing_report",
                     title="Report not submitted today",
                     message="Please submit today's report.", link="/ceo"),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


# T-m1: the local ApiClient harness is deleted — tests use the shared
# `client` fixture from root conftest.py (same httpx ASGI client, same
# get_db override, same cookie handling). `ceo`/`admin` build their own
# client via `make_client` because a test can hold both at once
# (test_check_expiry_admin_ok) and one client holds one cookie.
@pytest.fixture()
def admin(make_client):
    c = make_client()
    c.login("ho", PW)
    return c


@pytest.fixture()
def ceo(make_client):
    c = make_client()
    c.login("ceo_one", PW)
    return c


# ---------------------------------------------------------------- admin dashboards

class TestAdminDashboards:
    def test_comparison_happy_path(self, admin):
        r = admin.get("/api/admin/comparison")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "comparison_data" in body
        assert "trend_labels" in body

    def test_comparison_ceo_denied(self, ceo):
        assert ceo.get("/api/admin/comparison").status_code == 403

    def test_operations_happy_path(self, admin):
        r = admin.get("/api/admin/operations")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "operations" in body
        assert any(op["bu_id"] == 1 for op in body["operations"])

    def test_operations_ceo_denied(self, ceo):
        assert ceo.get("/api/admin/operations").status_code == 403

    def test_operations_unauthenticated(self, client):
        assert client.get("/api/admin/operations").status_code == 401

    def test_ceo_profiles_happy_path(self, admin):
        r = admin.get("/api/admin/ceo-profiles")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        assert {p["username"] for p in body["ceo_profiles"]} == {"ceo_one", "ceo_two"}

    def test_ceo_profiles_ceo_denied(self, ceo):
        assert ceo.get("/api/admin/ceo-profiles").status_code == 403

    def test_health_dashboard_happy_path(self, admin):
        r = admin.get("/api/admin/health-dashboard")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_business_units"] == 2
        assert body["submitted_count"] == 1
        assert len(body["directory"]) == 2
        assert body["counts"]["healthy"] + body["counts"]["warning"] \
            + body["counts"]["critical"] + body["counts"]["unknown"] == 2

    def test_health_dashboard_ceo_denied(self, ceo):
        assert ceo.get("/api/admin/health-dashboard").status_code == 403


# ---------------------------------------------------------------- CEO financial notes

class TestFinancialNotes:
    def test_list_financial_notes(self, ceo):
        r = ceo.get("/api/ceo/financial-notes")
        assert r.status_code == 200, r.text
        notes = r.json()["financial_notes"]
        assert len(notes) == 1
        assert notes[0]["note_text"] == "Big client paid early"

    def test_list_financial_notes_admin_denied(self, admin):
        assert admin.get("/api/ceo/financial-notes").status_code == 403

    def test_add_financial_note(self, ceo, http_db):
        r = ceo.post("/api/ceo/financial-notes",
                     json={"note_text": "Power outage cut trading"})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        rows = http_db.query(FinancialNote).filter(
            FinancialNote.business_unit_id == 1).all()
        assert len(rows) == 2

    def test_add_blank_financial_note_rejected(self, ceo):
        r = ceo.post("/api/ceo/financial-notes", json={"note_text": "   "})
        assert r.status_code == 400

    def test_add_financial_note_admin_denied(self, admin):
        r = admin.post("/api/ceo/financial-notes", json={"note_text": "x"})
        assert r.status_code == 403


# ---------------------------------------------------------------- notifications

class TestNotificationEndpoints:
    def test_read_all_marks_unread_read(self, ceo, http_db):
        assert ceo.get("/api/ops/notifications?unread_only=true").json()["unread_count"] == 1
        r = ceo.post("/api/ops/notifications/read-all")
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        assert ceo.get("/api/ops/notifications?unread_only=true").json()["unread_count"] == 0
        assert http_db.query(Notification).filter(
            Notification.user_id == 3, Notification.is_read == False).count() == 0  # noqa: E712

    def test_read_all_unauthenticated(self, client):
        assert client.post("/api/ops/notifications/read-all").status_code == 401

    def test_remind_missing_admin_ok(self, admin):
        # ceo_two (BU2) has no DailyFinancial today; ceo_one does.
        r = admin.post("/api/ops/notifications/remind-missing")
        assert r.status_code == 200, r.text
        assert r.json() == {"notified": 1}

    def test_remind_missing_ceo_denied(self, ceo):
        assert ceo.post("/api/ops/notifications/remind-missing").status_code == 403

    def test_remind_actions_admin_ok(self, admin, http_db):
        from src.models import ActionItem
        http_db.add(ActionItem(business_unit_id=1, title="Chase supplier",
                               assigned_to_id=3, due_date=today_local(),
                               status="pending", created_by_id=1))
        http_db.commit()
        r = admin.post("/api/ops/notifications/remind-actions")
        assert r.status_code == 200, r.text
        assert r.json() == {"checked": 1, "notified": 1}

    def test_remind_actions_ceo_denied(self, ceo):
        assert ceo.post("/api/ops/notifications/remind-actions").status_code == 403


# ---------------------------------------------------------------- ops staff / customers / forecast

class TestOpsStaffCustomersForecast:
    def test_list_envelope_has_total_limit_offset(self, ceo):
        # B-m2: every list endpoint returns {"items", "total", "limit",
        # "offset"} with total = full matching count, not the page length.
        today = today_local().isoformat()
        ceo.post("/api/ops/staff", json={
            "report_date": today, "total_staff": 12, "present": 11,
            "absent": 1, "new_hires": 0, "resignations": 0})
        ceo.post("/api/ops/customers", json={
            "report_date": today, "footfall": 120, "transactions": 80,
            "complaints": 2})
        for path in ("/api/ops/staff", "/api/ops/customers",
                     "/api/ops/action-items", "/api/ops/documents"):
            body = ceo.get(path).json()
            assert set(body) >= {"items", "total", "limit", "offset"}, path
            assert body["total"] == len(body["items"]), path
            assert body["limit"] == 200 and body["offset"] == 0, path
        staff = ceo.get("/api/ops/staff?limit=1").json()
        assert staff["total"] == 1 and len(staff["items"]) == 1 and staff["limit"] == 1

    def test_submit_and_list_staff(self, ceo):
        today = today_local().isoformat()
        r = ceo.post("/api/ops/staff", json={
            "report_date": today, "total_staff": 12, "present": 11,
            "absent": 1, "new_hires": 0, "resignations": 0})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        rows = ceo.get("/api/ops/staff").json()["items"]
        assert len(rows) == 1
        assert rows[0]["total_staff"] == 12
        assert rows[0]["present"] == 11

    def test_staff_cross_bu_denied(self, ceo):
        # CEO pinned to BU1 may not list BU2's staff rows.
        assert ceo.get("/api/ops/staff?business_unit_id=2").status_code == 403

    def test_staff_unauthenticated(self, client):
        assert client.get("/api/ops/staff").status_code == 401

    def test_submit_and_list_customers(self, ceo):
        today = today_local().isoformat()
        r = ceo.post("/api/ops/customers", json={
            "report_date": today, "footfall": 120, "transactions": 80,
            "complaints": 2})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        rows = ceo.get("/api/ops/customers").json()["items"]
        assert len(rows) == 1
        assert rows[0]["footfall"] == 120
        assert rows[0]["satisfaction_score"] is None

    def test_customers_bad_score_rejected_before_decimal(self, ceo):
        # 400 from the range check (runs before the C-2 Decimal line).
        r = ceo.post("/api/ops/customers", json={
            "report_date": today_local().isoformat(), "satisfaction_score": 9.5})
        assert r.status_code == 400

    def test_customers_cross_bu_denied(self, ceo):
        assert ceo.get("/api/ops/customers?business_unit_id=2").status_code == 403

    def test_forecast_from_seeded_financials(self, ceo):
        r = ceo.get("/api/ops/forecast")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mtd_revenue"] == 1000
        assert body["mtd_cash_flow"] == 100
        assert body["reporting_days"] == 1
        assert body["projected_revenue"] >= body["mtd_revenue"]

    def test_forecast_admin_sees_all_bus(self, admin):
        r = admin.get("/api/ops/forecast")
        assert r.status_code == 200, r.text
        assert r.json()["mtd_revenue"] == 1000


# ---------------------------------------------------------------- ops documents

class TestOpsDocuments:
    def _doc(self):
        return {"title": "Trade licence", "document_type": "license",
                "expiry_date": (today_local() + timedelta(days=10)).isoformat()}

    def test_create_list_delete_document(self, ceo):
        r = ceo.post("/api/ops/documents", json=self._doc())
        assert r.status_code == 200, r.text
        doc_id = r.json()["id"]
        assert r.json()["title"] == "Trade licence"
        assert r.json()["days_left"] == 10
        rows = ceo.get("/api/ops/documents").json()
        assert rows["total"] == 1
        assert rows["items"][0]["id"] == doc_id
        assert ceo.delete(f"/api/ops/documents/{doc_id}").json() == {"ok": True}
        assert ceo.get("/api/ops/documents").json()["total"] == 0

    def test_document_cross_bu_create_denied(self, ceo):
        payload = self._doc()
        payload["business_unit_id"] = 2
        assert ceo.post("/api/ops/documents", json=payload).status_code == 403

    def test_delete_missing_document_404(self, ceo):
        assert ceo.delete("/api/ops/documents/99999").status_code == 404

    def test_check_expiry_admin_ok(self, ceo, admin):
        ceo.post("/api/ops/documents", json=self._doc())
        r = admin.post("/api/ops/documents/check-expiry", params={"days": 30})
        assert r.status_code == 200, r.text
        assert r.json()["checked"] == 1
        # CEO of BU1 + Chairman + HO are notified.
        assert r.json()["notified"] == 3

    def test_check_expiry_ceo_denied(self, ceo):
        assert ceo.post("/api/ops/documents/check-expiry").status_code == 403


# ---------------------------------------------------------------- ops Excel export (merge follow-up)

class TestOpsExportExcel:
    def test_ceo_export_all_tabs_ok(self, ceo):
        # Merge follow-up: scoped() called .filter() AFTER .limit() —
        # SQLAlchemy forbids filtering a limited query, so every CEO
        # export 500d while the Chairman path (no filter, plain .all())
        # worked fine and hid the bug.
        for tab in ("actions", "documents", "staff", "customers"):
            r = ceo.get(f"/api/ops/export-excel?tab={tab}")
            assert r.status_code == 200, (tab, r.text)
            assert "spreadsheetml.sheet" in r.headers.get("content-type", "")

    def test_ceo_export_is_bu_scoped(self, ceo, http_db):
        # A CEO's export must contain only their own BU's rows, not the
        # other BU's — the filter that 500d was also the scoping itself.
        # (xlsx is a zip, so parse it with openpyxl rather than grepping
        # the raw bytes.)
        from io import BytesIO

        from openpyxl import load_workbook

        http_db.add(ActionItem(business_unit_id=2, title="other BU item",
                               status="pending", created_by_id=4))
        http_db.add(ActionItem(business_unit_id=1, title="own BU item",
                               status="pending", created_by_id=3))
        http_db.commit()
        r = ceo.get("/api/ops/export-excel?tab=actions")
        assert r.status_code == 200, r.text
        ws = load_workbook(BytesIO(r.content)).active
        titles = [row[0] for row in ws.iter_rows(min_row=2, values_only=True)]
        assert "own BU item" in titles
        assert "other BU item" not in titles
