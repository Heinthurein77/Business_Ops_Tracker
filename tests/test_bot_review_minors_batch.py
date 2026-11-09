"""Regression tests for the BOT security + reliability review minors batch
(workstream 1, backend code): P3-1..P3-8, D-m1, D-m2, D-m3, D-m4, D-m7.

In-memory SQLite, calling router functions directly, except P3-5 which
drives the real app over HTTP (httpx + ASGITransport, same pattern as
tests/test_http_smoke.py) and the edge tests, which use the raw-ASGI
harness from tests/test_edge_surface.py.
"""
import asyncio
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.auth import hash_password
from src.config import settings
from src.database import get_db
from src.main import app as fastapi_app
from src.main import lifespan, seed_business_units
from src.models import (
    Account, Base, BusinessUnit, Employee, InventoryItem, JournalEntry,
    JournalLine, StaffAdvance, Supplier, User,
)
from src.routers.accounting import (
    delete_journal, export_report, opening_balance_template, provision_bu,
)
from src.routers.ops import (
    CustomerIn, EmployeeIn, InvoiceIn, InvoiceLineIn, MovementIn, PaymentIn,
    PayrollCreate, SalesReturnIn, SalesReturnLineIn, _safe_filename,
    _serial_list, create_employee, create_invoice, create_payroll, create_po,
    create_sales_customer, create_sales_return, list_inventory, payslip_pdf,
    record_movement, record_payment, send_invoice, POIn, POLineIn,
)
from src.utils import server_error, utcnow

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None)
ACCT_BU1 = SimpleNamespace(id=3, role="Accountant", business_unit_id=1)
ACCT_BU2 = SimpleNamespace(id=4, role="Accountant", business_unit_id=2)

PASSWORD = "Chairman123!"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu1 = BusinessUnit(id=1, code="BU1", name="Shop One", nature="retail")
    bu2 = BusinessUnit(id=2, code="BU2", name="Shop Two", nature="retail")
    session.add_all([bu1, bu2])
    session.commit()
    provision_bu(1, "retail", CHAIRMAN, session)
    provision_bu(2, "retail", CHAIRMAN, session)
    session.test_bu = bu1
    session.test_bu2 = bu2
    yield session
    session.close()


def _item(db, bu_id=1, name="Widget", stock=10, cost=500):
    it = InventoryItem(business_unit_id=bu_id, name=name,
                       current_stock=Decimal(str(stock)), unit_cost=Decimal(str(cost)))
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _customer(db, bu_id=1, name="Buyer"):
    return create_sales_customer(CustomerIn(business_unit_id=bu_id, name=name),
                                 user=CHAIRMAN, db=db)


def _sent_invoice(db, amount=1500):
    cust = _customer(db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 6),
        tax_rate=0, lines=[InvoiceLineIn(description="Widget", quantity=1,
                                         unit_price=amount)]),
        user=CHAIRMAN, db=db)
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    return inv


def _collect_response(resp):
    async def _go():
        return b"".join([chunk async for chunk in resp.body_iterator])
    return asyncio.run(_go())


def _workbook_bytes(resp):
    import io
    from openpyxl import load_workbook
    return load_workbook(io.BytesIO(_collect_response(resp)))


# ---------------------------------------------------------------- P3-1
def test_p3_1_journal_export_neutralises_formula_cells(db):
    acc = db.query(Account).filter(Account.business_unit_id == 1, Account.code == "1110").first()
    acc.name = "=EVIL()"
    entry = JournalEntry(business_unit_id=1, entry_no="JV-2026-10-001",
                         entry_date=date(2026, 10, 6),
                         description="=HYPERLINK(\"http://evil.example\",\"click\")",
                         status="posted")
    db.add(entry)
    db.flush()
    db.add(JournalLine(entry_id=entry.id, account_id=acc.id, business_unit_id=1,
                       debit=Decimal("100"), credit=Decimal("0")))
    db.commit()
    wb = _workbook_bytes(export_report("journal", "xlsx", business_unit_id=1,
                                       user=CHAIRMAN, db=db))
    ws = wb.active
    for row in ws.iter_rows():
        for cell in row:
            assert cell.data_type != "f", f"live formula cell: {cell.value!r}"
    flat = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
    assert any(v.startswith("'=HYPERLINK") for v in flat)
    assert any(v.startswith("'=EVIL") for v in flat)


def test_p3_1_opening_balance_template_neutralises_account_name(db):
    acc = db.query(Account).filter(Account.business_unit_id == 1, Account.code == "1110").first()
    acc.name = "=CMD|calc"
    db.commit()
    wb = _workbook_bytes(opening_balance_template(business_unit_id=1, user=CHAIRMAN, db=db))
    ws = wb.active
    names = [row[1].value for row in ws.iter_rows(min_row=2) if row[1].value]
    assert "'=CMD|calc" in names
    for row in ws.iter_rows():
        for cell in row:
            assert cell.data_type != "f"


# ---------------------------------------------------------------- P3-2
def test_p3_2_safe_filename_strips_quote_and_controls():
    assert _safe_filename('A"B\r\nC/D\\E') == "ABCDE"
    assert _safe_filename("John Doe") == "John_Doe"
    assert _safe_filename("   ") == "file"
    assert _safe_filename('x"; filename*=UTF-8\'\'evil') == "x;_filename*=UTF-8''evil"


def test_p3_2_payslip_filename_is_sanitised(db):
    emp = create_employee(EmployeeIn(business_unit_id=1, name='A"B\r\nC',
                                     base_salary=Decimal("500000")),
                          user=CHAIRMAN, db=db)
    run = create_payroll(PayrollCreate(business_unit_id=1, period="2026-10"),
                         user=CHAIRMAN, db=db)
    line_id = run["lines"][0]["id"]
    resp = payslip_pdf(run["id"], line_id, user=CHAIRMAN, db=db)
    disp = resp.headers["Content-Disposition"]
    assert '"A' not in disp and "\r" not in disp and "\n" not in disp
    assert "payslip_2026-10_ABC.pdf" in disp


# ---------------------------------------------------------------- P3-3
def test_p3_3_record_payment_surfaces_failed_allocation(db, monkeypatch, caplog):
    inv = _sent_invoice(db)
    import src.routers.modules as modules_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("allocation exploded")

    monkeypatch.setattr(modules_mod, "allocate_installments", _boom)
    with caplog.at_level(logging.WARNING):
        out = record_payment(inv["id"], PaymentIn(amount=1500, payment_date=date(2026, 10, 7)),
                             user=CHAIRMAN, db=db)
    assert out["journal_posted"] is True
    assert out["installments_ok"] is False  # was silently swallowed before
    assert out["status"] == "paid"
    assert any(f"invoice {inv['id']}" in r.message for r in caplog.records)


def test_p3_3_sales_return_surfaces_failed_allocation(db, monkeypatch):
    inv = _sent_invoice(db)
    import src.routers.modules as modules_mod
    monkeypatch.setattr(modules_mod, "allocate_installments",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = create_sales_return(SalesReturnIn(
        invoice_id=inv["id"],
        lines=[SalesReturnLineIn(invoice_line_id=inv["lines"][0]["id"], quantity=1)]),
        user=CHAIRMAN, db=db)
    assert out["installments_ok"] is False


# ---------------------------------------------------------------- P3-4
def test_p3_4_journal_failure_hides_raw_exception(db, monkeypatch):
    it = _item(db, stock=5, cost=800)
    import src.routers.accounting as acct_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("SECRET_MARKER_xyz SELECT * FROM pg_shadow boom")

    monkeypatch.setattr(acct_mod, "create_entry", _boom)
    with pytest.raises(HTTPException) as exc:
        record_movement(it.id, MovementIn(movement_type="out", quantity=1, post_cogs=True),
                        user=CHAIRMAN, db=db)
    assert exc.value.status_code == 500
    assert "SECRET_MARKER_xyz" not in exc.value.detail
    assert "pg_shadow" not in exc.value.detail
    assert "error_id:" in exc.value.detail


def test_p3_4_server_error_helper_never_embeds_exception():
    logger = logging.getLogger("test_p3_4")
    exc = server_error("COGS journal could not be posted",
                       RuntimeError("SELECT * FROM users"), logger, context="item 7")
    assert exc.status_code == 500
    assert "SELECT" not in exc.detail and "users" not in exc.detail
    assert exc.detail.startswith("COGS journal could not be posted (error_id: ")


# ---------------------------------------------------------------- P3-5
@pytest.fixture()
def http_db(tmp_path):
    """File-backed SQLite DB shared with the app thread (see test_http_smoke)."""
    import httpx

    db_path = tmp_path / "http.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(username="chairman", hashed_password=hash_password(PASSWORD),
                     role="Chairman", is_active=True, require_password_change=False,
                     is_profile_completed=True))
    session.add(User(username="acct_bu1", hashed_password=hash_password(PASSWORD),
                     role="Accountant", business_unit_id=1, is_active=True,
                     require_password_change=False, is_profile_completed=True))
    bu1 = BusinessUnit(id=1, code="BU1", name="Shop One", nature="retail")
    bu2 = BusinessUnit(id=2, code="BU2", name="Shop Two", nature="retail")
    session.add_all([bu1, bu2])
    session.add(InventoryItem(business_unit_id=1, name="BU1 Widget",
                              current_stock=Decimal("5"), unit_cost=Decimal("100")))
    session.add(InventoryItem(business_unit_id=2, name="BU2 Widget",
                              current_stock=Decimal("5"), unit_cost=Decimal("100")))
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def client(http_db):
    import httpx

    def _override():
        try:
            yield http_db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = _override
    try:
        class SyncClient:
            def request(self, method, url, **kw):
                async def _go():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fastapi_app),
                                                 base_url="http://testserver") as ac:
                        return await ac.request(method, url, **kw)
                return asyncio.run(_go())

            def get(self, url, **kw):
                return self.request("GET", url, **kw)

            def post(self, url, **kw):
                return self.request("POST", url, **kw)

        yield SyncClient()
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


def _login_cookie(client):
    r = client.post("/api/auth/login", json={"username": "acct_bu1", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.headers["set-cookie"].split(";")[0]


class TestP3_5RouterLevelC1:
    def test_bu1_accountant_post_po_for_bu2_returns_403(self, client):
        cookie = _login_cookie(client)
        r = client.post("/api/ops/purchase-orders",
                        json={"business_unit_id": 2, "supplier_id": 1,
                              "order_date": "2026-10-20",
                              "lines": [{"item_name": "Generator", "quantity": 1,
                                         "unit_cost": 100}]},
                        headers={"Cookie": cookie})
        assert r.status_code == 403, r.text

    def test_bu1_accountant_inventory_scoped_to_bu1(self, client):
        cookie = _login_cookie(client)
        r = client.get("/api/ops/inventory", headers={"Cookie": cookie})
        assert r.status_code == 200, r.text
        names = [i["name"] for i in r.json()["items"]]
        assert "BU1 Widget" in names
        assert "BU2 Widget" not in names


# ---------------------------------------------------------------- P3-6
def test_p3_6_utcnow_is_naive_utc_like_the_old_call():
    now = utcnow()
    assert now.tzinfo is None
    assert abs((datetime.now() - now).total_seconds()) < 5


def test_p3_6_no_datetime_utcnow_call_sites_remain_in_src():
    pattern = re.compile(r"datetime\.utcnow\s*[\(\,]")
    offenders = []
    for path in Path("src").rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == []


# ---------------------------------------------------------------- P3-7
def test_p3_7_corrupt_serial_json_logs_warning_with_item_id(caplog):
    with caplog.at_level(logging.WARNING):
        assert _serial_list("this is not json", item_id=42) == []
    assert any("42" in r.message and "corrupt" in r.message.lower()
               for r in caplog.records)
    assert _serial_list('["A", "B"]') == ["A", "B"]
    assert _serial_list(None) == []


# ---------------------------------------------------------------- P3-8
def test_p3_8_origin_check_rejects_host_outside_allowed_hosts(monkeypatch):
    from src.edge import EdgeMiddleware

    async def _dummy(scope, receive, send):
        pass

    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["app.example.com"])
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", [])
    mw = EdgeMiddleware(_dummy)
    good = {b"origin": b"https://app.example.com", b"host": b"app.example.com"}
    assert mw._origin_ok(good) is True
    # Same origin string, but the Host is not one the app answers to: the
    # fallback must not trust it.
    bad = {b"origin": b"https://app.example.com", b"host": b"evil.example.com"}
    assert mw._origin_ok(bad) is False


# ---------------------------------------------------------------- D-m1
def test_dm1_r2_client_has_timeouts_and_retries(monkeypatch):
    from src import r2_storage as r2_mod

    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "test-account")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setattr(settings, "R2_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    client = r2_mod._get_client()
    assert client is not None
    assert client.meta.config.connect_timeout == 10
    assert client.meta.config.read_timeout == 30
    # botocore normalises retries={"max_attempts": 2} (legacy mode) to
    # total_max_attempts = max_attempts + 1, i.e. 2 retries after the
    # first attempt.
    assert client.meta.config.retries["total_max_attempts"] == 3


# ---------------------------------------------------------------- D-m2
def test_dm2_startup_logs_client_ip_source(monkeypatch, caplog):
    monkeypatch.setattr("src.main._run_startup_step", lambda *a, **k: None)

    async def _run():
        async with lifespan(fastapi_app):
            pass

    with caplog.at_level(logging.INFO):
        asyncio.run(_run())
    assert any("CLIENT_IP_HEADER" in r.message and "TRUSTED_PROXY_HOPS" in r.message
               for r in caplog.records)


# ---------------------------------------------------------------- D-m3
def test_dm3_bu_seed_survives_integrity_race(db, monkeypatch):
    real_commit = db.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("duplicate key value"))
        return real_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)
    seed_business_units(db)  # must not raise: the loser rolls back and moves on
    assert calls["n"] == 1


def test_dm3_bu_seed_is_idempotent(db):
    seed_business_units(db)
    seed_business_units(db)
    codes = [bu.code for bu in db.query(BusinessUnit).all()]
    assert len(codes) == len(set(codes))


# ---------------------------------------------------------------- D-m4
def _draft_entry(db, entry_no, **kw):
    e = JournalEntry(business_unit_id=1, entry_no=entry_no,
                     entry_date=date(2026, 10, 6), description="draft",
                     status="draft", **kw)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_dm4_delete_journal_referenced_by_reversal_returns_400(db):
    draft = _draft_entry(db, "JV-2026-10-100")
    _draft_entry(db, "JV-2026-10-101", reversal_of_id=draft.id)
    with pytest.raises(HTTPException) as exc:
        delete_journal(draft.id, user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    assert "in use" in exc.value.detail


def test_dm4_delete_journal_referenced_by_advance_returns_400(db):
    draft = _draft_entry(db, "JV-2026-10-102")
    emp = Employee(business_unit_id=1, name="Adv Emp", base_salary=Decimal("100000"))
    db.add(emp)
    db.flush()
    db.add(StaffAdvance(employee_id=emp.id, business_unit_id=1, amount=Decimal("50000"),
                        remaining_amount=Decimal("50000"), monthly_deduction=Decimal("10000"),
                        advance_date=date(2026, 10, 1), journal_entry_id=draft.id))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        delete_journal(draft.id, user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    assert "in use" in exc.value.detail


def test_dm4_delete_unreferenced_draft_journal_succeeds(db):
    draft = _draft_entry(db, "JV-2026-10-103")
    assert delete_journal(draft.id, user=CHAIRMAN, db=db) == {"ok": True}
    assert db.query(JournalEntry).filter(JournalEntry.id == draft.id).first() is None


# ---------------------------------------------------------------- D-m7
def _asgi_request(app, method, path, headers=None, body=b""):
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": raw_headers, "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    sent = []
    request_messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return request_messages.pop(0) if request_messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, payload


async def _dummy_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


def _upload_middleware(monkeypatch, count_limit, bytes_limit):
    from src.edge import EdgeMiddleware

    monkeypatch.setattr(settings, "EDGE_UPLOAD_DAILY_COUNT_LIMIT", count_limit)
    monkeypatch.setattr(settings, "EDGE_UPLOAD_DAILY_BYTES_LIMIT", bytes_limit)
    return EdgeMiddleware(_dummy_app)


def test_dm7_daily_upload_count_budget_429s(monkeypatch):
    mw = _upload_middleware(monkeypatch, count_limit=3, bytes_limit=10 ** 9)
    headers = {"host": "testserver", "content-length": "10"}
    for _ in range(3):
        status, _ = _asgi_request(mw, "POST", "/api/ceo/submit-report", headers=headers, body=b"x" * 10)
        assert status == 200
    status, payload = _asgi_request(mw, "POST", "/api/ceo/submit-report", headers=headers, body=b"x" * 10)
    assert status == 429
    assert "upload budget" in payload.decode().lower()


def test_dm7_daily_upload_size_budget_429s(monkeypatch):
    mw = _upload_middleware(monkeypatch, count_limit=100, bytes_limit=100)
    headers = {"host": "testserver", "content-length": "60"}
    status, _ = _asgi_request(mw, "POST", "/api/ceo/submit-report", headers=headers, body=b"x" * 60)
    assert status == 200
    status, _ = _asgi_request(mw, "POST", "/api/ceo/submit-report", headers=headers, body=b"x" * 60)
    assert status == 429


def test_dm7_non_upload_paths_do_not_consume_upload_budget(monkeypatch):
    mw = _upload_middleware(monkeypatch, count_limit=1, bytes_limit=10 ** 9)
    headers = {"host": "testserver", "content-length": "10"}
    for _ in range(5):
        status, _ = _asgi_request(mw, "POST", "/api/ops/expenses", headers=headers, body=b"x" * 10)
        assert status == 200


# ---------------------------------------------------------------- P3-5 (direct-call complement)
def test_p3_5_direct_po_create_for_other_bu_403(db):
    sup = Supplier(name="Vendor", business_unit_id=2)
    db.add(sup)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        create_po(POIn(business_unit_id=2, supplier_id=sup.id, order_date=date(2026, 10, 20),
                       lines=[POLineIn(item_name="Generator", quantity=1, unit_cost=100)]),
                  user=ACCT_BU1, db=db)
    assert exc.value.status_code == 403


def test_p3_5_direct_inventory_list_scoped_to_own_bu(db):
    _item(db, bu_id=1, name="A")
    _item(db, bu_id=2, name="B")
    out = list_inventory(user=ACCT_BU1, db=db)
    names = [i["name"] for i in out["items"]]
    assert "A" in names and "B" not in names
