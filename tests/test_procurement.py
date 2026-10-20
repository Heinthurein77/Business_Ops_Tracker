"""Tests for ERP Phase 3 procurement (in-memory SQLite).

Run with: ENVIRONMENT=test .venv/bin/python -m pytest tests/test_procurement.py
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, InventoryItem, JournalEntry, Supplier,
)
from src.routers.ops import (
    POLineIn, POIn, create_po, pay_po, receive_po, transfer_stock, TransferIn,
)

ADMIN = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu = BusinessUnit(name="Shop", code="S1")
    bu2 = BusinessUnit(name="Shop 2", code="S2")
    session.add_all([bu, bu2])
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
    session.test_bu2 = bu2
    session.test_supplier = sup
    yield session
    session.close()


def make_po(db, lines):
    payload = POIn(business_unit_id=db.test_bu.id, supplier_id=db.test_supplier.id,
                   order_date=date(2026, 10, 20),
                   lines=[POLineIn(**ln) for ln in lines])
    return create_po(payload, user=ADMIN, db=db)


def balance(db, code):
    acc = db.query(Account).filter(Account.code == code).first()
    total = Decimal("0")
    for e in db.query(JournalEntry).filter(JournalEntry.status == "posted").all():
        for l in e.lines:
            if l.account_id == acc.id:
                total += (l.debit or 0) - (l.credit or 0)
    return total


def test_receive_updates_stock_moving_average_and_posts_ap(db):
    po = make_po(db, [{"item_name": "Rice 5kg", "quantity": 10, "unit_cost": 1000}])
    receive_po(po["id"], user=ADMIN, db=db)
    item = db.query(InventoryItem).filter(InventoryItem.name == "Rice 5kg").first()
    assert float(item.current_stock) == 10
    assert float(item.unit_cost) == 1000
    assert balance(db, "1140") == 10000   # Dr Inventory
    assert balance(db, "2110") == -10000  # Cr AP

    # second receipt at a higher cost -> weighted average (10*1000 + 10*3000)/20 = 2000
    po2 = make_po(db, [{"item_name": "Rice 5kg", "quantity": 10, "unit_cost": 3000}])
    receive_po(po2["id"], user=ADMIN, db=db)
    db.refresh(item)
    assert float(item.current_stock) == 20
    assert float(item.unit_cost) == 2000


def test_pay_clears_ap_against_cash(db):
    po = make_po(db, [{"item_name": "Oil 1L", "quantity": 5, "unit_cost": 2000}])
    receive_po(po["id"], user=ADMIN, db=db)
    pay_po(po["id"], user=ADMIN, db=db)
    assert balance(db, "2110") == 0
    assert balance(db, "1110") == -10000  # cash out


def test_transfer_moves_stock_between_bus(db):
    item = InventoryItem(business_unit_id=db.test_bu.id, name="Soap",
                         current_stock=Decimal("30"), unit_cost=Decimal("500"))
    db.add(item)
    db.commit()
    out = transfer_stock(item.id, TransferIn(to_business_unit_id=db.test_bu2.id, quantity=12),
                         user=ADMIN, db=db)
    assert out["source_stock"] == 18
    dst = db.query(InventoryItem).filter(
        InventoryItem.business_unit_id == db.test_bu2.id,
        InventoryItem.name == "Soap").first()
    assert float(dst.current_stock) == 12
    assert float(dst.unit_cost) == 500
