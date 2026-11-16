"""Regression tests for BOT review 2026-11-16 findings F-5, F-8, F-9.

- F-5: CM-8 optimistic locking on quotations (version column +
  expected_version on update_quotation, mirroring update_po).
- F-8: receive_po takes item row locks in canonical sorted-id order,
  never in PO line order (Cm-4 class; cf. send_invoice).
- F-9: _check_version logs a warning when expected_version is omitted
  instead of silently disabling optimistic locking.

SQLite note (F-8): FOR UPDATE is a no-op on SQLite, so a true
deadlock can only be reproduced on Postgres. These tests prove the
restructure did not change receive behaviour (shared items in
opposite line order, duplicate lines for one item, name-only lines)
and that the sorted pre-lock pass covers every line's item. The
Postgres proof tests in tests/test_rt_postgres_concurrency.py are
the place for a live deadlock check.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_bot_review_f5_f8_f9.py -q
"""
import logging
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, ExpenseRequest, InventoryItem, Quotation,
    Supplier,
)
from src.routers.ops import (
    CustomerIn, ExpenseIn, ExpenseReviewIn, InvoiceLineIn, POIn, POLineIn,
    POUpdate, QuotationIn, QuotationUpdate, create_po, create_quotation,
    create_sales_customer, create_supplier, receive_po, review_expense,
    submit_expense, update_po, update_quotation,
)

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None)
CEO = SimpleNamespace(id=7, role="CEO", business_unit_id=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu = BusinessUnit(name="Shop", code="S1")
    session.add(bu)
    session.commit()
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts("retail"):
        acc = Account(business_unit_id=bu.id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        session.add(acc)
        session.flush()
        id_by_code[code] = acc.id
    sup = Supplier(name="Wholesale Co")
    session.add(sup)
    session.commit()
    session.test_bu = bu
    session.test_supplier = sup
    yield session
    session.close()


def _customer(db, name="Hotel"):
    return create_sales_customer(
        CustomerIn(business_unit_id=db.test_bu.id, name=name), user=CHAIRMAN, db=db)


def _quotation(db, cust):
    return create_quotation(QuotationIn(
        business_unit_id=db.test_bu.id, customer_id=cust["id"],
        quote_date=date.today(), tax_rate=5,
        lines=[InvoiceLineIn(description="Goods", quantity=10, unit_price=1000)],
    ), user=CHAIRMAN, db=db)


def _make_po(db, lines):
    return create_po(POIn(
        business_unit_id=db.test_bu.id, supplier_id=db.test_supplier.id,
        order_date=date(2026, 10, 20),
        lines=[POLineIn(**ln) for ln in lines]), user=ADMIN, db=db)


ADMIN = CHAIRMAN


def _item(db, name, stock=0, cost=0):
    item = InventoryItem(business_unit_id=db.test_bu.id, name=name,
                         current_stock=stock, unit_cost=cost)
    db.add(item)
    db.commit()
    return item


def _stock(db, item_id):
    return Decimal(str(db.query(InventoryItem).filter(
        InventoryItem.id == item_id).first().current_stock))


# --------------------------------------------------------------------- F-5

def test_quotation_version_starts_at_zero(db):
    q = _quotation(db, _customer(db))
    assert q["version"] == 0
    row = db.query(Quotation).filter(Quotation.id == q["id"]).first()
    assert row.version == 0


def test_quotation_update_with_current_version_bumps_it(db):
    q = _quotation(db, _customer(db))
    out = update_quotation(q["id"], QuotationUpdate(notes="revised", expected_version=0),
                           user=CHAIRMAN, db=db)
    assert out["notes"] == "revised"
    assert out["version"] == 1
    assert db.query(Quotation).filter(Quotation.id == q["id"]).first().version == 1


def test_quotation_update_with_stale_version_409s(db):
    q = _quotation(db, _customer(db))
    update_quotation(q["id"], QuotationUpdate(notes="first", expected_version=0),
                     user=CHAIRMAN, db=db)
    with pytest.raises(HTTPException) as exc:
        update_quotation(q["id"], QuotationUpdate(notes="stale", expected_version=0),
                         user=CHAIRMAN, db=db)
    assert exc.value.status_code == 409
    db.rollback()
    row = db.query(Quotation).filter(Quotation.id == q["id"]).first()
    assert row.notes == "first"  # the stale edit did not land


def test_quotation_update_replaces_lines_and_bumps_version(db):
    q = _quotation(db, _customer(db))
    out = update_quotation(
        q["id"],
        QuotationUpdate(
            lines=[InvoiceLineIn(description="Other", quantity=2, unit_price=500)],
            expected_version=0),
        user=CHAIRMAN, db=db)
    assert out["version"] == 1
    assert len(out["lines"]) == 1 and out["lines"][0]["description"] == "Other"
    assert out["total"] == 1050  # 2 x 500 + 5% tax


# --------------------------------------------------------------------- F-8

def test_receive_two_pos_sharing_items_in_opposite_line_order(db):
    a = _item(db, "Rice 5kg")
    b = _item(db, "Oil 1L")
    po1 = _make_po(db, [
        {"item_name": "Rice 5kg", "inventory_item_id": a.id, "quantity": 10, "unit_cost": 1000},
        {"item_name": "Oil 1L", "inventory_item_id": b.id, "quantity": 5, "unit_cost": 2000},
    ])
    po2 = _make_po(db, [
        {"item_name": "Oil 1L", "inventory_item_id": b.id, "quantity": 7, "unit_cost": 2000},
        {"item_name": "Rice 5kg", "inventory_item_id": a.id, "quantity": 3, "unit_cost": 1000},
    ])
    receive_po(po1["id"], user=CHAIRMAN, db=db)
    receive_po(po2["id"], user=CHAIRMAN, db=db)
    assert _stock(db, a.id) == 13
    assert _stock(db, b.id) == 12


def test_receive_po_with_duplicate_lines_for_one_item(db):
    a = _item(db, "Rice 5kg")
    po = _make_po(db, [
        {"item_name": "Rice 5kg", "inventory_item_id": a.id, "quantity": 10, "unit_cost": 1000},
        {"item_name": "Rice 5kg", "inventory_item_id": a.id, "quantity": 10, "unit_cost": 2000},
    ])
    receive_po(po["id"], user=CHAIRMAN, db=db)
    assert _stock(db, a.id) == 20
    # Moving average over both lines: (10x1000 + 10x2000) / 20 = 1500.
    item = db.query(InventoryItem).filter(InventoryItem.id == a.id).first()
    assert Decimal(str(item.unit_cost)) == 1500


def test_receive_po_name_only_lines_still_create_items(db):
    po = _make_po(db, [
        {"item_name": "New Spice", "quantity": 4, "unit_cost": 500},
    ])
    receive_po(po["id"], user=CHAIRMAN, db=db)
    item = db.query(InventoryItem).filter(
        InventoryItem.business_unit_id == db.test_bu.id,
        InventoryItem.name == "New Spice").first()
    assert item is not None and Decimal(str(item.current_stock)) == 4


def test_receive_po_line_item_from_other_bu_still_rejected(db):
    # create_po itself validates line items, so build the PO directly to
    # reach receive_po's own BU check (e.g. an item moved BU after the
    # PO was created).
    from src.models import PurchaseOrder, PurchaseOrderLine
    other_bu = BusinessUnit(name="Other", code="O1")
    db.add(other_bu)
    db.commit()
    foreign = InventoryItem(business_unit_id=other_bu.id, name="Foreign",
                            current_stock=0, unit_cost=100)
    db.add(foreign)
    db.commit()
    po = PurchaseOrder(business_unit_id=db.test_bu.id,
                       supplier_id=db.test_supplier.id, po_no="PO-TEST-1",
                       status="draft", order_date=date(2026, 10, 20))
    db.add(po)
    db.flush()
    db.add(PurchaseOrderLine(po_id=po.id, item_name="Foreign",
                             inventory_item_id=foreign.id,
                             quantity=1, unit_cost=100))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        receive_po(po.id, user=CHAIRMAN, db=db)
    assert exc.value.status_code == 400


# --------------------------------------------------------------------- F-9

def test_update_quotation_without_version_warns_but_succeeds(db, caplog):
    q = _quotation(db, _customer(db))
    with caplog.at_level(logging.WARNING, logger="src.routers.ops"):
        out = update_quotation(q["id"], QuotationUpdate(notes="no version"),
                               user=CHAIRMAN, db=db)
    assert out["notes"] == "no version"
    assert out["version"] == 1  # edit still applied (back-compat)
    assert "without expected_version" in caplog.text


def test_update_po_without_version_warns_but_succeeds(db, caplog):
    po = _make_po(db, [{"item_name": "Paper", "quantity": 1, "unit_cost": 100}])
    with caplog.at_level(logging.WARNING, logger="src.routers.ops"):
        out = update_po(po["id"], POUpdate(notes="no version"), user=CHAIRMAN, db=db)
    assert out["notes"] == "no version"
    assert "without expected_version" in caplog.text


def test_review_expense_without_version_warns_but_succeeds(db, caplog):
    ceo = SimpleNamespace(id=7, role="CEO", business_unit_id=db.test_bu.id)
    exp = submit_expense(
        ExpenseIn(title="Stationery", amount=500000, category="supplies"),
        user=ceo, db=db)
    with caplog.at_level(logging.WARNING, logger="src.routers.ops"):
        out = review_expense(exp["id"], ExpenseReviewIn(decision="approved"),
                             user=CHAIRMAN, db=db)
    assert out["status"] == "approved"
    assert "without expected_version" in caplog.text
    assert db.query(ExpenseRequest).filter(
        ExpenseRequest.id == exp["id"]).first().status == "approved"


def test_version_check_with_correct_version_logs_no_warning(db, caplog):
    q = _quotation(db, _customer(db))
    with caplog.at_level(logging.WARNING, logger="src.routers.ops"):
        update_quotation(q["id"], QuotationUpdate(notes="with version", expected_version=0),
                         user=CHAIRMAN, db=db)
    assert "without expected_version" not in caplog.text
