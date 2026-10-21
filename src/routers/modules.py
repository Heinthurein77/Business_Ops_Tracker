"""Nature modules (2026-10-21): batch/expiry inventory, projects &
per-project P&L, logistics shipments, manufacturing (BOM + production
orders), real-estate units / installment plans / leases, and the
principal (brand-owner) sales report.

Built on the shared core: stock lives in InventoryItem, money posts
through the accounting journal, sales through SalesInvoice.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.database import get_db
from src.models import (
    BomComponent, BusinessUnit, Customer, ExpenseRequest, Installment,
    InventoryItem, LeaseContract, ManufacturingBom, ProductionOrder,
    Project, PropertyUnit, PurchaseOrder, SalesInvoice, Shipment,
    StockBatch, StockMovement, Supplier, User,
)
from src.routers.ops import bu_scope, invoice_amounts, po_total

router = APIRouter(prefix="/api/modules", tags=["modules"])
ZERO = Decimal("0")


def fmt(v) -> float:
    return float(Decimal(str(v or 0)))


# ------------------------------------------------------------------ batches & expiry

def serialize_batch(b: StockBatch) -> dict:
    return {
        "id": b.id, "item_id": b.item_id, "batch_no": b.batch_no,
        "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
        "quantity": fmt(b.quantity), "unit_cost": fmt(b.unit_cost),
        "days_to_expiry": (b.expiry_date - date.today()).days if b.expiry_date else None,
    }


@router.get("/inventory/{item_id}/batches")
def list_batches(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    bu_scope(user, item.business_unit_id)
    rows = db.query(StockBatch).filter(StockBatch.item_id == item_id,
                                       StockBatch.quantity > 0).order_by(
        StockBatch.expiry_date.is_(None), StockBatch.expiry_date, StockBatch.id).all()
    return {"items": [serialize_batch(b) for b in rows]}


class BatchIn(BaseModel):
    batch_no: str = Field(min_length=1, max_length=80)
    expiry_date: Optional[date] = None
    quantity: float = Field(gt=0)
    unit_cost: float = Field(ge=0)


@router.post("/inventory/{item_id}/batches")
def receive_batch(item_id: int, payload: BatchIn, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Receive stock as a tracked batch; item stock rises at weighted avg."""
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    bu_scope(user, item.business_unit_id)
    qty = Decimal(str(payload.quantity))
    cost = Decimal(str(payload.unit_cost))
    old_qty = Decimal(str(item.current_stock or 0))
    old_cost = Decimal(str(item.unit_cost or 0))
    new_qty = old_qty + qty
    item.unit_cost = ((old_qty * old_cost) + (qty * cost)) / new_qty if new_qty else cost
    item.current_stock = new_qty
    item.updated_at = datetime.utcnow()
    b = StockBatch(item_id=item.id, batch_no=payload.batch_no.strip(),
                   expiry_date=payload.expiry_date, quantity=qty, unit_cost=cost)
    db.add(b)
    db.add(StockMovement(item_id=item.id, movement_type="in", quantity=qty,
                         reason=f"Batch {b.batch_no} received", recorded_by_id=user.id))
    db.commit()
    db.refresh(b)
    return serialize_batch(b)


@router.get("/expiring")
def expiring(days: int = 30, business_unit_id: Optional[int] = None,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Batches expiring within `days` (or already expired) with stock left."""
    bu_id = bu_scope(user, business_unit_id)
    cutoff = date.today() + timedelta(days=days)
    q = db.query(StockBatch).filter(StockBatch.quantity > 0,
                                    StockBatch.expiry_date.isnot(None),
                                    StockBatch.expiry_date <= cutoff)
    if bu_id is not None:
        q = q.join(InventoryItem).filter(InventoryItem.business_unit_id == bu_id)
    rows = q.order_by(StockBatch.expiry_date).all()
    out = []
    for b in rows:
        d = serialize_batch(b)
        d["item_name"] = b.item.name if b.item else None
        d["business_unit_id"] = b.item.business_unit_id if b.item else None
        out.append(d)
    return {"items": out, "count": len(out)}


def consume_batches_fefo(db: Session, item: InventoryItem, qty: Decimal) -> None:
    """Deduct `qty` from the item's batches, earliest expiry first (FEFO)."""
    remaining = qty
    batches = db.query(StockBatch).filter(StockBatch.item_id == item.id,
                                          StockBatch.quantity > 0).order_by(
        StockBatch.expiry_date.is_(None), StockBatch.expiry_date, StockBatch.id).all()
    for b in batches:
        if remaining <= 0:
            break
        take = min(Decimal(str(b.quantity)), remaining)
        b.quantity = Decimal(str(b.quantity)) - take
        remaining -= take


# ------------------------------------------------------------------ projects

def serialize_project(db: Session, p: Project) -> dict:
    return {
        "id": p.id, "business_unit_id": p.business_unit_id,
        "bu_name": p.business_unit.name if p.business_unit else None,
        "name": p.name, "project_type": p.project_type,
        "customer_id": p.customer_id,
        "customer_name": p.customer.name if p.customer else None,
        "status": p.status,
        "start_date": p.start_date.isoformat() if p.start_date else None,
        "end_date": p.end_date.isoformat() if p.end_date else None,
        "notes": p.notes,
    }


class ProjectIn(BaseModel):
    business_unit_id: Optional[int] = None
    name: str = Field(min_length=1, max_length=200)
    project_type: str = "general"
    customer_id: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None


@router.get("/projects")
def list_projects(business_unit_id: Optional[int] = None, status: Optional[str] = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(Project)
    if bu_id is not None:
        q = q.filter(Project.business_unit_id == bu_id)
    if status:
        q = q.filter(Project.status == status)
    rows = q.order_by(Project.created_at.desc()).limit(300).all()
    return {"items": [serialize_project(db, p) for p in rows]}


@router.post("/projects")
def create_project(payload: ProjectIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    p = Project(business_unit_id=bu_id, name=payload.name.strip(),
                project_type=payload.project_type, customer_id=payload.customer_id,
                start_date=payload.start_date, end_date=payload.end_date,
                notes=payload.notes, created_by_id=user.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return serialize_project(db, p)


@router.post("/projects/{project_id}/status")
def set_project_status(project_id: int, status: str, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    bu_scope(user, p.business_unit_id)
    if status not in ("active", "completed", "cancelled"):
        raise HTTPException(400, "status must be active/completed/cancelled")
    p.status = status
    db.commit()
    return serialize_project(db, p)


@router.get("/projects/{project_id}/pl")
def project_pl(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Per-project P&L from linked documents (all in MMK).

    Revenue: sent invoices (net of tax). Costs: received/paid POs +
    approved expenses. COGS and salaries posted directly in the journal
    are BU-level and not split per project (v1 scope).
    """
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    bu_scope(user, p.business_unit_id)
    revenue = ZERO
    inv_count = 0
    for inv in db.query(SalesInvoice).filter(SalesInvoice.project_id == project_id,
                                             SalesInvoice.status.in_(("sent", "partial", "paid"))).all():
        a = invoice_amounts(db, inv)
        revenue += a["subtotal"] * Decimal(str(getattr(inv, "exchange_rate", 1) or 1))
        inv_count += 1
    po_cost = ZERO
    for po in db.query(PurchaseOrder).filter(PurchaseOrder.project_id == project_id,
                                             PurchaseOrder.status.in_(("received", "paid"))).all():
        po_cost += po_total(po) * Decimal(str(getattr(po, "exchange_rate", 1) or 1))
    exp_cost = ZERO
    for e in db.query(ExpenseRequest).filter(ExpenseRequest.project_id == project_id,
                                             ExpenseRequest.status == "approved").all():
        exp_cost += Decimal(str(e.amount or 0))
    costs = po_cost + exp_cost
    return {
        "project": serialize_project(db, p),
        "revenue": fmt(revenue), "po_costs": fmt(po_cost), "expense_costs": fmt(exp_cost),
        "total_costs": fmt(costs), "profit": fmt(revenue - costs),
        "margin_pct": fmt((revenue - costs) / revenue * 100) if revenue else None,
        "invoice_count": inv_count,
    }


# ------------------------------------------------------------------ shipments (logistics)

def serialize_shipment(s: Shipment) -> dict:
    costs = (Decimal(str(s.fuel_cost or 0)) + Decimal(str(s.toll_fees or 0))
             + Decimal(str(s.driver_fees or 0)) + Decimal(str(s.other_costs or 0)))
    return {
        "id": s.id, "business_unit_id": s.business_unit_id, "project_id": s.project_id,
        "customer_id": s.customer_id, "customer_name": s.customer.name if s.customer else None,
        "reference": s.reference, "origin": s.origin, "destination": s.destination,
        "vehicle_no": s.vehicle_no, "driver_name": s.driver_name,
        "shipment_date": s.shipment_date.isoformat() if s.shipment_date else None,
        "status": s.status, "freight_charge": fmt(s.freight_charge),
        "fuel_cost": fmt(s.fuel_cost), "toll_fees": fmt(s.toll_fees),
        "driver_fees": fmt(s.driver_fees), "other_costs": fmt(s.other_costs),
        "total_costs": fmt(costs), "profit": fmt(Decimal(str(s.freight_charge or 0)) - costs),
        "invoice_id": s.invoice_id,
    }


class ShipmentIn(BaseModel):
    business_unit_id: Optional[int] = None
    project_id: Optional[int] = None
    customer_id: Optional[int] = None
    reference: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    vehicle_no: Optional[str] = None
    driver_name: Optional[str] = None
    shipment_date: date
    freight_charge: float = 0
    fuel_cost: float = 0
    toll_fees: float = 0
    driver_fees: float = 0
    other_costs: float = 0


@router.get("/shipments")
def list_shipments(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(Shipment)
    if bu_id is not None:
        q = q.filter(Shipment.business_unit_id == bu_id)
    rows = q.order_by(Shipment.shipment_date.desc(), Shipment.id.desc()).limit(300).all()
    return {"items": [serialize_shipment(s) for s in rows]}


@router.post("/shipments")
def create_shipment(payload: ShipmentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    s = Shipment(business_unit_id=bu_id, created_by_id=user.id,
                 **{k: v for k, v in payload.model_dump().items() if k != "business_unit_id"})
    db.add(s)
    db.commit()
    db.refresh(s)
    return serialize_shipment(s)


@router.post("/shipments/{shipment_id}/status")
def set_shipment_status(shipment_id: int, status: str, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    s = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not s:
        raise HTTPException(404, "Shipment not found")
    bu_scope(user, s.business_unit_id)
    if status not in ("planned", "in_transit", "delivered", "cancelled"):
        raise HTTPException(400, "Invalid shipment status")
    s.status = status
    db.commit()
    return serialize_shipment(s)


# ------------------------------------------------------------------ manufacturing

def serialize_bom(db: Session, bom: ManufacturingBom) -> dict:
    return {
        "id": bom.id, "business_unit_id": bom.business_unit_id,
        "finished_item_id": bom.finished_item_id,
        "finished_item_name": bom.finished_item.name if bom.finished_item else None,
        "name": bom.name, "is_active": bom.is_active,
        "components": [{"id": c.id, "component_item_id": c.component_item_id,
                        "component_name": c.component_item.name if c.component_item else None,
                        "quantity_per_unit": fmt(c.quantity_per_unit)} for c in bom.components],
    }


class BomIn(BaseModel):
    business_unit_id: Optional[int] = None
    finished_item_id: int
    name: Optional[str] = None
    components: list[dict] = Field(min_length=1)  # [{component_item_id, quantity_per_unit}]


@router.get("/boms")
def list_boms(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(ManufacturingBom)
    if bu_id is not None:
        q = q.filter(ManufacturingBom.business_unit_id == bu_id)
    return {"items": [serialize_bom(db, b) for b in q.order_by(ManufacturingBom.id.desc()).all()]}


@router.post("/boms")
def create_bom(payload: BomIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    fin = db.query(InventoryItem).filter(InventoryItem.id == payload.finished_item_id,
                                         InventoryItem.business_unit_id == bu_id).first()
    if not fin:
        raise HTTPException(404, "Finished item not found in this BU")
    bom = ManufacturingBom(business_unit_id=bu_id, finished_item_id=fin.id, name=payload.name)
    db.add(bom)
    db.flush()
    for c in payload.components:
        comp = db.query(InventoryItem).filter(InventoryItem.id == c.get("component_item_id"),
                                              InventoryItem.business_unit_id == bu_id).first()
        if not comp:
            raise HTTPException(400, "Component item not found in this BU")
        db.add(BomComponent(bom_id=bom.id, component_item_id=comp.id,
                            quantity_per_unit=Decimal(str(c.get("quantity_per_unit") or 0))))
    db.commit()
    db.refresh(bom)
    return serialize_bom(db, bom)


class ProductionIn(BaseModel):
    bom_id: int
    quantity: float = Field(gt=0)
    labour_cost: float = 0
    overhead_cost: float = 0
    production_date: Optional[date] = None


def serialize_production(o: ProductionOrder) -> dict:
    return {
        "id": o.id, "business_unit_id": o.business_unit_id, "bom_id": o.bom_id,
        "finished_item_name": (o.bom.finished_item.name if o.bom and o.bom.finished_item else None),
        "quantity": fmt(o.quantity), "labour_cost": fmt(o.labour_cost),
        "overhead_cost": fmt(o.overhead_cost), "materials_cost": fmt(o.materials_cost),
        "unit_cost": fmt(o.unit_cost), "status": o.status,
        "production_date": o.production_date.isoformat() if o.production_date else None,
    }


@router.get("/production-orders")
def list_production(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(ProductionOrder)
    if bu_id is not None:
        q = q.filter(ProductionOrder.business_unit_id == bu_id)
    return {"items": [serialize_production(o) for o in q.order_by(ProductionOrder.id.desc()).limit(200).all()]}


@router.post("/production-orders")
def create_production(payload: ProductionIn, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    bom = db.query(ManufacturingBom).filter(ManufacturingBom.id == payload.bom_id).first()
    if not bom:
        raise HTTPException(404, "BOM not found")
    bu_scope(user, bom.business_unit_id)
    o = ProductionOrder(business_unit_id=bom.business_unit_id, bom_id=bom.id,
                        quantity=Decimal(str(payload.quantity)),
                        labour_cost=Decimal(str(payload.labour_cost)),
                        overhead_cost=Decimal(str(payload.overhead_cost)),
                        production_date=payload.production_date or date.today(),
                        created_by_id=user.id)
    db.add(o)
    db.commit()
    db.refresh(o)
    return serialize_production(o)


@router.post("/production-orders/{order_id}/complete")
def complete_production(order_id: int, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Consume components (FEFO over batches) and stock the finished goods
    at full production cost. The added labour/overhead value posts to the
    journal: Dr 1140 Inventory / Cr 2120 Accrued."""
    o = db.query(ProductionOrder).filter(ProductionOrder.id == order_id).first()
    if not o:
        raise HTTPException(404, "Production order not found")
    bu_scope(user, o.business_unit_id)
    if o.status != "planned":
        raise HTTPException(400, f"Cannot complete a {o.status} order")
    bom = o.bom
    qty = Decimal(str(o.quantity))
    # check + value components
    materials = ZERO
    needs = []
    for c in bom.components:
        item = c.component_item
        need = Decimal(str(c.quantity_per_unit)) * qty
        if Decimal(str(item.current_stock or 0)) < need:
            raise HTTPException(400, f"Not enough stock of {item.name}: need {need}, have {item.current_stock}")
        materials += need * Decimal(str(item.unit_cost or 0))
        needs.append((item, need))
    for item, need in needs:
        item.current_stock = Decimal(str(item.current_stock)) - need
        item.updated_at = datetime.utcnow()
        consume_batches_fefo(db, item, need)
        db.add(StockMovement(item_id=item.id, movement_type="out", quantity=need,
                             reason=f"Production order #{o.id}", recorded_by_id=user.id))
    total_cost = materials + Decimal(str(o.labour_cost or 0)) + Decimal(str(o.overhead_cost or 0))
    unit_cost = total_cost / qty if qty else ZERO
    fin = bom.finished_item
    old_qty = Decimal(str(fin.current_stock or 0))
    old_cost = Decimal(str(fin.unit_cost or 0))
    new_qty = old_qty + qty
    fin.unit_cost = ((old_qty * old_cost) + total_cost) / new_qty if new_qty else unit_cost
    fin.current_stock = new_qty
    fin.updated_at = datetime.utcnow()
    db.add(StockMovement(item_id=fin.id, movement_type="in", quantity=qty,
                         reason=f"Production order #{o.id} completed", recorded_by_id=user.id))
    o.materials_cost = materials
    o.unit_cost = unit_cost
    o.status = "completed"
    db.commit()
    added_value = Decimal(str(o.labour_cost or 0)) + Decimal(str(o.overhead_cost or 0))
    if added_value > 0:
        try:
            from src.routers.accounting import account_by_code, create_entry
            inv_acc = account_by_code(db, o.business_unit_id, "1140")
            acc_acc = account_by_code(db, o.business_unit_id, "2120")
            if inv_acc and acc_acc:
                create_entry(db, o.business_unit_id, o.production_date or date.today(),
                             f"Production order #{o.id} — labour & overhead capitalised",
                             "production", user.id,
                             [{"account_id": inv_acc.id, "debit": added_value, "credit": 0},
                              {"account_id": acc_acc.id, "debit": 0, "credit": added_value}],
                             source_id=o.id, post=True)
                db.commit()
        except Exception:
            db.rollback()
    db.refresh(o)
    return serialize_production(o)


# ------------------------------------------------------------------ real estate

def serialize_unit(u: PropertyUnit) -> dict:
    return {
        "id": u.id, "business_unit_id": u.business_unit_id, "development": u.development,
        "unit_no": u.unit_no, "unit_type": u.unit_type, "size_sqft": fmt(u.size_sqft),
        "asking_price": fmt(u.asking_price), "status": u.status,
        "sale_invoice_id": u.sale_invoice_id, "notes": u.notes,
    }


class UnitIn(BaseModel):
    business_unit_id: Optional[int] = None
    development: Optional[str] = None
    unit_no: str = Field(min_length=1, max_length=60)
    unit_type: str = "apartment"
    size_sqft: Optional[float] = None
    asking_price: Optional[float] = None
    notes: Optional[str] = None


@router.get("/property-units")
def list_units(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(PropertyUnit)
    if bu_id is not None:
        q = q.filter(PropertyUnit.business_unit_id == bu_id)
    return {"items": [serialize_unit(u) for u in q.order_by(PropertyUnit.id.desc()).limit(300).all()]}


@router.post("/property-units")
def create_unit(payload: UnitIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    u = PropertyUnit(business_unit_id=bu_id, **{k: v for k, v in payload.model_dump().items()
                                                if k != "business_unit_id"})
    db.add(u)
    db.commit()
    db.refresh(u)
    return serialize_unit(u)


class SellUnitIn(BaseModel):
    customer_id: int
    price: Optional[float] = None
    sale_date: Optional[date] = None
    tax_rate: float = 0
    downpayment_pct: float = 0
    installments: int = 0          # number of monthly installments for the balance
    first_due_date: Optional[date] = None


@router.post("/property-units/{unit_id}/sell")
def sell_unit(unit_id: int, payload: SellUnitIn, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Sell a unit: creates a sales invoice (draft) and, when requested, a
    monthly installment plan for the balance after the downpayment."""
    u = db.query(PropertyUnit).filter(PropertyUnit.id == unit_id).first()
    if not u:
        raise HTTPException(404, "Unit not found")
    bu_scope(user, u.business_unit_id)
    if u.status not in ("available", "reserved"):
        raise HTTPException(400, f"Unit is {u.status}")
    cust = db.query(Customer).filter(Customer.id == payload.customer_id,
                                     Customer.business_unit_id == u.business_unit_id).first()
    if not cust:
        raise HTTPException(404, "Customer not found in this BU")
    price = Decimal(str(payload.price)) if payload.price else Decimal(str(u.asking_price or 0))
    if price <= 0:
        raise HTTPException(400, "A sale price is required")
    from src.routers.ops import InvoiceIn, InvoiceLineIn, create_invoice
    inv = create_invoice(InvoiceIn(
        business_unit_id=u.business_unit_id, customer_id=cust.id,
        invoice_date=payload.sale_date or date.today(), tax_rate=payload.tax_rate,
        notes=f"Property sale: {u.development or ''} {u.unit_no}".strip(),
        lines=[InvoiceLineIn(description=f"{u.development or ''} Unit {u.unit_no} ({u.unit_type})".strip(),
                             quantity=1, unit_price=float(price))],
    ), user=user, db=db)
    u.status = "reserved"
    u.sale_invoice_id = inv["id"]
    db.commit()
    plan = []
    if payload.installments > 0:
        total = Decimal(str(inv["total"]))
        down = (total * Decimal(str(payload.downpayment_pct)) / Decimal("100")).quantize(Decimal("0.01"))
        balance = total - down
        per = (balance / payload.installments).quantize(Decimal("0.01"))
        first = payload.first_due_date or (payload.sale_date or date.today())
        seq = 0
        if down > 0:
            seq += 1
            db.add(Installment(invoice_id=inv["id"], seq=seq, due_date=first, amount=down))
            plan.append({"seq": seq, "due_date": first.isoformat(), "amount": fmt(down)})
        for i in range(payload.installments):
            seq += 1
            y = first.year + (first.month - 1 + i + (1 if down > 0 else 0)) // 12
            m = (first.month - 1 + i + (1 if down > 0 else 0)) % 12 + 1
            due = date(y, m, min(first.day, 28))
            amt = per if i < payload.installments - 1 else balance - per * (payload.installments - 1)
            db.add(Installment(invoice_id=inv["id"], seq=seq, due_date=due, amount=amt))
            plan.append({"seq": seq, "due_date": due.isoformat(), "amount": fmt(amt)})
        db.commit()
    return {"unit": serialize_unit(u), "invoice_id": inv["id"], "invoice_no": inv["invoice_no"],
            "total": inv["total"], "installments": plan}


@router.get("/invoices/{invoice_id}/installments")
def list_installments(invoice_id: int, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    inv = db.query(SalesInvoice).filter(SalesInvoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    bu_scope(user, inv.business_unit_id)
    rows = db.query(Installment).filter(Installment.invoice_id == invoice_id).order_by(Installment.seq).all()
    today_d = date.today()
    return {"items": [{
        "id": r.id, "seq": r.seq, "due_date": r.due_date.isoformat(),
        "amount": fmt(r.amount), "paid_amount": fmt(r.paid_amount),
        "balance": fmt(Decimal(str(r.amount)) - Decimal(str(r.paid_amount))),
        "overdue": (r.due_date < today_d and Decimal(str(r.paid_amount)) < Decimal(str(r.amount))),
    } for r in rows]}


def allocate_installments(db: Session, invoice_id: int) -> None:
    """Re-allocate an invoice's total paid amount FIFO across its plan."""
    from src.routers.ops import invoice_amounts
    inv = db.query(SalesInvoice).filter(SalesInvoice.id == invoice_id).first()
    if not inv:
        return
    rows = db.query(Installment).filter(Installment.invoice_id == invoice_id).order_by(Installment.seq).all()
    if not rows:
        return
    remaining = invoice_amounts(db, inv)["paid"]
    for r in rows:
        take = min(Decimal(str(r.amount)), remaining)
        r.paid_amount = take
        remaining -= take
    db.commit()


class LeaseIn(BaseModel):
    business_unit_id: Optional[int] = None
    unit_id: int
    tenant_id: int
    monthly_rent: float = Field(gt=0)
    deposit_amount: float = 0
    start_date: date
    end_date: Optional[date] = None


def serialize_lease(l: LeaseContract) -> dict:
    return {
        "id": l.id, "business_unit_id": l.business_unit_id, "unit_id": l.unit_id,
        "unit_no": l.unit.unit_no if l.unit else None,
        "development": l.unit.development if l.unit else None,
        "tenant_id": l.tenant_id, "tenant_name": l.tenant.name if l.tenant else None,
        "monthly_rent": fmt(l.monthly_rent), "deposit_amount": fmt(l.deposit_amount),
        "start_date": l.start_date.isoformat() if l.start_date else None,
        "end_date": l.end_date.isoformat() if l.end_date else None,
        "status": l.status, "last_billed_period": l.last_billed_period,
    }


@router.get("/leases")
def list_leases(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(LeaseContract)
    if bu_id is not None:
        q = q.filter(LeaseContract.business_unit_id == bu_id)
    return {"items": [serialize_lease(l) for l in q.order_by(LeaseContract.id.desc()).limit(200).all()]}


@router.post("/leases")
def create_lease(payload: LeaseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    unit = db.query(PropertyUnit).filter(PropertyUnit.id == payload.unit_id,
                                         PropertyUnit.business_unit_id == bu_id).first()
    if not unit:
        raise HTTPException(404, "Unit not found in this BU")
    if unit.status not in ("available", "rented"):
        raise HTTPException(400, f"Unit is {unit.status}")
    l = LeaseContract(business_unit_id=bu_id, created_by_id=user.id,
                      **{k: v for k, v in payload.model_dump().items() if k != "business_unit_id"})
    db.add(l)
    unit.status = "rented"
    db.commit()
    db.refresh(l)
    return serialize_lease(l)


@router.post("/leases/{lease_id}/bill")
def bill_lease(lease_id: int, period: Optional[str] = None, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Create this month's rent invoice (draft) for an active lease."""
    l = db.query(LeaseContract).filter(LeaseContract.id == lease_id).first()
    if not l:
        raise HTTPException(404, "Lease not found")
    bu_scope(user, l.business_unit_id)
    if l.status != "active":
        raise HTTPException(400, "Lease is not active")
    period = period or date.today().strftime("%Y-%m")
    if l.last_billed_period == period:
        raise HTTPException(400, f"Rent already billed for {period}")
    from src.routers.ops import InvoiceIn, InvoiceLineIn, create_invoice
    y, m = period.split("-")
    inv = create_invoice(InvoiceIn(
        business_unit_id=l.business_unit_id, customer_id=l.tenant_id,
        invoice_date=date(int(y), int(m), 1), tax_rate=0,
        notes=f"Monthly rent {period} — {l.unit.development or ''} {l.unit.unit_no if l.unit else ''}",
        lines=[InvoiceLineIn(description=f"Rent {period} — Unit {l.unit.unit_no if l.unit else ''}",
                             quantity=1, unit_price=float(l.monthly_rent))],
    ), user=user, db=db)
    l.last_billed_period = period
    db.commit()
    return {"invoice_id": inv["id"], "invoice_no": inv["invoice_no"], "period": period}


# ------------------------------------------------------------------ principal report

@router.get("/principal-report")
def principal_report(business_unit_id: Optional[int] = None, period: Optional[str] = None,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sales grouped by principal (brand owner) and brand, from invoice
    lines linked to inventory items. Amounts in MMK."""
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(SalesInvoice).filter(SalesInvoice.status.in_(("sent", "partial", "paid")))
    if bu_id is not None:
        q = q.filter(SalesInvoice.business_unit_id == bu_id)
    if period:
        q = q.filter(func.strftime("%Y-%m", SalesInvoice.invoice_date) == period) \
            if db.bind.dialect.name == "sqlite" else \
            q.filter(func.to_char(SalesInvoice.invoice_date, "YYYY-MM") == period)
    groups: dict[str, dict] = {}
    for inv in q.all():
        rate = Decimal(str(getattr(inv, "exchange_rate", 1) or 1))
        for line in inv.lines:
            item = line.inventory_item if getattr(line, "inventory_item_id", None) else None
            principal = None
            if item and item.principal_id:
                sup = db.query(Supplier).filter(Supplier.id == item.principal_id).first()
                principal = sup.name if sup else None
            key = f"{principal or '—'}|{(item.brand if item else None) or '—'}"
            g = groups.setdefault(key, {"principal": principal or "—",
                                        "brand": (item.brand if item else None) or "—",
                                        "quantity": ZERO, "revenue": ZERO, "lines": 0})
            g["quantity"] += Decimal(str(line.quantity or 0))
            g["revenue"] += Decimal(str(line.quantity or 0)) * Decimal(str(line.unit_price or 0)) * rate
            g["lines"] += 1
    items = sorted(groups.values(), key=lambda g: -g["revenue"])
    return {"items": [{"principal": g["principal"], "brand": g["brand"],
                       "quantity": fmt(g["quantity"]), "revenue": fmt(g["revenue"]),
                       "lines": g["lines"]} for g in items],
            "total_revenue": fmt(sum(g["revenue"] for g in items))}


# ------------------------------------------------------------------ scheduled: batch expiry alerts

def run_check_batch_expiry(db: Session, days: int = 30) -> dict:
    """Notify BU CEOs + Chairman/HO about batches expiring within `days`
    (7-day throttle per batch, mirroring the document-expiry pattern)."""
    from src.models import Notification, User
    cutoff = date.today() + timedelta(days=days)
    batches = db.query(StockBatch).filter(StockBatch.quantity > 0,
                                          StockBatch.expiry_date.isnot(None),
                                          StockBatch.expiry_date <= cutoff).all()
    notified = 0
    for b in batches:
        if b.expiry_notified_at and (datetime.utcnow() - b.expiry_notified_at).days < 7:
            continue
        item = b.item
        if not item:
            continue
        recipients = db.query(User).filter(
            User.is_active == True,  # noqa: E712
            ((User.role == "CEO") & (User.business_unit_id == item.business_unit_id))
            | (User.role.in_(("Chairman", "HO")))).all()
        days_left = (b.expiry_date - date.today()).days
        for u in recipients:
            db.add(Notification(
                user_id=u.id, type="batch_expiry",
                title="Stock batch expiring" if days_left >= 0 else "Stock batch EXPIRED",
                message=f"{item.name} — batch {b.batch_no} expires {b.expiry_date.isoformat()} "
                        f"({days_left} days), {float(b.quantity)} left",
                link="/admin/operations-center"))
            notified += 1
        b.expiry_notified_at = datetime.utcnow()
    db.commit()
    return {"checked": len(batches), "notified": notified}


# ------------------------------------------------------------------ stock transfers between BUs (ERP completion)

def serialize_transfer(db: Session, t) -> dict:
    total = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_cost or 0)) for l in t.lines), Decimal("0"))
    return {
        "id": t.id, "transfer_no": t.transfer_no,
        "from_business_unit_id": t.from_business_unit_id,
        "from_bu_name": t.from_business_unit.name if t.from_business_unit else None,
        "to_business_unit_id": t.to_business_unit_id,
        "to_bu_name": t.to_business_unit.name if t.to_business_unit else None,
        "status": t.status, "notes": t.notes,
        "total_cost": float(total),
        "sent_at": t.sent_at.isoformat() if t.sent_at else None,
        "received_at": t.received_at.isoformat() if t.received_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "lines": [{"id": l.id, "inventory_item_id": l.inventory_item_id,
                   "item_name": l.inventory_item.name if l.inventory_item else None,
                   "dest_inventory_item_id": l.dest_inventory_item_id,
                   "quantity": float(l.quantity),
                   "unit_cost": float(l.unit_cost or 0)} for l in t.lines],
    }


class TransferLineIn(BaseModel):
    inventory_item_id: int
    quantity: float = Field(gt=0)


class StockTransferIn(BaseModel):
    from_business_unit_id: int
    to_business_unit_id: int
    notes: Optional[str] = None
    lines: list[TransferLineIn] = Field(min_length=1)


def _transfer_or_404(db: Session, transfer_id: int):
    from src.models import StockTransfer
    t = db.query(StockTransfer).filter(StockTransfer.id == transfer_id).first()
    if not t:
        raise HTTPException(404, "Stock transfer not found")
    return t


def _post_transfer_journal(db: Session, bu_id: int, lines_spec, user_id: int,
                           transfer_id: int, description: str) -> None:
    """Best-effort journal in one BU's books (PO/invoice helper pattern)."""
    try:
        from src.routers.accounting import create_entry
        create_entry(db, bu_id, date.today(), description, "stock_transfer",
                     user_id, lines_spec, source_id=transfer_id, post=True)
        db.commit()
    except Exception:
        db.rollback()


@router.get("/stock-transfers")
def list_stock_transfers(from_business_unit_id: Optional[int] = None,
                         to_business_unit_id: Optional[int] = None,
                         status: Optional[str] = None,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from src.models import StockTransfer
    if user.role == "CEO":
        from_business_unit_id = user.business_unit_id
    q = db.query(StockTransfer)
    if from_business_unit_id:
        q = q.filter(StockTransfer.from_business_unit_id == from_business_unit_id)
    if to_business_unit_id:
        q = q.filter(StockTransfer.to_business_unit_id == to_business_unit_id)
    if status:
        q = q.filter(StockTransfer.status == status)
    rows = q.order_by(StockTransfer.id.desc()).limit(200).all()
    return {"items": [serialize_transfer(db, t) for t in rows]}


@router.post("/stock-transfers")
def create_stock_transfer(payload: StockTransferIn, user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    from src.models import StockTransfer, StockTransferLine
    bu_scope(user, payload.from_business_unit_id)
    if payload.from_business_unit_id == payload.to_business_unit_id:
        raise HTTPException(400, "Source and destination must be different BUs")
    if not db.query(BusinessUnit).filter(BusinessUnit.id == payload.to_business_unit_id).first():
        raise HTTPException(404, "Destination business unit not found")
    for ln in payload.lines:
        item = db.query(InventoryItem).filter(InventoryItem.id == ln.inventory_item_id).first()
        if not item or item.business_unit_id != payload.from_business_unit_id:
            raise HTTPException(400, f"Item {ln.inventory_item_id} does not belong to the source BU")
    count = db.query(StockTransfer).filter(
        StockTransfer.from_business_unit_id == payload.from_business_unit_id).count()
    t = StockTransfer(from_business_unit_id=payload.from_business_unit_id,
                      to_business_unit_id=payload.to_business_unit_id,
                      transfer_no=f"TRF-{payload.from_business_unit_id}-{count + 1:04d}",
                      status="draft", notes=payload.notes, created_by_id=user.id)
    db.add(t)
    db.flush()
    for ln in payload.lines:
        db.add(StockTransferLine(transfer_id=t.id, inventory_item_id=ln.inventory_item_id,
                                 quantity=Decimal(str(ln.quantity))))
    db.commit()
    db.refresh(t)
    return serialize_transfer(db, t)


@router.post("/stock-transfers/{transfer_id}/send")
def send_stock_transfer(transfer_id: int, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Stock out of the source BU at moving-average cost; the source BU's
    books post Dr 1180 Intercompany Receivable / Cr 1140 Inventory."""
    from src.models import StockMovement as SM
    t = _transfer_or_404(db, transfer_id)
    bu_scope(user, t.from_business_unit_id)
    if t.status != "draft":
        raise HTTPException(400, f"Cannot send a transfer in status {t.status}")
    for ln in t.lines:
        item = ln.inventory_item
        if Decimal(str(item.current_stock or 0)) < Decimal(str(ln.quantity)):
            raise HTTPException(400, f"Not enough stock of {item.name} in the source BU")
    total_cost = Decimal("0")
    for ln in t.lines:
        item = ln.inventory_item
        qty = Decimal(str(ln.quantity))
        cost = Decimal(str(item.unit_cost or 0))
        ln.unit_cost = cost
        total_cost += qty * cost
        item.current_stock = Decimal(str(item.current_stock or 0)) - qty
        item.updated_at = datetime.utcnow()
        db.add(SM(item_id=item.id, movement_type="out", quantity=qty,
                  reason=f"Stock transfer {t.transfer_no} to BU {t.to_business_unit_id}",
                  recorded_by_id=user.id))
        if getattr(item, "requires_expiry", False):
            consume_batches_fefo(db, item, qty)
    t.status = "in_transit"
    t.sent_at = datetime.utcnow()
    db.commit()
    if total_cost > 0:
        try:
            from src.routers.accounting import _ensure_intercompany_accounts, account_by_code
            _ensure_intercompany_accounts(db, t.from_business_unit_id)
            db.commit()
            recv = account_by_code(db, t.from_business_unit_id, "1180")
            inv_acc = account_by_code(db, t.from_business_unit_id, "1140")
            if recv and inv_acc:
                _post_transfer_journal(
                    db, t.from_business_unit_id,
                    [{"account_id": recv.id, "debit": total_cost, "credit": 0},
                     {"account_id": inv_acc.id, "debit": 0, "credit": total_cost}],
                    user.id, t.id,
                    f"Stock transfer {t.transfer_no} sent to {t.to_business_unit.name if t.to_business_unit else ''}")
        except Exception:
            db.rollback()
    db.refresh(t)
    return serialize_transfer(db, t)


@router.post("/stock-transfers/{transfer_id}/receive")
def receive_stock_transfer(transfer_id: int, user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """Stock into the destination BU at the transfer cost (moving average
    updated); destination books post Dr 1140 / Cr 2180."""
    from src.models import StockMovement as SM
    t = _transfer_or_404(db, transfer_id)
    if user.role == "CEO" and user.business_unit_id not in (t.from_business_unit_id, t.to_business_unit_id):
        raise HTTPException(403, "Not your business unit")
    if t.status != "in_transit":
        raise HTTPException(400, f"Cannot receive a transfer in status {t.status}")
    total_cost = Decimal("0")
    for ln in t.lines:
        src_item = ln.inventory_item
        qty = Decimal(str(ln.quantity))
        cost = Decimal(str(ln.unit_cost or 0))
        total_cost += qty * cost
        dst = db.query(InventoryItem).filter(
            InventoryItem.business_unit_id == t.to_business_unit_id,
            InventoryItem.name == src_item.name, InventoryItem.is_active == True).first()  # noqa: E712
        if dst is None:
            dst = InventoryItem(business_unit_id=t.to_business_unit_id, name=src_item.name,
                                sku=src_item.sku, category=src_item.category, unit=src_item.unit,
                                current_stock=0, reorder_level=src_item.reorder_level,
                                unit_cost=cost, brand=getattr(src_item, "brand", None),
                                principal_id=getattr(src_item, "principal_id", None),
                                requires_expiry=getattr(src_item, "requires_expiry", False))
            db.add(dst)
            db.flush()
        old_qty = Decimal(str(dst.current_stock or 0))
        old_avg = Decimal(str(dst.unit_cost or 0))
        new_qty = old_qty + qty
        dst.unit_cost = ((old_qty * old_avg + qty * cost) / new_qty) if new_qty > 0 else cost
        dst.current_stock = new_qty
        dst.updated_at = datetime.utcnow()
        ln.dest_inventory_item_id = dst.id
        db.add(SM(item_id=dst.id, movement_type="in", quantity=qty,
                  reason=f"Stock transfer {t.transfer_no} from BU {t.from_business_unit_id}",
                  recorded_by_id=user.id))
    t.status = "received"
    t.received_at = datetime.utcnow()
    db.commit()
    if total_cost > 0:
        try:
            from src.routers.accounting import _ensure_intercompany_accounts, account_by_code
            _ensure_intercompany_accounts(db, t.to_business_unit_id)
            db.commit()
            inv_acc = account_by_code(db, t.to_business_unit_id, "1140")
            pay = account_by_code(db, t.to_business_unit_id, "2180")
            if inv_acc and pay:
                _post_transfer_journal(
                    db, t.to_business_unit_id,
                    [{"account_id": inv_acc.id, "debit": total_cost, "credit": 0},
                     {"account_id": pay.id, "debit": 0, "credit": total_cost}],
                    user.id, t.id,
                    f"Stock transfer {t.transfer_no} received from {t.from_business_unit.name if t.from_business_unit else ''}")
        except Exception:
            db.rollback()
    db.refresh(t)
    return serialize_transfer(db, t)


@router.post("/stock-transfers/{transfer_id}/cancel")
def cancel_stock_transfer(transfer_id: int, user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Draft: nothing posted, just cancel. In-transit: reverse the source
    journal and restock the source BU. Received transfers cannot cancel."""
    from src.models import JournalEntry, StockMovement as SM
    from src.routers.ops import restock_batches
    t = _transfer_or_404(db, transfer_id)
    bu_scope(user, t.from_business_unit_id)
    if t.status == "draft":
        t.status = "cancelled"
        db.commit()
        db.refresh(t)
        return serialize_transfer(db, t)
    if t.status != "in_transit":
        raise HTTPException(400, f"Cannot cancel a transfer in status {t.status}")
    for ln in t.lines:
        item = ln.inventory_item
        qty = Decimal(str(ln.quantity))
        item.current_stock = Decimal(str(item.current_stock or 0)) + qty
        item.updated_at = datetime.utcnow()
        db.add(SM(item_id=item.id, movement_type="in", quantity=qty,
                  reason=f"Stock transfer {t.transfer_no} cancelled",
                  recorded_by_id=user.id))
        if getattr(item, "requires_expiry", False):
            restock_batches(db, item, qty, batch_no="TRF-RETURN")
    from src.routers.accounting import _reverse_entry
    entries = db.query(JournalEntry).filter(
        JournalEntry.source == "stock_transfer", JournalEntry.source_id == t.id,
        JournalEntry.business_unit_id == t.from_business_unit_id,
        JournalEntry.status == "posted", JournalEntry.reversed_by_id == None).all()  # noqa: E711
    for e in entries:
        _reverse_entry(db, e, user.id)
    t.status = "cancelled"
    db.commit()
    db.refresh(t)
    return serialize_transfer(db, t)
