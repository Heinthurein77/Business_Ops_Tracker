"""Tests for the nature modules: templates, batches/FEFO, manufacturing,
projects P&L, shipments, real-estate installments + leases, principal
report (in-memory SQLite)."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import Account, Base, BusinessUnit, InventoryItem
from src.routers.modules import (
    BatchIn, BomIn, LeaseIn, ProductionIn, ProjectIn, SellUnitIn, ShipmentIn,
    UnitIn, bill_lease, complete_production, consume_batches_fefo,
    create_bom, create_lease, create_production, create_project,
    create_shipment, create_unit, expiring, list_installments,
    principal_report, project_pl, receive_batch, sell_unit,
)
from src.routers.ops import (
    CustomerIn, InvoiceIn, InvoiceLineIn, MovementIn, PaymentIn,
    create_invoice, create_sales_customer, record_movement, record_payment,
    send_invoice,
)

ADMIN = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu = BusinessUnit(name="Factory", code="F1")
    session.add(bu)
    session.commit()
    id_by_code = {}
    for code, name, acc_type, parent, is_header in template_accounts("production"):
        acc = Account(business_unit_id=bu.id, code=code, name=name, account_type=acc_type,
                      is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent) if parent else None)
        session.add(acc)
        session.flush()
        id_by_code[code] = acc.id
    session.commit()
    session.test_bu = bu
    yield session
    session.close()


def item(db, name, stock=0, cost=0, **kw):
    it = InventoryItem(business_unit_id=db.test_bu.id, name=name,
                       current_stock=stock, unit_cost=cost, **kw)
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def test_all_templates_have_unique_codes():
    from src.accounting_templates import TEMPLATE_TYPES, template_accounts
    assert len(TEMPLATE_TYPES) == 13
    for t in TEMPLATE_TYPES:
        codes = [a[0] for a in template_accounts(t)]
        assert len(codes) == len(set(codes)), f"duplicate codes in {t}"


def test_batch_receive_and_fefo(db):
    it = item(db, "Lipstick", requires_expiry=True)
    receive_batch(it.id, BatchIn(batch_no="B-LATE", expiry_date=date.today() + timedelta(days=90),
                                 quantity=10, unit_cost=100), user=ADMIN, db=db)
    receive_batch(it.id, BatchIn(batch_no="B-EARLY", expiry_date=date.today() + timedelta(days=10),
                                 quantity=5, unit_cost=100), user=ADMIN, db=db)
    db.refresh(it)
    assert float(it.current_stock) == 15
    out = expiring(days=30, user=ADMIN, db=db)
    assert out["count"] == 1 and out["items"][0]["batch_no"] == "B-EARLY"
    # FEFO: stock-out 7 consumes the early batch (5) then the late one (2)
    record_movement(it.id, MovementIn(movement_type="out", quantity=7), user=ADMIN, db=db)
    from src.models import StockBatch
    batches = {b.batch_no: float(b.quantity) for b in db.query(StockBatch).all()}
    assert batches["B-EARLY"] == 0 and batches["B-LATE"] == 8


def test_manufacturing_flow(db):
    wood = item(db, "Teak plank", stock=100, cost=1000)
    varnish = item(db, "Varnish tin", stock=50, cost=500)
    chair = item(db, "Teak chair", stock=0, cost=0)
    bom = create_bom(BomIn(business_unit_id=db.test_bu.id, finished_item_id=chair.id,
                           name="Chair BOM",
                           components=[{"component_item_id": wood.id, "quantity_per_unit": 4},
                                       {"component_item_id": varnish.id, "quantity_per_unit": 0.5}]),
                     user=ADMIN, db=db)
    order = create_production(ProductionIn(bom_id=bom["id"], quantity=10, labour_cost=20000,
                                           overhead_cost=5000), user=ADMIN, db=db)
    done = complete_production(order["id"], user=ADMIN, db=db)
    assert done["status"] == "completed"
    # materials: 10*4*1000 + 10*0.5*500 = 42,500; total 67,500; unit 6,750
    assert done["materials_cost"] == 42500 and done["unit_cost"] == 6750
    db.refresh(wood); db.refresh(chair)
    assert float(wood.current_stock) == 60 and float(chair.current_stock) == 10
    assert float(chair.unit_cost) == 6750


def test_project_pl(db):
    proj = create_project(ProjectIn(business_unit_id=db.test_bu.id, name="Hotel fit-out",
                                    project_type="contract"), user=ADMIN, db=db)
    cust = create_sales_customer(CustomerIn(business_unit_id=db.test_bu.id, name="Hotel"),
                                 user=ADMIN, db=db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=db.test_bu.id, customer_id=cust["id"], invoice_date=date(2026, 10, 3),
        tax_rate=0, project_id=proj["id"],
        lines=[InvoiceLineIn(description="Fit-out", quantity=1, unit_price=1000000)]),
        user=ADMIN, db=db)
    send_invoice(inv["id"], user=ADMIN, db=db)
    out = project_pl(proj["id"], user=ADMIN, db=db)
    assert out["revenue"] == 1000000 and out["profit"] == 1000000


def test_shipment_profit(db):
    s = create_shipment(ShipmentIn(business_unit_id=db.test_bu.id, origin="Yangon",
                                   destination="Mandalay", shipment_date=date(2026, 10, 9),
                                   freight_charge=800000, fuel_cost=300000, toll_fees=50000,
                                   driver_fees=100000), user=ADMIN, db=db)
    assert s["total_costs"] == 450000 and s["profit"] == 350000


def test_real_estate_installments_and_lease(db):
    unit = create_unit(UnitIn(business_unit_id=db.test_bu.id, development="Sky Tower",
                              unit_no="12A", asking_price=100000000), user=ADMIN, db=db)
    buyer = create_sales_customer(CustomerIn(business_unit_id=db.test_bu.id, name="Buyer"),
                                  user=ADMIN, db=db)
    sale = sell_unit(unit["id"], SellUnitIn(customer_id=buyer["id"], downpayment_pct=20,
                                            installments=8, first_due_date=date(2026, 11, 1)),
                     user=ADMIN, db=db)
    plan = list_installments(sale["invoice_id"], user=ADMIN, db=db)["items"]
    assert len(plan) == 9  # downpayment + 8 monthly
    assert plan[0]["amount"] == 20000000 and plan[1]["amount"] == 10000000
    send_invoice(sale["invoice_id"], user=ADMIN, db=db)
    record_payment(sale["invoice_id"], PaymentIn(amount=25000000, payment_date=date(2026, 11, 2)),
                   user=ADMIN, db=db)
    plan = list_installments(sale["invoice_id"], user=ADMIN, db=db)["items"]
    assert plan[0]["paid_amount"] == 20000000 and plan[1]["paid_amount"] == 5000000
    # lease on a second unit + monthly billing guard
    unit2 = create_unit(UnitIn(business_unit_id=db.test_bu.id, unit_no="3B",
                               asking_price=50000000), user=ADMIN, db=db)
    lease = create_lease(LeaseIn(business_unit_id=db.test_bu.id, unit_id=unit2["id"],
                                 tenant_id=buyer["id"], monthly_rent=800000,
                                 start_date=date(2026, 10, 1)), user=ADMIN, db=db)
    bill = bill_lease(lease["id"], period="2026-10", user=ADMIN, db=db)
    assert bill["period"] == "2026-10"
    with pytest.raises(Exception):
        bill_lease(lease["id"], period="2026-10", user=ADMIN, db=db)


def test_principal_report(db):
    from src.models import Supplier
    sup = Supplier(name="L'Oreal Principal")
    db.add(sup)
    db.commit()
    cream = item(db, "Face cream", brand="L'Oreal", principal_id=sup.id)
    cust = create_sales_customer(CustomerIn(business_unit_id=db.test_bu.id, name="Shop"),
                                 user=ADMIN, db=db)
    inv = create_invoice(InvoiceIn(
        business_unit_id=db.test_bu.id, customer_id=cust["id"], invoice_date=date(2026, 10, 6),
        tax_rate=0, lines=[InvoiceLineIn(description="Face cream", quantity=10, unit_price=15000,
                                         inventory_item_id=cream.id)]),
        user=ADMIN, db=db)
    send_invoice(inv["id"], user=ADMIN, db=db)
    out = principal_report(business_unit_id=db.test_bu.id, period="2026-10", user=ADMIN, db=db)
    assert out["total_revenue"] == 150000
    assert out["items"][0]["principal"] == "L'Oreal Principal"
    assert out["items"][0]["brand"] == "L'Oreal"
