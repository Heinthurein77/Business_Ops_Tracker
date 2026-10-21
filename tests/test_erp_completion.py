"""Tests for the ERP completion features (POS, quotations, returns,
invoice PDF, stock transfers, serials, staff advances, PO/journal
approval, attendance + leave balances) — in-memory SQLite.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_erp_completion.py
"""
import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, InventoryItem, JournalEntry, StaffAdvance,
)
from src.routers.accounting import (
    JournalIn, JournalLineIn, approve_journal, create_journal, post_journal,
)
from src.routers.modules import (
    StockTransferIn, TransferLineIn, cancel_stock_transfer,
    create_stock_transfer, receive_stock_transfer, send_stock_transfer,
)
from src.routers.ops import (
    AdvanceIn, AttendanceBulkIn, AttendanceRecordIn, CustomerIn, InvoiceIn,
    InvoiceLineIn, LeaveIn, LeaveReview, POIn, POLineIn, PayrollCreate,
    PosLineIn, PosSaleIn, PurchaseReturnIn, PurchaseReturnLineIn,
    QuotationIn, SalesReturnIn, SalesReturnLineIn, SerialAddIn, SupplierIn,
    add_serials, approve_advance, approve_payroll, create_advance,
    create_invoice, create_payroll, create_pos_sale, create_po,
    create_purchase_return, create_quotation, create_sales_customer,
    create_sales_return, create_supplier, convert_quotation,
    delivery_note_pdf, invoice_pdf, list_attendance, leave_balances,
    lookup_serial, order_po, approve_po, pay_payroll, pos_daily_summary,
    receive_po, review_leave, send_invoice, upsert_attendance,
    update_payroll_line, PayrollLineUpdate, void_pos_sale, create_employee,
    EmployeeIn, create_leave,
)

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None)
HO = SimpleNamespace(id=2, role="HO", business_unit_id=None)
ACCOUNTANT = SimpleNamespace(id=77, role="Accountant", business_unit_id=None)


def _provision(session, bu, template):
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts(template):
        acc = Account(business_unit_id=bu.id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        session.add(acc)
        session.flush()
        id_by_code[code] = acc.id
    session.commit()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu1 = BusinessUnit(name="Shop", code="S1")
    bu2 = BusinessUnit(name="Warehouse", code="W1")
    session.add_all([bu1, bu2])
    session.commit()
    _provision(session, bu1, "retail")
    _provision(session, bu2, "distribution")
    session.test_bu = bu1
    session.test_bu2 = bu2
    yield session
    session.close()


def bal(db, bu_id, code):
    acc = db.query(Account).filter(Account.business_unit_id == bu_id, Account.code == code).first()
    assert acc is not None, f"account {code} missing"
    total = Decimal("0")
    for e in db.query(JournalEntry).filter(JournalEntry.status == "posted",
                                           JournalEntry.business_unit_id == bu_id).all():
        for l in e.lines:
            if l.account_id == acc.id:
                total += (l.debit or 0) - (l.credit or 0)
    return total


def add_item(db, bu_id, name="Phone", stock=20, cost=500):
    item = InventoryItem(business_unit_id=bu_id, name=name,
                         current_stock=Decimal(str(stock)), unit_cost=Decimal(str(cost)))
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def stock_of(db, item_id):
    it = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    return float(it.current_stock)


# ---------------------------------------------------------------- POS (F1)

def test_pos_sale_posts_journals_and_moves_stock(db):
    bu = db.test_bu
    item = add_item(db, bu.id, stock=20, cost=500)
    sale = create_pos_sale(PosSaleIn(
        business_unit_id=bu.id, payment_method="cash", tax_rate=10,
        lines=[PosLineIn(inventory_item_id=item.id, quantity=4, unit_price=1000)],
    ), user=CHAIRMAN, db=db)
    assert sale["subtotal"] == 4000 and sale["tax_amount"] == 400 and sale["total"] == 4400
    assert sale["cogs"] == 2000 and sale["gross_profit"] == 2000
    assert stock_of(db, item.id) == 16
    assert bal(db, bu.id, "1110") == 4400    # cash in, gross
    assert bal(db, bu.id, "4100") == -4000   # sales net
    assert bal(db, bu.id, "2130") == -400    # tax payable
    assert bal(db, bu.id, "5100") == 2000    # COGS
    assert bal(db, bu.id, "1140") == -2000   # inventory out at cost


def test_pos_sale_insufficient_stock_rejected(db):
    bu = db.test_bu
    item = add_item(db, bu.id, stock=2, cost=500)
    with pytest.raises(HTTPException) as exc:
        create_pos_sale(PosSaleIn(
            business_unit_id=bu.id,
            lines=[PosLineIn(inventory_item_id=item.id, quantity=5, unit_price=1000)],
        ), user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    assert stock_of(db, item.id) == 2


def test_pos_void_reverses_journals_and_restocks(db):
    bu = db.test_bu
    item = add_item(db, bu.id, stock=20, cost=500)
    sale = create_pos_sale(PosSaleIn(
        business_unit_id=bu.id,
        lines=[PosLineIn(inventory_item_id=item.id, quantity=4, unit_price=1000)],
    ), user=CHAIRMAN, db=db)
    out = void_pos_sale(sale["id"], user=CHAIRMAN, db=db)
    assert out["status"] == "void"
    assert stock_of(db, item.id) == 20
    for code in ("1110", "4100", "5100", "1140"):
        assert bal(db, bu.id, code) == 0, code
    summary = pos_daily_summary(business_unit_id=bu.id, on=date.today(), user=CHAIRMAN, db=db)
    assert summary["sale_count"] == 0  # voided sales excluded


def test_pos_daily_summary_by_payment_method(db):
    bu = db.test_bu
    item = add_item(db, bu.id, stock=50, cost=400)
    create_pos_sale(PosSaleIn(business_unit_id=bu.id, payment_method="cash",
                              lines=[PosLineIn(inventory_item_id=item.id, quantity=2, unit_price=1000)]),
                    user=CHAIRMAN, db=db)
    create_pos_sale(PosSaleIn(business_unit_id=bu.id, payment_method="bank",
                              lines=[PosLineIn(inventory_item_id=item.id, quantity=3, unit_price=1000)]),
                    user=CHAIRMAN, db=db)
    s = pos_daily_summary(business_unit_id=bu.id, on=date.today(), user=CHAIRMAN, db=db)
    assert s["sale_count"] == 2 and s["total"] == 5000
    assert s["by_payment_method"]["cash"]["total"] == 2000
    assert s["by_payment_method"]["bank"]["total"] == 3000
    assert s["cogs"] == 2000 and s["gross_profit"] == 3000
    assert bal(db, bu.id, "1120") == 3000  # bank sales hit the bank account


# ---------------------------------------------------------------- quotations (F2)

def _customer(db, bu_id, name="Hotel"):
    return create_sales_customer(CustomerIn(business_unit_id=bu_id, name=name), user=CHAIRMAN, db=db)


def test_quotation_convert_creates_draft_invoice_no_journal(db):
    bu = db.test_bu
    cust = _customer(db, bu.id)
    q = create_quotation(QuotationIn(
        business_unit_id=bu.id, customer_id=cust["id"], quote_date=date.today(), tax_rate=5,
        lines=[InvoiceLineIn(description="Goods", quantity=10, unit_price=1000)],
    ), user=CHAIRMAN, db=db)
    assert q["total"] == 10500 and q["status"] == "draft"
    assert db.query(JournalEntry).count() == 0  # quotations never post
    inv = convert_quotation(q["id"], user=CHAIRMAN, db=db)
    assert inv["status"] == "draft" and inv["total"] == 10500
    assert inv["customer_id"] == cust["id"] and len(inv["lines"]) == 1
    assert db.query(JournalEntry).count() == 0  # still nothing posted
    from src.routers.ops import list_quotations
    after = list_quotations(business_unit_id=bu.id, user=CHAIRMAN, db=db)["items"][0]
    assert after["status"] == "converted" and after["converted_invoice_id"] == inv["id"]
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    assert bal(db, bu.id, "1130") == 10500  # journal only once the invoice is sent


# ---------------------------------------------------------------- returns (F3)

def _sent_invoice(db, bu_id, cust_id, qty=10, price=1000, item_id=None):
    inv = create_invoice(InvoiceIn(
        business_unit_id=bu_id, customer_id=cust_id, invoice_date=date.today(), tax_rate=5,
        lines=[InvoiceLineIn(description="Goods", quantity=qty, unit_price=price,
                             inventory_item_id=item_id)],
    ), user=CHAIRMAN, db=db)
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    return inv


def test_sales_return_reduces_balance_restocks_and_guards_qty(db):
    bu = db.test_bu
    cust = _customer(db, bu.id)
    item = add_item(db, bu.id, stock=0, cost=600)
    inv = _sent_invoice(db, bu.id, cust["id"], item_id=item.id)
    line_id = inv["lines"][0]["id"]
    r = create_sales_return(SalesReturnIn(
        invoice_id=inv["id"],
        lines=[SalesReturnLineIn(invoice_line_id=line_id, quantity=4)],
    ), user=CHAIRMAN, db=db)
    assert r["total"] == 4200  # 4 x 1000 + 5% tax
    assert stock_of(db, item.id) == 4  # restocked
    from src.routers.ops import invoice_amounts, _invoice_or_404
    a = invoice_amounts(db, _invoice_or_404(db, inv["id"]))
    assert a["returns"] == 4200 and a["balance"] == 10500 - 4200
    with pytest.raises(HTTPException) as exc:  # only 6 left to return
        create_sales_return(SalesReturnIn(
            invoice_id=inv["id"],
            lines=[SalesReturnLineIn(invoice_line_id=line_id, quantity=7)],
        ), user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    r2 = create_sales_return(SalesReturnIn(
        invoice_id=inv["id"],
        lines=[SalesReturnLineIn(invoice_line_id=line_id, quantity=6)],
    ), user=CHAIRMAN, db=db)
    assert r2["return_no"].startswith("SR-")
    inv_now = _invoice_or_404(db, inv["id"])
    assert inv_now.status == "paid"  # fully returned -> nothing owed
    assert bal(db, bu.id, "1130") == 0  # AR fully reversed


def test_purchase_return_posts_debit_note_and_guards(db):
    bu = db.test_bu
    sup = create_supplier(SupplierIn(name="Vendor", business_unit_id=None), user=CHAIRMAN, db=db)
    po = create_po(POIn(business_unit_id=bu.id, supplier_id=sup["id"], order_date=date.today(),
                        lines=[POLineIn(item_name="Rice", quantity=10, unit_cost=1000)]),
                   user=CHAIRMAN, db=db)
    receive_po(po["id"], user=CHAIRMAN, db=db)
    po_line_id = po["lines"][0]["id"]
    assert bal(db, bu.id, "2110") == -10000
    r = create_purchase_return(PurchaseReturnIn(
        po_id=po["id"], lines=[PurchaseReturnLineIn(po_line_id=po_line_id, quantity=3)],
    ), user=CHAIRMAN, db=db)
    assert r["total"] == 3000
    assert bal(db, bu.id, "2110") == -7000  # AP reduced
    assert bal(db, bu.id, "1140") == 7000   # inventory reduced
    with pytest.raises(HTTPException) as exc:
        create_purchase_return(PurchaseReturnIn(
            po_id=po["id"], lines=[PurchaseReturnLineIn(po_line_id=po_line_id, quantity=8)],
        ), user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()


# ---------------------------------------------------------------- PDFs (F4 + delivery note)

async def _collect(resp):
    chunks = []
    async for c in resp.body_iterator:
        chunks.append(c if isinstance(c, bytes) else c.encode())
    return b"".join(chunks)


def test_invoice_pdf_and_delivery_note(db):
    bu = db.test_bu
    cust = _customer(db, bu.id)
    inv = _sent_invoice(db, bu.id, cust["id"])
    resp = invoice_pdf(inv["id"], user=CHAIRMAN, db=db)
    assert resp.media_type == "application/pdf"
    body = asyncio.run(_collect(resp))
    assert body.startswith(b"%PDF") and len(body) > 500
    dn = delivery_note_pdf(inv["id"], user=CHAIRMAN, db=db)
    assert dn.media_type == "application/pdf"
    body2 = asyncio.run(_collect(dn))
    assert body2.startswith(b"%PDF")


# ---------------------------------------------------------------- stock transfers (F5)

def test_stock_transfer_full_cycle(db):
    bu1, bu2 = db.test_bu, db.test_bu2
    item = add_item(db, bu1.id, name="Rice 5kg", stock=10, cost=500)
    t = create_stock_transfer(StockTransferIn(
        from_business_unit_id=bu1.id, to_business_unit_id=bu2.id,
        lines=[TransferLineIn(inventory_item_id=item.id, quantity=4)],
    ), user=CHAIRMAN, db=db)
    assert t["status"] == "draft"
    t = send_stock_transfer(t["id"], user=CHAIRMAN, db=db)
    assert t["status"] == "in_transit" and t["total_cost"] == 2000
    assert stock_of(db, item.id) == 6
    assert bal(db, bu1.id, "1180") == 2000   # source: intercompany receivable
    assert bal(db, bu1.id, "1140") == -2000  # source inventory out at cost
    t = receive_stock_transfer(t["id"], user=CHAIRMAN, db=db)
    assert t["status"] == "received"
    dst_id = t["lines"][0]["dest_inventory_item_id"]
    assert stock_of(db, dst_id) == 4
    assert bal(db, bu2.id, "1140") == 2000
    assert bal(db, bu2.id, "2180") == -2000  # destination: intercompany payable
    with pytest.raises(HTTPException):  # received transfers cannot cancel
        cancel_stock_transfer(t["id"], user=CHAIRMAN, db=db)
    db.rollback()


def test_stock_transfer_cancel_in_transit_restocks_and_reverses(db):
    bu1, bu2 = db.test_bu, db.test_bu2
    item = add_item(db, bu1.id, name="Oil 1L", stock=10, cost=500)
    t = create_stock_transfer(StockTransferIn(
        from_business_unit_id=bu1.id, to_business_unit_id=bu2.id,
        lines=[TransferLineIn(inventory_item_id=item.id, quantity=4)],
    ), user=CHAIRMAN, db=db)
    send_stock_transfer(t["id"], user=CHAIRMAN, db=db)
    assert stock_of(db, item.id) == 6
    out = cancel_stock_transfer(t["id"], user=CHAIRMAN, db=db)
    assert out["status"] == "cancelled"
    assert stock_of(db, item.id) == 10
    assert bal(db, bu1.id, "1180") == 0 and bal(db, bu1.id, "1140") == 0


# ---------------------------------------------------------------- serials (F6)

def test_serials_duplicate_guard_and_invoice_sale(db):
    bu = db.test_bu
    item = add_item(db, bu.id, stock=5, cost=800)
    out = add_serials(item.id, SerialAddIn(serial_nos=["IMEI-1", "IMEI-2"],
                                           warranty_until=date(2027, 10, 1)),
                      user=CHAIRMAN, db=db)
    assert out["count"] == 2
    with pytest.raises(HTTPException) as exc:
        add_serials(item.id, SerialAddIn(serial_nos=["IMEI-1"]), user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    cust = _customer(db, bu.id)
    inv = create_invoice(InvoiceIn(
        business_unit_id=bu.id, customer_id=cust["id"], invoice_date=date.today(), tax_rate=0,
        lines=[InvoiceLineIn(description="Phone", quantity=1, unit_price=1500,
                             inventory_item_id=item.id, serial_nos=["IMEI-1"])],
    ), user=CHAIRMAN, db=db)
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    found = lookup_serial("IMEI-1", user=CHAIRMAN, db=db)
    assert found["count"] == 1
    row = found["items"][0]
    assert row["status"] == "sold" and row["sales_invoice_id"] == inv["id"]
    assert row["warranty_until"] == "2027-10-01" and row["item_name"] == "Phone"
    # a sold serial cannot be sold again
    inv2 = create_invoice(InvoiceIn(
        business_unit_id=bu.id, customer_id=cust["id"], invoice_date=date.today(), tax_rate=0,
        lines=[InvoiceLineIn(description="Phone", quantity=1, unit_price=1500,
                             inventory_item_id=item.id, serial_nos=["IMEI-1"])],
    ), user=CHAIRMAN, db=db)
    with pytest.raises(HTTPException) as exc:
        send_invoice(inv2["id"], user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()


# ---------------------------------------------------------------- staff advances (F7)

def test_staff_advance_payroll_cycle(db):
    bu = db.test_bu
    emp = create_employee(EmployeeIn(business_unit_id=bu.id, name="Aung Min",
                                     base_salary=500000), user=CHAIRMAN, db=db)
    adv = create_advance(AdvanceIn(employee_id=emp["id"], amount=300000,
                                   monthly_deduction=100000), user=CHAIRMAN, db=db)
    assert adv["status"] == "pending" and adv["remaining_amount"] == 300000
    adv = approve_advance(adv["id"], user=CHAIRMAN, db=db)
    assert adv["status"] == "approved" and adv["journal_entry_id"] is not None
    assert bal(db, bu.id, "1240") == 300000   # Dr staff advances
    assert bal(db, bu.id, "1110") == -300000  # Cr cash disbursed

    # Month 1: manual deductions 20,000 + advance repayment 100,000.
    run = create_payroll(PayrollCreate(business_unit_id=bu.id, period="2026-10"),
                         user=CHAIRMAN, db=db)
    line = run["lines"][0]
    run = update_payroll_line(run["id"], line["id"], PayrollLineUpdate(deductions=20000),
                              user=CHAIRMAN, db=db)
    approve_payroll(run["id"], user=CHAIRMAN, db=db)
    run = pay_payroll(run["id"], user=CHAIRMAN, db=db)
    assert run["lines"][0]["advance_deduction"] == 100000
    assert run["total_deductions"] == 120000 and run["total_net"] == 380000
    assert bal(db, bu.id, "6200") == 500000
    assert bal(db, bu.id, "2120") == -20000   # only the manual portion
    assert bal(db, bu.id, "1240") == 200000   # advance portion credited here
    adv_now = db.query(StaffAdvance).first()
    assert float(adv_now.remaining_amount) == 200000 and adv_now.status == "approved"

    # Months 2 and 3 settle the advance.
    for period in ("2026-11", "2026-12"):
        r = create_payroll(PayrollCreate(business_unit_id=bu.id, period=period),
                           user=CHAIRMAN, db=db)
        approve_payroll(r["id"], user=CHAIRMAN, db=db)
        pay_payroll(r["id"], user=CHAIRMAN, db=db)
    adv_now = db.query(StaffAdvance).first()
    assert float(adv_now.remaining_amount) == 0 and adv_now.status == "settled"
    assert bal(db, bu.id, "1240") == 0


# ---------------------------------------------------------------- approvals (F8)

def test_po_two_stage_approval_gate(db):
    bu = db.test_bu
    sup = create_supplier(SupplierIn(name="BigVendor", business_unit_id=None), user=CHAIRMAN, db=db)
    po = create_po(POIn(business_unit_id=bu.id, supplier_id=sup["id"], order_date=date.today(),
                        lines=[POLineIn(item_name="Generator", quantity=1, unit_cost=2000000)]),
                   user=CHAIRMAN, db=db)
    assert po["approval_stage"] == "first"
    with pytest.raises(HTTPException) as exc:  # cannot order while awaiting approval
        order_po(po["id"], user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    with pytest.raises(HTTPException) as exc:  # Chairman cannot clear stage 1
        approve_po(po["id"], user=CHAIRMAN, db=db)
    assert exc.value.status_code == 403
    db.rollback()
    po = approve_po(po["id"], user=HO, db=db)
    assert po["approval_stage"] == "final"
    with pytest.raises(HTTPException):  # HO cannot give final approval
        approve_po(po["id"], user=HO, db=db)
    db.rollback()
    po = approve_po(po["id"], user=CHAIRMAN, db=db)
    assert po["approval_stage"] == "approved"
    out = order_po(po["id"], user=CHAIRMAN, db=db)
    assert out["status"] == "ordered"
    # Small POs are never gated.
    po2 = create_po(POIn(business_unit_id=bu.id, supplier_id=sup["id"], order_date=date.today(),
                         lines=[POLineIn(item_name="Paper", quantity=10, unit_cost=1000)]),
                    user=CHAIRMAN, db=db)
    assert po2["approval_stage"] is None
    assert order_po(po2["id"], user=CHAIRMAN, db=db)["status"] == "ordered"


def test_journal_approval_gate_for_accountant(db):
    bu = db.test_bu

    def acc(code):
        return db.query(Account).filter(Account.business_unit_id == bu.id,
                                        Account.code == code).first()

    big = create_journal(JournalIn(
        business_unit_id=bu.id, entry_date=date.today(), description="Big manual entry",
        lines=[JournalLineIn(account_id=acc("6600").id, debit=2000000),
               JournalLineIn(account_id=acc("1110").id, credit=2000000)],
    ), user=ACCOUNTANT, db=db)
    assert big["approval_status"] == "pending"
    with pytest.raises(HTTPException) as exc:  # posting blocked while pending
        post_journal(big["id"], user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    with pytest.raises(HTTPException) as exc:  # creator cannot approve own entry
        approve_journal(big["id"], user=SimpleNamespace(id=77, role="HO", business_unit_id=None), db=db)
    assert exc.value.status_code == 403
    db.rollback()
    out = approve_journal(big["id"], user=CHAIRMAN, db=db)
    assert out["approval_status"] == "approved"
    posted = post_journal(big["id"], user=ACCOUNTANT, db=db)
    assert posted["status"] == "posted"
    assert bal(db, bu.id, "6600") == 2000000
    # Small accountant entries and admin entries need no approval.
    small = create_journal(JournalIn(
        business_unit_id=bu.id, entry_date=date.today(), description="Small",
        lines=[JournalLineIn(account_id=acc("6600").id, debit=5000),
               JournalLineIn(account_id=acc("1110").id, credit=5000)],
    ), user=ACCOUNTANT, db=db)
    assert small["approval_status"] == "not_required"
    assert post_journal(small["id"], user=ACCOUNTANT, db=db)["status"] == "posted"


# ---------------------------------------------------------------- attendance + leave balances (F9)

def test_attendance_bulk_upsert_and_summary(db):
    bu = db.test_bu
    emp = create_employee(EmployeeIn(business_unit_id=bu.id, name="Su Su",
                                     base_salary=300000), user=CHAIRMAN, db=db)
    out = upsert_attendance(AttendanceBulkIn(records=[
        AttendanceRecordIn(employee_id=emp["id"], record_date=date(2026, 10, 5), status="present"),
        AttendanceRecordIn(employee_id=emp["id"], record_date=date(2026, 10, 6), status="late"),
        AttendanceRecordIn(employee_id=emp["id"], record_date=date(2026, 10, 7), status="present"),
    ]), user=CHAIRMAN, db=db)
    assert out["saved"] == 3
    # Upsert: same employee + date updates instead of duplicating.
    upsert_attendance(AttendanceBulkIn(records=[
        AttendanceRecordIn(employee_id=emp["id"], record_date=date(2026, 10, 6), status="half_day"),
    ]), user=CHAIRMAN, db=db)
    res = list_attendance(business_unit_id=bu.id, month="2026-10", user=CHAIRMAN, db=db)
    assert len(res["items"]) == 3
    counts = res["summary"][0]
    assert counts["present"] == 2 and counts["half_day"] == 1 and counts["late"] == 0


def test_leave_balances_from_approved_requests(db):
    bu = db.test_bu
    emp = create_employee(EmployeeIn(business_unit_id=bu.id, name="May May",
                                     base_salary=300000), user=CHAIRMAN, db=db)
    lv = create_leave(LeaveIn(employee_id=emp["id"], leave_type="annual",
                              start_date=date(2026, 11, 2), end_date=date(2026, 11, 4)),
                      user=CHAIRMAN, db=db)
    review_leave(lv["id"], LeaveReview(decision="approved"), user=CHAIRMAN, db=db)
    create_leave(LeaveIn(employee_id=emp["id"], leave_type="casual",
                         start_date=date(2026, 12, 24), end_date=date(2026, 12, 24)),
                 user=CHAIRMAN, db=db)  # stays pending -> not counted
    res = leave_balances(business_unit_id=bu.id, year=2026, user=CHAIRMAN, db=db)
    row = res["items"][0]
    assert row["balances"]["annual"] == {"entitlement": 10, "used": 3, "remaining": 7}
    assert row["balances"]["casual"]["used"] == 0
    assert row["balances"]["sick"] == {"entitlement": 30, "used": 0, "remaining": 30}
