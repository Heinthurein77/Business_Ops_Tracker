"""R-3 / T-2: audit trail on money/stock paths, D-14 supplier scope + anomaly flag.

One test per high-value action asserting the audit row (actor, action,
target id) exists after the call, plus a meta-test that fails if a mutating
endpoint in ops.py / modules.py / accounting.py has no audit call.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_audit_trail.py -q
"""
import ast
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from src.auth import hash_password
from src.models import (
    Account, ActivityLog, AuditLog, Base, BusinessUnit, DailyFinancial,
    InventoryItem, JournalEntry, Notification, Supplier, User,
)
from src.routers import accounting as acct
from src.routers.accounting import JournalLineIn, provision_bu
from src.routers.ceo import submit_report as ceo_submit_report
from src.routers.modules import (
    BomIn, ProductionIn, StockTransferIn, TransferLineIn,
    complete_production, create_bom, create_production, create_stock_transfer,
    receive_stock_transfer, send_stock_transfer,
)
from src.routers.ops import (
    CustomerIn, EmployeeIn, ExpenseIn, ExpenseReviewIn, InvoiceIn, InvoiceLineIn,
    MovementIn, PaymentIn, PayrollCreate, POIn, POLineIn, SupplierIn,
    TransferIn, create_employee, create_invoice, create_payroll, create_po,
    create_sales_customer, create_supplier, delete_supplier, pay_payroll,
    pay_po, receive_po, record_movement, record_payment, review_expense,
    send_invoice, submit_expense, transfer_stock, approve_payroll,
)
from src.utils import today_local

PASSWORD = "TestPass123!"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu1 = BusinessUnit(id=1, code="BU1", name="Shop One")
    bu2 = BusinessUnit(id=2, code="BU2", name="Shop Two")
    session.add_all([bu1, bu2])
    chairman = User(id=701, username="chairman", hashed_password=hash_password(PASSWORD),
                    role="Chairman", is_active=True)
    ho = User(id=702, username="ho_user", hashed_password=hash_password(PASSWORD),
              role="HO", is_active=True)
    ceo = User(id=703, username="ceo_one", hashed_password=hash_password(PASSWORD),
               role="CEO", business_unit_id=1, is_active=True)
    session.add_all([chairman, ho, ceo])
    session.commit()
    admin = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)
    provision_bu(1, template="retail", user=admin, db=session)
    provision_bu(2, template="retail", user=admin, db=session)
    sup = Supplier(name="Wholesale Co")
    session.add(sup)
    session.commit()
    session.test_bu = bu1
    session.test_bu2 = bu2
    session.test_supplier = sup
    session.test_users = {"chairman": chairman, "ho": ho, "ceo": ceo}
    yield session
    session.close()
    engine.dispose()


def _audit(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


def _assert_audited(db, action, user, detail_fragment):
    rows = _audit(db, action)
    assert len(rows) == 1, f"expected exactly one {action} audit row, got {len(rows)}"
    row = rows[0]
    assert row.user_id == user.id
    assert row.username == user.username
    assert detail_fragment in (row.details or "")
    return row


def _jline(db, code, debit=0, credit=0):
    acc = db.query(Account).filter(Account.business_unit_id == 1, Account.code == code).one()
    return JournalLineIn(account_id=acc.id, debit=Decimal(str(debit)), credit=Decimal(str(credit)))


def _make_po(db, chairman):
    return create_po(
        POIn(business_unit_id=1, supplier_id=db.test_supplier.id,
             order_date=date(2026, 10, 20),
             lines=[POLineIn(item_name="Rice 5kg", quantity=10, unit_cost=1000)]),
        user=chairman, db=db)


# ------------------------------------------------------------------ PO lifecycle

def test_create_po_audited(db):
    chairman = db.test_users["chairman"]
    po = _make_po(db, chairman)
    _assert_audited(db, "CREATE_PO", chairman, f"id={po['id']}")


def test_receive_and_pay_po_audited(db):
    chairman = db.test_users["chairman"]
    po = _make_po(db, chairman)
    receive_po(po["id"], user=chairman, db=db)
    _assert_audited(db, "RECEIVE_PO", chairman, f"po_id={po['id']}")
    pay_po(po["id"], user=chairman, db=db)
    _assert_audited(db, "PAY_PO", chairman, f"po_id={po['id']}")


# ------------------------------------------------------------------ journal lifecycle

def test_journal_lifecycle_audited(db):
    chairman = db.test_users["chairman"]
    entry = acct.create_journal(
        acct.JournalIn(business_unit_id=1, entry_date=date(2026, 10, 5), description="sale",
                       lines=[_jline(db, "1110", debit=1000), _jline(db, "4100", credit=1000)]),
        user=chairman, db=db)
    _assert_audited(db, "CREATE_JOURNAL", chairman, f"id={entry['id']}")
    acct.post_journal(entry["id"], user=chairman, db=db)
    _assert_audited(db, "POST_JOURNAL", chairman, f"entry_id={entry['id']}")
    acct.reverse_journal(entry["id"], user=chairman, db=db)
    _assert_audited(db, "REVERSE_JOURNAL", chairman, f"entry_id={entry['id']}")
    draft = acct.create_journal(
        acct.JournalIn(business_unit_id=1, entry_date=date(2026, 10, 6), description="draft",
                       lines=[_jline(db, "1110", debit=500), _jline(db, "4100", credit=500)]),
        user=chairman, db=db)
    acct.delete_journal(draft["id"], user=chairman, db=db)
    _assert_audited(db, "DELETE_JOURNAL", chairman, f"entry_id={draft['id']}")


def test_period_close_and_reopen_audited(db):
    chairman = db.test_users["chairman"]
    acct.close_period(acct.PeriodAction(business_unit_id=1, period="2026-10"),
                      user=chairman, db=db)
    assert len(_audit(db, "FISCAL_PERIOD_CLOSED")) == 1
    acct.reopen_period(acct.PeriodAction(business_unit_id=1, period="2026-10"),
                       user=chairman, db=db)
    _assert_audited(db, "REOPEN_PERIOD", chairman, "period=2026-10")


# ------------------------------------------------------------------ sales / payroll

def test_invoice_send_and_payment_audited(db):
    chairman = db.test_users["chairman"]
    cust = create_sales_customer(CustomerIn(business_unit_id=1, name="Buyer"),
                                 user=chairman, db=db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 5),
        tax_rate=0, lines=[InvoiceLineIn(description="Sale", quantity=1, unit_price=100000)]),
        user=chairman, db=db)
    _assert_audited(db, "CREATE_INVOICE", chairman, f"id={inv['id']}")
    send_invoice(inv["id"], user=chairman, db=db)
    _assert_audited(db, "SEND_INVOICE", chairman, f"invoice_id={inv['id']}")
    record_payment(inv["id"], PaymentIn(amount=100000, payment_date=date(2026, 10, 7)),
                   user=chairman, db=db)
    _assert_audited(db, "RECORD_PAYMENT", chairman, f"invoice_id={inv['id']}")


def test_payroll_pay_audited(db):
    chairman = db.test_users["chairman"]
    create_employee(EmployeeIn(business_unit_id=1, name="Aung Aung", base_salary=500000),
                    user=chairman, db=db)
    run = create_payroll(PayrollCreate(business_unit_id=1, period="2026-10"),
                         user=chairman, db=db)
    _assert_audited(db, "CREATE_PAYROLL", chairman, f"id={run['id']}")
    approve_payroll(run["id"], user=chairman, db=db)
    _assert_audited(db, "APPROVE_PAYROLL", chairman, f"run_id={run['id']}")
    pay_payroll(run["id"], user=chairman, db=db)
    _assert_audited(db, "PAY_PAYROLL", chairman, f"run_id={run['id']}")


def test_expense_submit_and_review_audited(db):
    chairman = db.test_users["chairman"]
    ceo = db.test_users["ceo"]
    exp = submit_expense(
        ExpenseIn(business_unit_id=1, title="Fuel", amount=50000, category="transport"),
        user=ceo, db=db)
    _assert_audited(db, "SUBMIT_EXPENSE", ceo, f"id={exp['id']}")
    review_expense(exp["id"], ExpenseReviewIn(decision="approved"), user=chairman, db=db)
    _assert_audited(db, "REVIEW_EXPENSE", chairman, f"expense_id={exp['id']}")


# ------------------------------------------------------------------ stock

def test_stock_movement_and_transfer_audited(db):
    chairman = db.test_users["chairman"]
    item = InventoryItem(business_unit_id=1, name="Soap", current_stock=Decimal("30"),
                         unit_cost=Decimal("500"))
    db.add(item)
    db.commit()
    record_movement(item.id, MovementIn(movement_type="in", quantity=5, reason="count"),
                    user=chairman, db=db)
    _assert_audited(db, "RECORD_MOVEMENT", chairman, f"item_id={item.id}")
    transfer_stock(item.id, TransferIn(to_business_unit_id=2, quantity=12),
                   user=chairman, db=db)
    _assert_audited(db, "TRANSFER_STOCK", chairman, f"item_id={item.id}")
    t = create_stock_transfer(StockTransferIn(
        from_business_unit_id=1, to_business_unit_id=2,
        lines=[TransferLineIn(inventory_item_id=item.id, quantity=2)]),
        user=chairman, db=db)
    _assert_audited(db, "CREATE_STOCK_TRANSFER", chairman, f"id={t['id']}")
    send_stock_transfer(t["id"], user=chairman, db=db)
    _assert_audited(db, "SEND_STOCK_TRANSFER", chairman, f"transfer_id={t['id']}")
    receive_stock_transfer(t["id"], user=chairman, db=db)
    _assert_audited(db, "RECEIVE_STOCK_TRANSFER", chairman, f"transfer_id={t['id']}")


def test_production_completion_audited(db):
    chairman = db.test_users["chairman"]
    wood = InventoryItem(business_unit_id=1, name="Teak", current_stock=Decimal("100"),
                         unit_cost=Decimal("1000"))
    chair = InventoryItem(business_unit_id=1, name="Chair", current_stock=Decimal("0"),
                          unit_cost=Decimal("0"))
    db.add_all([wood, chair])
    db.commit()
    bom = create_bom(BomIn(business_unit_id=1, finished_item_id=chair.id,
                           components=[{"component_item_id": wood.id, "quantity_per_unit": 4}]),
                     user=chairman, db=db)
    order = create_production(ProductionIn(bom_id=bom["id"], quantity=10,
                                           labour_cost=20000, overhead_cost=5000),
                              user=chairman, db=db)
    complete_production(order["id"], user=chairman, db=db)
    _assert_audited(db, "COMPLETE_PRODUCTION", chairman, f"order_id={order['id']}")


# ------------------------------------------------------------------ intercompany

def test_intercompany_create_and_settle_audited(db):
    chairman = db.test_users["chairman"]
    t = acct.create_intercompany(acct.IntercompanyIn(
        from_business_unit_id=1, to_business_unit_id=2, amount=300000,
        description="Shared logistics", txn_date=date(2026, 10, 10),
        buyer_account_code="6400"), user=chairman, db=db)
    _assert_audited(db, "CREATE_INTERCOMPANY", chairman, f"id={t['id']}")
    acct.settle_intercompany(t["id"], user=chairman, db=db)
    _assert_audited(db, "SETTLE_INTERCOMPANY", chairman, f"txn_id={t['id']}")


# ------------------------------------------------------------------ D-14(a): supplier scope

def test_ceo_cannot_create_group_shared_supplier(db):
    # A BU-assigned CEO's create is pinned to their own BU first, so the
    # group-shared path is unreachable for them; a legacy CEO with no BU
    # (the only non-admin who could reach business_unit_id=None) gets 403.
    legacy_ceo = User(id=704, username="ceo_legacy", hashed_password=hash_password(PASSWORD),
                      role="CEO", business_unit_id=None, is_active=True)
    db.add(legacy_ceo)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        create_supplier(SupplierIn(name="Shared", business_unit_id=None), user=legacy_ceo, db=db)
    assert exc.value.status_code == 403
    db.rollback()
    assert _audit(db, "CREATE_SUPPLIER") == []
    # ...and the pinned CEO never creates a group-shared row either:
    ceo = db.test_users["ceo"]
    out = create_supplier(SupplierIn(name="Shared", business_unit_id=None), user=ceo, db=db)
    assert out["business_unit_id"] == 1


def test_ceo_supplier_create_forced_to_own_bu(db):
    ceo = db.test_users["ceo"]
    out = create_supplier(SupplierIn(name="Local", business_unit_id=2), user=ceo, db=db)
    assert out["business_unit_id"] == 1
    _assert_audited(db, "CREATE_SUPPLIER", ceo, f"id={out['id']}")


# ------------------------------------------------------------------ D-14(b): anomaly flag

def _ceo_submit(db, user, **overrides):
    import src.routers.ceo as ceo_router
    ceo_router._report_submit_timestamps.clear()
    payload = dict(
        report_date="", revenue="100000", cash_flow="0", receivable="0", payable="0",
        other_amount="", financial_other_notes="", meetings_attended="Standup",
        meetings_count="", site_visits="", key_decisions="", key_achievement="",
        next_day_schedule="", problems_today="", help_needed="", gym_percent="",
        swimming_percent="", cycle_percent="", activity_other_notes="",
        daily_steps="", heart_rate="", sleep_hours="", bp_systolic="", bp_diastolic="",
        stress_level="", energy_mood="", weight_kg="", is_sick="false",
        health_gym_percent=None, health_swimming_percent=None, health_cycle_percent=None,
        health_other_notes="", event_flag="false", meeting_flag="false",
        golf_flag="false", social_other_notes="", attachment=None,
    )
    payload.update(overrides)
    request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
    return ceo_submit_report(request=request, user=user, db=db, **payload)


def _seed_history(db, revenue, days=14):
    today = today_local()
    for i in range(1, days + 1):
        db.add(DailyFinancial(business_unit_id=1, report_date=today - timedelta(days=i),
                              revenue=Decimal(str(revenue)), expenses=Decimal("0"),
                              net_profit=Decimal("0"), cash_in_hand=Decimal("0")))
    db.commit()


def test_revenue_anomaly_flagged_and_ho_notified(db):
    ceo = db.test_users["ceo"]
    ho = db.test_users["ho"]
    _seed_history(db, 100000)
    out = _ceo_submit(db, ceo, revenue="200000")
    assert out == {"ok": True}
    fin = db.query(DailyFinancial).filter(
        DailyFinancial.business_unit_id == 1,
        DailyFinancial.report_date == today_local()).one()
    assert fin.revenue_anomaly is True
    notes = db.query(Notification).filter(
        Notification.user_id == ho.id, Notification.type == "alert").all()
    assert len(notes) == 1
    assert "Revenue anomaly" in notes[0].title
    assert "200,000" in notes[0].message


def test_revenue_within_band_not_flagged(db):
    ceo = db.test_users["ceo"]
    ho = db.test_users["ho"]
    _seed_history(db, 100000)
    assert _ceo_submit(db, ceo, revenue="110000") == {"ok": True}
    fin = db.query(DailyFinancial).filter(
        DailyFinancial.business_unit_id == 1,
        DailyFinancial.report_date == today_local()).one()
    assert fin.revenue_anomaly is False
    assert db.query(Notification).filter(
        Notification.user_id == ho.id, Notification.type == "alert").count() == 0


# ------------------------------------------------------------------ T-2 meta-test

# Endpoints whose mutation is genuinely not auditable: flipping a
# notification's own read state. Everything else that mutates must call
# the audit wrapper.
_META_ALLOWLIST = {
    ("POST", "/notifications/{notification_id}/read"),
    ("POST", "/notifications/read-all"),
}


def _mutating_handlers_without_audit(path):
    src = Path(path).read_text()
    tree = ast.parse(src)
    missing = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            method = dec.func.attr
            if method not in ("post", "patch", "put", "delete"):
                continue
            route = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else "?"
            seg = ast.get_source_segment(src, node) or ""
            if "log_audit" not in seg and (method.upper(), route) not in _META_ALLOWLIST:
                missing.append(f"{method.upper()} {route} ({node.name})")
    return missing


def test_meta_every_mutating_endpoint_audits():
    root = Path(__file__).resolve().parents[1] / "src" / "routers"
    missing = []
    for name in ("ops.py", "modules.py", "accounting.py"):
        missing += [f"{name}: {m}" for m in _mutating_handlers_without_audit(root / name)]
    assert missing == [], f"mutating endpoints without an audit call: {missing}"
