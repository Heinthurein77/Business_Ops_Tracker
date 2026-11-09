"""Regression tests for the BOT review ops/modules batch (workstream B):
C1, C2, C3, M-B4/B7/B8, M-C1/C2/C3/C4/C5/C6/C7/C8/C9, M-C15/C16, M-D2/D7,
m3, m7, M-A2 — in-memory SQLite, calling router functions directly.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, InventoryItem, JournalEntry, StockBatch,
    Supplier,
)
from src.routers.accounting import provision_bu
from src.routers.modules import (
    BatchIn, BomIn, LeaseIn, ProductionIn, SellUnitIn, ShipmentIn, UnitIn,
    allocate_installments, complete_production, consume_batches_fefo,
    create_bom, create_lease, create_production, create_shipment,
    create_unit, list_stock_transfers, receive_batch, sell_unit,
)
from src.routers.ops import (
    AdvanceIn, CustomerIn, EmployeeIn, InvoiceIn, InvoiceLineIn, LeaveIn,
    LeaveReview, MovementIn, PaymentIn, POIn, POLineIn, PayrollCreate,
    PayrollLineUpdate, PosLineIn, PosSaleIn, QuotationIn, QuotationStatusIn,
    SalesReturnIn, SalesReturnLineIn, SerialAddIn, SupplierIn,
    add_serials, approve_advance, approve_payroll, approve_po, bu_scope,
    cancel_invoice, convert_quotation, create_advance, create_employee,
    create_invoice, create_leave, create_payroll, create_po,
    create_pos_sale, create_quotation, create_sales_customer,
    create_sales_return, create_supplier, delete_supplier, list_inventory,
    order_po, pay_payroll, receive_po, record_movement, record_payment,
    review_leave, send_invoice, set_quotation_status, transfer_stock,
    TransferIn, update_payroll_line, update_po, POUpdate,
)
from src.utils import _sanitize_excel_cell

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None)
HO = SimpleNamespace(id=2, role="HO", business_unit_id=None)
ACCT_BU1 = SimpleNamespace(id=3, role="Accountant", business_unit_id=1)
ACCT_BU2 = SimpleNamespace(id=4, role="Accountant", business_unit_id=2)
ACCT_LEGACY = SimpleNamespace(id=5, role="Accountant", business_unit_id=None)
CEO_BU1 = SimpleNamespace(id=6, role="CEO", business_unit_id=1)


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


def _supplier(db, bu_id=None, name="Vendor"):
    s = Supplier(name=name, business_unit_id=bu_id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _item(db, bu_id=1, name="Widget", stock=10, cost=500, **kw):
    it = InventoryItem(business_unit_id=bu_id, name=name,
                       current_stock=Decimal(str(stock)), unit_cost=Decimal(str(cost)), **kw)
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _customer(db, bu_id=1, name="Buyer"):
    return create_sales_customer(CustomerIn(business_unit_id=bu_id, name=name),
                                 user=CHAIRMAN, db=db)


def _po(db, supplier_id, total=2000000, bu_id=1):
    return create_po(POIn(business_unit_id=bu_id, supplier_id=supplier_id,
                          order_date=date(2026, 10, 20),
                          lines=[POLineIn(item_name="Generator", quantity=1,
                                          unit_cost=total)]),
                     user=CHAIRMAN, db=db)


def _bal(db, bu_id, code):
    acc = db.query(Account).filter(Account.business_unit_id == bu_id, Account.code == code).first()
    total = Decimal("0")
    for e in db.query(JournalEntry).filter(JournalEntry.status == "posted",
                                           JournalEntry.business_unit_id == bu_id).all():
        for l in e.lines:
            if l.account_id == acc.id:
                total += (l.debit or 0) - (l.credit or 0)
    return total


# ---------------------------------------------------------------- C1
def test_c1_scoped_accountant_pinned_in_ops(db):
    assert bu_scope(ACCT_BU1, None) == 1
    assert bu_scope(ACCT_BU1, 1) == 1
    with pytest.raises(HTTPException) as exc:
        bu_scope(ACCT_BU1, 2)
    assert exc.value.status_code == 403
    assert bu_scope(ACCT_LEGACY, 2) == 2  # legacy keeps group access
    assert bu_scope(ACCT_LEGACY, None) is None
    # list_inventory through the endpoint: BU1 accountant sees only BU1
    _item(db, bu_id=1, name="A")
    _item(db, bu_id=2, name="B")
    out = list_inventory(business_unit_id=None, user=ACCT_BU1, db=db)
    assert [i["name"] for i in out["items"]] == ["A"]
    with pytest.raises(HTTPException) as exc:  # naming BU2 is a 403
        list_inventory(business_unit_id=2, user=ACCT_BU1, db=db)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------- C2 / C3
def test_c2_receive_draft_po_over_threshold_refused(db):
    sup = _supplier(db)
    po = _po(db, sup.id)  # 2,000,000 > 1,000,000 threshold, stage "first"
    assert po["approval_stage"] == "first"
    with pytest.raises(HTTPException) as exc:
        receive_po(po["id"], user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()


def test_c3_approved_po_edit_resets_approval(db):
    sup = _supplier(db)
    po = _po(db, sup.id)
    approve_po(po["id"], user=HO, db=db)
    po = approve_po(po["id"], user=CHAIRMAN, db=db)
    assert po["approval_stage"] == "approved"
    po = update_po(po["id"], POUpdate(lines=[POLineIn(item_name="Generator", quantity=1,
                                                       unit_cost=1500000)]),
                   user=CHAIRMAN, db=db)
    assert po["approval_stage"] == "first"  # over threshold again
    # ... and the reset PO cannot be ordered until it is re-approved
    with pytest.raises(HTTPException):
        order_po(po["id"], user=CHAIRMAN, db=db)
    db.rollback()
    po = update_po(po["id"], POUpdate(lines=[POLineIn(item_name="Paper", quantity=1,
                                                       unit_cost=1000)]),
                   user=CHAIRMAN, db=db)
    assert po["approval_stage"] is None  # under threshold now
    assert order_po(po["id"], user=CHAIRMAN, db=db)["status"] == "ordered"


# ---------------------------------------------------------------- M-B7 (the failing test's behaviour, at unit level)
def test_mb7_payment_journal_uses_payment_date(db):
    cust = _customer(db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 5),
        tax_rate=0, lines=[InvoiceLineIn(description="Sale", quantity=1, unit_price=200000)]),
        user=CHAIRMAN, db=db)
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    record_payment(inv["id"], PaymentIn(amount=200000, payment_date=date(2026, 10, 7)),
                   user=CHAIRMAN, db=db)
    entries = db.query(JournalEntry).filter(
        JournalEntry.source == "invoice", JournalEntry.source_id == inv["id"],
        JournalEntry.status == "posted").all()
    assert {e.entry_date for e in entries} == {date(2026, 10, 5), date(2026, 10, 7)}


# ---------------------------------------------------------------- M-C1
def test_mc1_send_invoice_decrements_stock_and_posts_cogs(db):
    it = _item(db, stock=10, cost=500)
    cust = _customer(db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 6),
        tax_rate=0, lines=[InvoiceLineIn(description="Widget", quantity=4, unit_price=1000,
                                         inventory_item_id=it.id)]),
        user=CHAIRMAN, db=db)
    out = send_invoice(inv["id"], user=CHAIRMAN, db=db)
    assert out["status"] == "sent" and out["journal_posted"] is True
    db.refresh(it)
    assert float(it.current_stock) == 6
    assert _bal(db, 1, "5100") == 2000   # COGS 4 x 500
    assert _bal(db, 1, "1140") == -2000  # inventory out at cost
    assert _bal(db, 1, "4100") == -4000  # sales net
    # insufficient stock blocks the send and leaves the invoice a draft
    inv2 = create_invoice(InvoiceIn(
        business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 6),
        tax_rate=0, lines=[InvoiceLineIn(description="Widget", quantity=99, unit_price=1000,
                                         inventory_item_id=it.id)]),
        user=CHAIRMAN, db=db)
    with pytest.raises(HTTPException) as exc:
        send_invoice(inv2["id"], user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    from src.routers.ops import _invoice_or_404
    assert _invoice_or_404(db, inv2["id"]).status == "draft"


# ---------------------------------------------------------------- M-C2 / M-C5
def test_mc2_fefo_shortfall_raises_and_expired_skipped(db):
    it = _item(db, name="Cream", stock=10, cost=100, requires_expiry=True)
    receive_batch(it.id, BatchIn(batch_no="OLD", expiry_date=date(2026, 1, 1),
                                 quantity=5, unit_cost=100), user=CHAIRMAN, db=db)
    receive_batch(it.id, BatchIn(batch_no="FRESH", expiry_date=date(2027, 1, 1),
                                 quantity=5, unit_cost=100), user=CHAIRMAN, db=db)
    # 8 units: only 5 unexpired available -> shortfall raises
    with pytest.raises(HTTPException) as exc:
        consume_batches_fefo(db, it, Decimal("8"))
    assert exc.value.status_code == 400
    db.rollback()
    # 5 units: consumes the fresh batch only, expired untouched
    consume_batches_fefo(db, it, Decimal("5"))
    batches = {b.batch_no: float(b.quantity) for b in
               db.query(StockBatch).filter(StockBatch.item_id == it.id).all()}
    assert batches["OLD"] == 5 and batches["FRESH"] == 0


def test_mc5_movement_validation(db):
    it = _item(db, stock=5, cost=100)
    with pytest.raises(HTTPException):
        record_movement(it.id, MovementIn(movement_type="out", quantity=6),
                        user=CHAIRMAN, db=db)
    db.rollback()
    with pytest.raises(HTTPException):
        record_movement(it.id, MovementIn(movement_type="in", quantity=-1),
                        user=CHAIRMAN, db=db)
    db.rollback()
    with pytest.raises(HTTPException):
        record_movement(it.id, MovementIn(movement_type="adjustment", quantity=3),
                        user=CHAIRMAN, db=db)  # no reason
    db.rollback()
    out = record_movement(it.id, MovementIn(movement_type="adjustment", quantity=3,
                                            reason="stocktake"), user=CHAIRMAN, db=db)
    assert out["current_stock"] == 3


# ---------------------------------------------------------------- M-C3 / M-C4 / M-C6
def test_mc3_pos_duplicate_lines_aggregate(db):
    it = _item(db, stock=10, cost=500)
    with pytest.raises(HTTPException) as exc:
        create_pos_sale(PosSaleIn(
            business_unit_id=1,
            lines=[PosLineIn(inventory_item_id=it.id, quantity=6, unit_price=1000),
                   PosLineIn(inventory_item_id=it.id, quantity=6, unit_price=1000)]),
            user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    db.refresh(it)
    assert float(it.current_stock) == 10


def test_mc4_po_validates_item_supplier_and_fx(db):
    sup_other_bu = _supplier(db, bu_id=2, name="Other BU vendor")
    other_item = _item(db, bu_id=2, name="Foreign item")
    with pytest.raises(HTTPException) as exc:
        create_po(POIn(business_unit_id=1, supplier_id=sup_other_bu.id,
                       order_date=date(2026, 10, 20),
                       lines=[POLineIn(item_name="X", quantity=1, unit_cost=100)]),
                  user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    sup = _supplier(db)
    with pytest.raises(HTTPException):
        create_po(POIn(business_unit_id=1, supplier_id=sup.id,
                       order_date=date(2026, 10, 20),
                       lines=[POLineIn(item_name="X", quantity=1, unit_cost=100,
                                       inventory_item_id=other_item.id)]),
                  user=CHAIRMAN, db=db)
    db.rollback()
    with pytest.raises(ValidationError):  # exchange_rate 0 rejected at the schema
        POIn(business_unit_id=1, supplier_id=sup.id, order_date=date(2026, 10, 20),
             exchange_rate=0, lines=[POLineIn(item_name="X", quantity=1, unit_cost=100)])


def test_mc6_convert_only_from_accepted(db):
    cust = _customer(db)
    q = create_quotation(QuotationIn(
        business_unit_id=1, customer_id=cust["id"], quote_date=date(2026, 10, 1),
        lines=[InvoiceLineIn(description="Goods", quantity=1, unit_price=1000)]),
        user=CHAIRMAN, db=db)
    with pytest.raises(HTTPException):
        convert_quotation(q["id"], user=CHAIRMAN, db=db)
    db.rollback()
    set_quotation_status(q["id"], QuotationStatusIn(status="sent"), user=CHAIRMAN, db=db)
    set_quotation_status(q["id"], QuotationStatusIn(status="accepted"), user=CHAIRMAN, db=db)
    inv = convert_quotation(q["id"], user=CHAIRMAN, db=db)
    assert inv["status"] == "draft"


# ---------------------------------------------------------------- M-C7 / M-C8
def test_mc7_cancel_invoice_frees_reserved_unit(db):
    unit = create_unit(UnitIn(business_unit_id=1, development="Sky", unit_no="1A",
                              asking_price=1000000), user=CHAIRMAN, db=db)
    cust = _customer(db)
    sale = sell_unit(unit["id"], SellUnitIn(customer_id=cust["id"]), user=CHAIRMAN, db=db)
    from src.models import PropertyUnit
    u = db.query(PropertyUnit).filter(PropertyUnit.id == unit["id"]).first()
    assert u.status == "reserved" and u.sale_invoice_id == sale["invoice_id"]
    cancel_invoice(sale["invoice_id"], user=CHAIRMAN, db=db)
    db.refresh(u)
    assert u.status == "available" and u.sale_invoice_id is None


def test_mc8_sales_return_puts_serial_back_in_stock(db):
    it = _item(db, stock=5, cost=800)
    add_serials(it.id, SerialAddIn(serial_nos=["IMEI-9"]), user=CHAIRMAN, db=db)
    cust = _customer(db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 6),
        tax_rate=0, lines=[InvoiceLineIn(description="Phone", quantity=1, unit_price=1500,
                                         inventory_item_id=it.id, serial_nos=["IMEI-9"])]),
        user=CHAIRMAN, db=db)
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    line_id = inv["lines"][0]["id"]
    create_sales_return(SalesReturnIn(
        invoice_id=inv["id"],
        lines=[SalesReturnLineIn(invoice_line_id=line_id, quantity=1,
                                 serial_nos=["IMEI-9"])]),
        user=CHAIRMAN, db=db)
    from src.models import ItemSerial
    s = db.query(ItemSerial).filter(ItemSerial.serial_no == "IMEI-9").first()
    assert s.status == "in_stock" and s.sales_invoice_id is None and s.sold_at is None
    # ... and the unit can be sold again
    inv2 = create_invoice(InvoiceIn(
        business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 7),
        tax_rate=0, lines=[InvoiceLineIn(description="Phone", quantity=1, unit_price=1500,
                                         inventory_item_id=it.id, serial_nos=["IMEI-9"])]),
        user=CHAIRMAN, db=db)
    send_invoice(inv2["id"], user=CHAIRMAN, db=db)
    assert db.query(ItemSerial).filter(ItemSerial.serial_no == "IMEI-9").first().status == "sold"


# ---------------------------------------------------------------- M-C9 / M-C15 / M-C16
def test_mc9_bom_rejects_self_duplicate_and_zero(db):
    fin = _item(db, name="Chair", stock=0, cost=0)
    wood = _item(db, name="Wood", stock=10, cost=100)
    with pytest.raises(HTTPException):
        create_bom(BomIn(business_unit_id=1, finished_item_id=fin.id,
                         components=[{"component_item_id": fin.id, "quantity_per_unit": 1}]),
                   user=CHAIRMAN, db=db)
    db.rollback()
    with pytest.raises(HTTPException):
        create_bom(BomIn(business_unit_id=1, finished_item_id=fin.id,
                         components=[{"component_item_id": wood.id, "quantity_per_unit": 1},
                                     {"component_item_id": wood.id, "quantity_per_unit": 2}]),
                   user=CHAIRMAN, db=db)
    db.rollback()
    with pytest.raises(ValidationError):
        BomIn(business_unit_id=1, finished_item_id=fin.id,
              components=[{"component_item_id": wood.id, "quantity_per_unit": 0}])


def test_mc15_sell_unit_bounds(db):
    with pytest.raises(ValidationError):
        SellUnitIn(customer_id=1, downpayment_pct=150, installments=12)
    with pytest.raises(ValidationError):
        SellUnitIn(customer_id=1, downpayment_pct=10, installments=400)


def test_mc16_lease_requires_available_unit_and_same_bu_tenant(db):
    unit = create_unit(UnitIn(business_unit_id=1, unit_no="2B", asking_price=500000),
                       user=CHAIRMAN, db=db)
    tenant = _customer(db, bu_id=1)
    create_lease(LeaseIn(business_unit_id=1, unit_id=unit["id"], tenant_id=tenant["id"],
                         monthly_rent=100000, start_date=date(2026, 10, 1)),
                 user=CHAIRMAN, db=db)
    with pytest.raises(HTTPException) as exc:  # unit now rented
        create_lease(LeaseIn(business_unit_id=1, unit_id=unit["id"], tenant_id=tenant["id"],
                             monthly_rent=100000, start_date=date(2026, 11, 1)),
                     user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    unit2 = create_unit(UnitIn(business_unit_id=1, unit_no="2C", asking_price=500000),
                        user=CHAIRMAN, db=db)
    foreign_tenant = _customer(db, bu_id=2, name="Outsider")
    with pytest.raises(HTTPException) as exc:
        create_lease(LeaseIn(business_unit_id=1, unit_id=unit2["id"],
                             tenant_id=foreign_tenant["id"], monthly_rent=100000,
                             start_date=date(2026, 10, 1)), user=CHAIRMAN, db=db)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- M-B4 / M-B8 / M-D7
def test_mb4_payroll_line_cannot_go_negative_net(db):
    emp = create_employee(EmployeeIn(business_unit_id=1, name="May", base_salary=300000),
                          user=CHAIRMAN, db=db)
    run = create_payroll(PayrollCreate(business_unit_id=1, period="2026-10"),
                         user=CHAIRMAN, db=db)
    line = run["lines"][0]
    with pytest.raises(HTTPException) as exc:
        update_payroll_line(run["id"], line["id"], PayrollLineUpdate(deductions=300001),
                            user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()


def test_mb8_approve_advance_without_coa_stays_pending(db):
    # BU 3 has no chart of accounts at all: the journal cannot post, so the
    # approval must not stand.
    bu3 = BusinessUnit(id=3, code="BU3", name="No Books")
    db.add(bu3)
    db.commit()
    emp = create_employee(EmployeeIn(business_unit_id=3, name="Ko", base_salary=200000),
                          user=CHAIRMAN, db=db)
    adv = create_advance(AdvanceIn(employee_id=emp["id"], amount=100000,
                                   monthly_deduction=50000), user=CHAIRMAN, db=db)
    with pytest.raises(HTTPException):
        approve_advance(adv["id"], user=CHAIRMAN, db=db)
    db.rollback()
    from src.models import StaffAdvance
    assert db.query(StaffAdvance).filter(StaffAdvance.id == adv["id"]).first().status == "pending"


def test_md7_payment_method_and_currency_validated(db):
    cust = _customer(db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=1, customer_id=cust["id"], invoice_date=date(2026, 10, 6),
        tax_rate=0, lines=[InvoiceLineIn(description="Sale", quantity=1, unit_price=1000)]),
        user=CHAIRMAN, db=db)
    send_invoice(inv["id"], user=CHAIRMAN, db=db)
    with pytest.raises(ValidationError):  # method not in cash/bank/mobile
        PaymentIn(amount=100, payment_date=date(2026, 10, 7), method="barter")
    with pytest.raises(HTTPException) as exc:  # MMK payment against an MMK invoice is fine...
        record_payment(inv["id"], PaymentIn(amount=100, payment_date=date(2026, 10, 7),
                                            method="cash", currency="USD"),
                       user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    out = record_payment(inv["id"], PaymentIn(amount=100, payment_date=date(2026, 10, 7),
                                              method="mobile", currency="MMK"),
                         user=CHAIRMAN, db=db)
    assert out["journal_posted"] is True


# ---------------------------------------------------------------- M-D2 / m3 / m7 / M-A2
def test_md2_tax_rate_and_currency_bounded():
    with pytest.raises(ValidationError):
        InvoiceIn(business_unit_id=1, customer_id=1, invoice_date=date(2026, 10, 6),
                  tax_rate=150, lines=[InvoiceLineIn(description="x", quantity=1, unit_price=1)])
    with pytest.raises(ValidationError):
        InvoiceIn(business_unit_id=1, customer_id=1, invoice_date=date(2026, 10, 6),
                  currency="MMKK", lines=[InvoiceLineIn(description="x", quantity=1, unit_price=1)])
    ok = InvoiceIn(business_unit_id=1, customer_id=1, invoice_date=date(2026, 10, 6),
                   tax_rate=7.5, currency="USD",
                   lines=[InvoiceLineIn(description="x", quantity=1, unit_price=1)])
    assert ok.tax_rate == 7.5  # per-document rates are allowed (Q4: not locked to 5)


def test_m3_excel_cell_sanitised():
    assert _sanitize_excel_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert _sanitize_excel_cell("+2+2") == "'+2+2"
    assert _sanitize_excel_cell("plain text") == "plain text"
    assert _sanitize_excel_cell(None) is None


def test_m7_leave_type_and_overlap(db):
    emp = create_employee(EmployeeIn(business_unit_id=1, name="May", base_salary=300000),
                          user=CHAIRMAN, db=db)
    with pytest.raises(HTTPException):
        create_leave(LeaveIn(employee_id=emp["id"], leave_type="mystery",
                             start_date=date(2026, 11, 2), end_date=date(2026, 11, 3)),
                     user=CHAIRMAN, db=db)
    db.rollback()
    lv = create_leave(LeaveIn(employee_id=emp["id"], leave_type="annual",
                              start_date=date(2026, 11, 2), end_date=date(2026, 11, 4)),
                      user=CHAIRMAN, db=db)
    review_leave(lv["id"], LeaveReview(decision="approved"), user=CHAIRMAN, db=db)
    with pytest.raises(HTTPException) as exc:
        create_leave(LeaveIn(employee_id=emp["id"], leave_type="casual",
                             start_date=date(2026, 11, 4), end_date=date(2026, 11, 5)),
                     user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400


def test_ma2_group_supplier_needs_chairman_or_ho(db):
    # CEOs / BU-assigned accountants are forced to their own BU, so they
    # can never create a group-shared supplier; only Chairman/HO can.
    ceo_sup = create_supplier(SupplierIn(name="CEO Vendor", business_unit_id=None),
                              user=CEO_BU1, db=db)
    assert ceo_sup["business_unit_id"] == 1
    acct_sup = create_supplier(SupplierIn(name="Acct Vendor", business_unit_id=None),
                               user=ACCT_BU1, db=db)
    assert acct_sup["business_unit_id"] == 1
    with pytest.raises(HTTPException) as exc:  # legacy accountant, no BU to force to
        create_supplier(SupplierIn(name="Shared", business_unit_id=None), user=ACCT_LEGACY, db=db)
    assert exc.value.status_code == 403
    db.rollback()
    s = create_supplier(SupplierIn(name="Shared", business_unit_id=None), user=CHAIRMAN, db=db)
    assert s["business_unit_id"] is None
    with pytest.raises(HTTPException) as exc:
        delete_supplier(s["id"], user=CEO_BU1, db=db)
    assert exc.value.status_code == 403
    db.rollback()
    assert delete_supplier(s["id"], user=CHAIRMAN, db=db)["ok"] is True


def test_mb9_production_capitalises_against_salaries_not_2120(db):
    wood = _item(db, name="Teak", stock=100, cost=1000)
    chair = _item(db, name="Chair", stock=0, cost=0)
    bom = create_bom(BomIn(business_unit_id=1, finished_item_id=chair.id,
                           components=[{"component_item_id": wood.id, "quantity_per_unit": 4}]),
                     user=CHAIRMAN, db=db)
    order = create_production(ProductionIn(bom_id=bom["id"], quantity=10,
                                           labour_cost=20000, overhead_cost=5000),
                              user=CHAIRMAN, db=db)
    done = complete_production(order["id"], user=CHAIRMAN, db=db)
    assert done["status"] == "completed" and done["journal_posted"] is True
    assert _bal(db, 1, "2120") == 0      # no stranded accrual
    assert _bal(db, 1, "6200") == -25000  # labour+overhead capitalised out of salaries
    assert _bal(db, 1, "1140") == 25000   # the capitalisation journal only;
    # materials were already in inventory when purchased, so no materials
    # journal posts at completion — the finished unit's cost still carries
    # them (materials 40000 + added value 25000 = unit cost 6500).
    assert done["materials_cost"] == 40000 and done["unit_cost"] == 6500


def test_mc18_ceo_sees_incoming_transfers_too(db):
    from src.routers.modules import (
        StockTransferIn, TransferLineIn, create_stock_transfer, list_stock_transfers,
    )
    it = _item(db, bu_id=2, name="Rice", stock=10, cost=100)
    t = create_stock_transfer(StockTransferIn(
        from_business_unit_id=2, to_business_unit_id=1, lines=[TransferLineIn(inventory_item_id=it.id, quantity=2)]),
        user=CHAIRMAN, db=db)
    out = list_stock_transfers(user=CEO_BU1, db=db)
    assert [x["id"] for x in out["items"]] == [t["id"]]
