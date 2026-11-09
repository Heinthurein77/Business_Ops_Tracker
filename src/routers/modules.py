"""Nature modules (2026-10-21): batch/expiry inventory, projects &
per-project P&L, logistics shipments, manufacturing (BOM + production
orders), real-estate units / installment plans / leases, and the
principal (brand-owner) sales report.

Built on the shared core: stock lives in InventoryItem, money posts
through the accounting journal, sales through SalesInvoice.
"""
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.database import get_db
from src.utils import log_audit, server_error, today_local, utcnow, validate_period
from src.models import (
    BomComponent, BusinessUnit, Customer, ExpenseRequest, Installment,
    InventoryItem, LeaseContract, ManufacturingBom, ProductionOrder,
    Project, PropertyUnit, PurchaseOrder, SalesInvoice, Shipment,
    StockBatch, StockMovement, Supplier, User,
)
from src.routers.ops import (_commit_or_409, _idempotency_lookup,
                             _idempotency_store, _stock_add, _stock_sub,
                             bu_scope, invoice_amounts, po_total,
                             sales_returns_total)

router = APIRouter(prefix="/api/modules", tags=["modules"])
ZERO = Decimal("0")

logger = logging.getLogger(__name__)

def _log_audit(db, user, action, details=""):
    """Local wrapper around src.utils.log_audit (R-3 audit trail).

    Two normalisations, both deliberate: (1) callers in the test suite pass
    duck-typed user objects (SimpleNamespace with id/role but no
    .username), which log_audit reads directly — the actor is re-shaped
    into the (id, username) pair log_audit needs instead of crashing the
    request; (2) an audit-write failure must never fail a business
    operation that already committed, so it is logged and swallowed (the
    D-8 direction: audit is evidence, not part of the money transaction).
    """
    from types import SimpleNamespace

    actor = user
    if user is not None and not hasattr(user, "username"):
        actor = SimpleNamespace(
            id=getattr(user, "id", None),
            username=f"user-{getattr(user, 'id', '?')}",
        )
    try:
        log_audit(db, actor, action, details)
    except Exception as exc:  # noqa: BLE001 - audit must not break the op
        logger.warning("audit write failed for %s: %s", action, exc)



def fmt(v) -> float:
    return float(Decimal(str(v or 0)))


# ------------------------------------------------------------------ batches & expiry

def serialize_batch(b: StockBatch) -> dict:
    return {
        "id": b.id, "item_id": b.item_id, "batch_no": b.batch_no,
        "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
        "quantity": fmt(b.quantity), "unit_cost": fmt(b.unit_cost),
        "days_to_expiry": (b.expiry_date - today_local()).days if b.expiry_date else None,
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
    # CC-1: lock the item row — two concurrent batch receipts used to
    # compute the weighted-average unit_cost from the same old values,
    # and the second commit silently overwrote the first.
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).with_for_update().first()
    if not item:
        raise HTTPException(404, "Item not found")
    bu_scope(user, item.business_unit_id)
    if not getattr(item, "requires_expiry", False):
        raise HTTPException(400, "This item does not require expiry tracking; receive stock with a stock movement instead")
    dup = db.query(StockBatch).filter(StockBatch.item_id == item.id,
                                      StockBatch.batch_no == payload.batch_no.strip()).first()
    if dup:
        raise HTTPException(400, f"Batch {payload.batch_no.strip()} already exists for this item")
    qty = Decimal(str(payload.quantity))
    cost = Decimal(str(payload.unit_cost))
    old_qty = Decimal(str(item.current_stock or 0))
    old_cost = Decimal(str(item.unit_cost or 0))
    new_qty = old_qty + qty
    item.unit_cost = ((old_qty * old_cost) + (qty * cost)) / new_qty if new_qty else cost
    _stock_add(db, item, qty)
    item.updated_at = utcnow()
    b = StockBatch(item_id=item.id, batch_no=payload.batch_no.strip(),
                   expiry_date=payload.expiry_date, quantity=qty, unit_cost=cost)
    db.add(b)
    db.add(StockMovement(item_id=item.id, movement_type="in", quantity=qty,
                         reason=f"Batch {b.batch_no} received", recorded_by_id=user.id))
    try:
        db.commit()
    except IntegrityError:
        # CM-4: the duplicate check above is check-then-act — two
        # concurrent receipts of the same batch_no both pass it, and the
        # loser hits the new uq_stock_batches_item_batch_no constraint.
        # 409 (retryable), not a 500.
        db.rollback()
        raise HTTPException(409, f"Batch {payload.batch_no.strip()} already exists for this item — please retry")
    db.refresh(b)
    _log_audit(db, user, "RECEIVE_BATCH", f"item_id={item_id}")
    return serialize_batch(b)


@router.get("/expiring")
def expiring(days: int = 30, business_unit_id: Optional[int] = None, limit: int = 200,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Batches expiring within `days` (or already expired) with stock left."""
    bu_id = bu_scope(user, business_unit_id)
    cutoff = today_local() + timedelta(days=days)
    q = db.query(StockBatch).filter(StockBatch.quantity > 0,
                                    StockBatch.expiry_date.isnot(None),
                                    StockBatch.expiry_date <= cutoff)
    if bu_id is not None:
        q = q.join(InventoryItem).filter(InventoryItem.business_unit_id == bu_id)
    rows = q.order_by(StockBatch.expiry_date).limit(limit).all()
    out = []
    for b in rows:
        d = serialize_batch(b)
        d["item_name"] = b.item.name if b.item else None
        d["business_unit_id"] = b.item.business_unit_id if b.item else None
        out.append(d)
    return {"items": out, "count": len(out)}


def consume_batches_fefo(db: Session, item: InventoryItem, qty: Decimal) -> None:
    """Deduct `qty` from the item's batches, earliest expiry first (FEFO).

    Raises 400 when the item's batches cannot cover `qty`, and skips
    expired batches entirely (they are never consumed). Items with no
    batches at all are not batch-tracked, so there is nothing to deduct
    and the item-level stock is authoritative for them."""
    qty = Decimal(str(qty))
    if qty <= 0:
        return
    # CC-1: lock the batch rows — two concurrent FEFO consumptions used
    # to read the same batch quantities and both deduct, driving a batch
    # negative.
    batches = db.query(StockBatch).filter(StockBatch.item_id == item.id,
                                          StockBatch.quantity > 0).order_by(
        StockBatch.expiry_date.is_(None), StockBatch.expiry_date, StockBatch.id).with_for_update().all()
    if not batches:
        return
    today = today_local()
    remaining = qty
    expired_qty = Decimal("0")
    for b in batches:
        if remaining <= 0:
            break
        if b.expiry_date is not None and b.expiry_date < today:
            expired_qty += Decimal(str(b.quantity))
            continue
        take = min(Decimal(str(b.quantity)), remaining)
        b.quantity = Decimal(str(b.quantity)) - take
        remaining -= take
    if remaining > 0:
        detail = f"Not enough unexpired batch stock for {item.name}: short by {remaining:g}"
        if expired_qty > 0:
            detail += f" ({expired_qty:g} expired units skipped)"
        raise HTTPException(400, detail)


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
    _log_audit(db, user, "CREATE_PROJECT", f"id={p.id}")
    return serialize_project(db, p)


@router.post("/projects/{project_id}/status")
def set_project_status(project_id: int, status: str, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    p = db.query(Project).filter(Project.id == project_id).with_for_update().first()
    if not p:
        raise HTTPException(404, "Project not found")
    bu_scope(user, p.business_unit_id)
    if status not in ("active", "completed", "cancelled"):
        raise HTTPException(400, "status must be active/completed/cancelled")
    p.status = status
    db.commit()
    _log_audit(db, user, "SET_PROJECT_STATUS", f"project_id={project_id}")
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
    returns_total = ZERO
    inv_count = 0
    for inv in db.query(SalesInvoice).filter(SalesInvoice.project_id == project_id,
                                             SalesInvoice.status.in_(("sent", "partial", "paid"))).all():
        a = invoice_amounts(db, inv)
        fx = Decimal(str(getattr(inv, "exchange_rate", 1) or 1))
        revenue += a["subtotal"] * fx
        # Posted sales returns reduce project revenue, net of tax, in MMK.
        ret_net, _, _ = sales_returns_total(db, inv)
        returns_total += ret_net * fx
        inv_count += 1
    revenue -= returns_total
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
        "revenue": fmt(revenue), "sales_returns": fmt(returns_total),
        "po_costs": fmt(po_cost), "expense_costs": fmt(exp_cost),
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
    freight_charge: float = Field(default=0, ge=0, le=10**13)
    fuel_cost: float = Field(default=0, ge=0, le=10**13)
    toll_fees: float = Field(default=0, ge=0, le=10**13)
    driver_fees: float = Field(default=0, ge=0, le=10**13)
    other_costs: float = Field(default=0, ge=0, le=10**13)


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
    if payload.project_id is not None:
        proj = db.query(Project).filter(Project.id == payload.project_id,
                                        Project.business_unit_id == bu_id).first()
        if not proj:
            raise HTTPException(404, "Project not found in this BU")
    if payload.customer_id is not None:
        cust = db.query(Customer).filter(Customer.id == payload.customer_id,
                                         Customer.business_unit_id == bu_id).first()
        if not cust:
            raise HTTPException(404, "Customer not found in this BU")
    s = Shipment(business_unit_id=bu_id, created_by_id=user.id,
                 **{k: v for k, v in payload.model_dump().items() if k != "business_unit_id"})
    db.add(s)
    db.commit()
    db.refresh(s)
    _log_audit(db, user, "CREATE_SHIPMENT", f"id={s.id}")
    return serialize_shipment(s)


@router.post("/shipments/{shipment_id}/status")
def set_shipment_status(shipment_id: int, status: str, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    s = db.query(Shipment).filter(Shipment.id == shipment_id).with_for_update().first()
    if not s:
        raise HTTPException(404, "Shipment not found")
    bu_scope(user, s.business_unit_id)
    if status not in ("planned", "in_transit", "delivered", "cancelled"):
        raise HTTPException(400, "Invalid shipment status")
    s.status = status
    db.commit()
    _log_audit(db, user, "SET_SHIPMENT_STATUS", f"shipment_id={shipment_id}")
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


class BomComponentIn(BaseModel):
    component_item_id: int
    quantity_per_unit: Decimal = Field(gt=0, le=10**13)


class BomIn(BaseModel):
    business_unit_id: Optional[int] = None
    finished_item_id: int
    name: Optional[str] = None
    components: list[BomComponentIn] = Field(min_length=1)


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
    seen: set[int] = set()
    for c in payload.components:
        if c.component_item_id == fin.id:
            raise HTTPException(400, "A BOM cannot contain its finished item as its own component")
        if c.component_item_id in seen:
            raise HTTPException(400, f"Duplicate component item {c.component_item_id} in this BOM")
        seen.add(c.component_item_id)
    bom = ManufacturingBom(business_unit_id=bu_id, finished_item_id=fin.id, name=payload.name)
    db.add(bom)
    db.flush()
    for c in payload.components:
        comp = db.query(InventoryItem).filter(InventoryItem.id == c.component_item_id,
                                              InventoryItem.business_unit_id == bu_id).first()
        if not comp:
            raise HTTPException(400, "Component item not found in this BU")
        db.add(BomComponent(bom_id=bom.id, component_item_id=comp.id,
                            quantity_per_unit=c.quantity_per_unit))
    db.commit()
    db.refresh(bom)
    _log_audit(db, user, "CREATE_BOM", f"id={bom.id}")
    return serialize_bom(db, bom)


class ProductionIn(BaseModel):
    bom_id: int
    quantity: float = Field(gt=0)
    labour_cost: float = Field(default=0, ge=0, le=10**13)
    overhead_cost: float = Field(default=0, ge=0, le=10**13)
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
                        production_date=payload.production_date or today_local(),
                        created_by_id=user.id)
    db.add(o)
    db.commit()
    db.refresh(o)
    _log_audit(db, user, "CREATE_PRODUCTION", f"id={o.id}")
    return serialize_production(o)


@router.post("/production-orders/{order_id}/complete")
def complete_production(order_id: int, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Consume components (FEFO over batches) and stock the finished goods
    at full production cost. The added labour/overhead value is capitalised
    by reversing the salary expense — Dr 1140 Inventory / Cr 6200 Salaries
    — instead of the old Dr 1140 / Cr 2120 Accrued treatment, whose 2120
    credit was never cleared and double-counted labour already expensed
    through payroll (M-B9)."""
    o = db.query(ProductionOrder).filter(ProductionOrder.id == order_id).with_for_update().first()
    if not o:
        raise HTTPException(404, "Production order not found")
    bu_scope(user, o.business_unit_id)
    if o.status != "planned":
        raise HTTPException(400, f"Cannot complete a {o.status} order")
    bom = o.bom
    qty = Decimal(str(o.quantity))
    # Cm-4: lock every stock row this completion touches — components
    # plus the finished item — in canonical (sorted id) order, before
    # the stock check, so two concurrent completions cannot both pass
    # the check on the same component stock.
    _item_ids = sorted({c.component_item_id for c in bom.components} | {bom.finished_item_id})
    _locked_items = {}
    for _iid in _item_ids:
        _locked_items[_iid] = db.query(InventoryItem).filter(
            InventoryItem.id == _iid).with_for_update().first()
    # Conditional claim (backstop on SQLite, where the FOR UPDATE load
    # above is a no-op): two concurrent completes must not both consume
    # the same components — the loser gets a 409.
    _claimed = db.query(ProductionOrder).filter(
        ProductionOrder.id == order_id, ProductionOrder.status == "planned",
    ).update({"status": "completed"}, synchronize_session=False)
    if _claimed == 0:
        db.refresh(o)
        raise HTTPException(409, f"Production order {order_id} has already been completed or moved (status: {o.status})")
    # check + value components
    materials = ZERO
    needs = []
    for c in bom.components:
        item = _locked_items.get(c.component_item_id) or c.component_item
        need = Decimal(str(c.quantity_per_unit)) * qty
        if Decimal(str(item.current_stock or 0)) < need:
            raise HTTPException(400, f"Not enough stock of {item.name}: need {need}, have {item.current_stock}")
        materials += need * Decimal(str(item.unit_cost or 0))
        needs.append((item, need))
    for item, need in needs:
        if not _stock_sub(db, item, need):
            raise HTTPException(409, f"Stock for {item.name} changed while completing — please retry")
        item.updated_at = utcnow()
        consume_batches_fefo(db, item, need)
        db.add(StockMovement(item_id=item.id, movement_type="out", quantity=need,
                             reason=f"Production order #{o.id}", recorded_by_id=user.id))
    total_cost = materials + Decimal(str(o.labour_cost or 0)) + Decimal(str(o.overhead_cost or 0))
    unit_cost = total_cost / qty if qty else ZERO
    fin = _locked_items.get(bom.finished_item_id) or bom.finished_item
    old_qty = Decimal(str(fin.current_stock or 0))
    old_cost = Decimal(str(fin.unit_cost or 0))
    new_qty = old_qty + qty
    fin.unit_cost = ((old_qty * old_cost) + total_cost) / new_qty if new_qty else unit_cost
    _stock_add(db, fin, qty)
    fin.updated_at = utcnow()
    db.add(StockMovement(item_id=fin.id, movement_type="in", quantity=qty,
                         reason=f"Production order #{o.id} completed", recorded_by_id=user.id))
    o.materials_cost = materials
    o.unit_cost = unit_cost
    o.status = "completed"
    db.flush()
    added_value = Decimal(str(o.labour_cost or 0)) + Decimal(str(o.overhead_cost or 0))
    journal_posted = True
    if added_value > 0:
        try:
            from src.routers.accounting import account_by_code, create_entry
            inv_acc = account_by_code(db, o.business_unit_id, "1140")
            sal_acc = account_by_code(db, o.business_unit_id, "6200")
            if not (inv_acc and sal_acc):
                raise HTTPException(500, "Production accounts (1140/6200) are not configured for this BU")
            create_entry(db, o.business_unit_id, o.production_date or today_local(),
                         f"Production order #{o.id} — labour & overhead capitalised",
                         "production", user.id,
                         [{"account_id": inv_acc.id, "debit": added_value, "credit": 0},
                          {"account_id": sal_acc.id, "debit": 0, "credit": added_value}],
                         source_id=o.id, post=True)
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise server_error("Production journal could not be posted; completion rolled back",
                               exc, logger, context=f"order {o.id}")
    db.commit()
    db.refresh(o)
    out = serialize_production(o)
    out["journal_posted"] = journal_posted
    _log_audit(db, user, "COMPLETE_PRODUCTION", f"order_id={order_id}")
    return out


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
    size_sqft: Optional[float] = Field(default=None, ge=0, le=10**7)
    asking_price: Optional[float] = Field(default=None, ge=0, le=10**13)
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
    _log_audit(db, user, "CREATE_UNIT", f"id={u.id}")
    return serialize_unit(u)


class SellUnitIn(BaseModel):
    customer_id: int
    price: Optional[float] = None
    sale_date: Optional[date] = None
    tax_rate: float = Field(default=0, ge=0, le=100)
    downpayment_pct: float = Field(default=0, ge=0, le=100)
    installments: int = Field(default=0, ge=0, le=360)  # number of monthly installments for the balance
    first_due_date: Optional[date] = None


@router.post("/property-units/{unit_id}/sell")
def sell_unit(unit_id: int, payload: SellUnitIn, user: User = Depends(get_current_user),
              db: Session = Depends(get_db),
              idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    """Sell a unit: creates a sales invoice (draft) and, when requested, a
    monthly installment plan for the balance after the downpayment."""
    u = db.query(PropertyUnit).filter(PropertyUnit.id == unit_id).with_for_update().first()
    if not u:
        raise HTTPException(404, "Unit not found")
    bu_scope(user, u.business_unit_id)
    endpoint = "POST /api/modules/property-units/{unit_id}/sell"
    replay = _idempotency_lookup(db, user, endpoint, idempotency_key)
    if replay is not None:
        return replay
    if u.status not in ("available", "reserved"):
        raise HTTPException(400, f"Unit is {u.status}")
    cust = db.query(Customer).filter(Customer.id == payload.customer_id,
                                     Customer.business_unit_id == u.business_unit_id).first()
    if not cust:
        raise HTTPException(404, "Customer not found in this BU")
    price = Decimal(str(payload.price)) if payload.price else Decimal(str(u.asking_price or 0))
    if price <= 0:
        raise HTTPException(400, "A sale price is required")
    if payload.downpayment_pct > 0 and payload.installments == 0:
        raise HTTPException(400, "A downpayment needs at least one installment to be recorded against")
    # CC-2 conditional claim: on SQLite the FOR UPDATE load above is a
    # no-op, so two concurrent sells of the same unit would both pass
    # the status check and both create invoices — the loser gets a 409
    # here instead of a second sale of the same unit.
    claimed = db.query(PropertyUnit).filter(
        PropertyUnit.id == unit_id, PropertyUnit.status == "available",
    ).update({"status": "reserved"}, synchronize_session=False)
    if claimed == 0:
        db.refresh(u)
        raise HTTPException(409, f"Unit {u.unit_no} has already been sold or reserved (status: {u.status})")
    from src.routers.ops import InvoiceIn, InvoiceLineIn, create_invoice
    inv = create_invoice(InvoiceIn(
        business_unit_id=u.business_unit_id, customer_id=cust.id,
        invoice_date=payload.sale_date or today_local(), tax_rate=payload.tax_rate,
        notes=f"Property sale: {u.development or ''} {u.unit_no}".strip(),
        lines=[InvoiceLineIn(description=f"{u.development or ''} Unit {u.unit_no} ({u.unit_type})".strip(),
                             quantity=1, unit_price=float(price))],
    ), user=user, db=db)
    u.status = "reserved"
    u.sale_invoice_id = inv["id"]
    # Cm-2: compute the whole installment plan in memory and validate
    # the sum against the invoice total BEFORE inserting any rows or
    # committing — the old code inserted + committed the plan, then
    # checked the sum and called db.rollback() on a committed
    # transaction (a no-op), leaving a bad plan in place.
    plan = []
    if payload.installments > 0:
        total = Decimal(str(inv["total"]))
        down = (total * Decimal(str(payload.downpayment_pct)) / Decimal("100")).quantize(Decimal("0.01"))
        balance = total - down
        per = (balance / payload.installments).quantize(Decimal("0.01"))
        first = payload.first_due_date or (payload.sale_date or today_local())
        seq = 0
        if down > 0:
            seq += 1
            plan.append({"seq": seq, "due_date": first, "amount": down})
        for i in range(payload.installments):
            seq += 1
            y = first.year + (first.month - 1 + i + (1 if down > 0 else 0)) // 12
            m = (first.month - 1 + i + (1 if down > 0 else 0)) % 12 + 1
            due = date(y, m, min(first.day, 28))
            amt = per if i < payload.installments - 1 else balance - per * (payload.installments - 1)
            plan.append({"seq": seq, "due_date": due, "amount": amt})
        planned = sum((Decimal(str(p["amount"])) for p in plan), Decimal("0"))
        if planned != total:
            db.rollback()
            raise HTTPException(500, f"Installment plan sums to {planned}, not the invoice total {inv['total']}")
        for p in plan:
            db.add(Installment(invoice_id=inv["id"], seq=p["seq"], due_date=p["due_date"], amount=p["amount"]))
    db.commit()
    out = {"unit": serialize_unit(u), "invoice_id": inv["id"], "invoice_no": inv["invoice_no"],
           "total": inv["total"],
           "installments": [{"seq": p["seq"], "due_date": p["due_date"].isoformat(),
                             "amount": fmt(p["amount"])} for p in plan]}
    _log_audit(db, user, "SELL_UNIT", f"unit_id={unit_id}")
    _idempotency_store(db, user, endpoint, idempotency_key, out)
    return out


@router.get("/invoices/{invoice_id}/installments")
def list_installments(invoice_id: int, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    inv = db.query(SalesInvoice).filter(SalesInvoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    bu_scope(user, inv.business_unit_id)
    rows = db.query(Installment).filter(Installment.invoice_id == invoice_id).order_by(Installment.seq).all()
    today_d = today_local()
    return {"items": [{
        "id": r.id, "seq": r.seq, "due_date": r.due_date.isoformat(),
        "amount": fmt(r.amount), "paid_amount": fmt(r.paid_amount),
        "balance": fmt(Decimal(str(r.amount)) - Decimal(str(r.paid_amount))),
        "overdue": (r.due_date < today_d and Decimal(str(r.paid_amount)) < Decimal(str(r.amount))),
    } for r in rows]}


def allocate_installments(db: Session, invoice_id: int) -> None:
    """Re-allocate what is actually owed across the plan, FIFO. A sales
    return reduces the balance, so the allocatable amount is capped at
    min(paid, total - returns) — installments must never show as paid
    beyond what is now owed."""
    from src.routers.ops import invoice_amounts
    inv = db.query(SalesInvoice).filter(SalesInvoice.id == invoice_id).first()
    if not inv:
        return
    rows = db.query(Installment).filter(Installment.invoice_id == invoice_id).order_by(Installment.seq).all()
    if not rows:
        return
    a = invoice_amounts(db, inv)
    remaining = min(a["paid"], a["total"] - a.get("returns", Decimal("0")))
    if remaining < 0:
        remaining = Decimal("0")
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
    deposit_amount: float = Field(default=0, ge=0, le=10**13)
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
def create_lease(payload: LeaseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                 idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    endpoint = "POST /api/modules/leases"
    replay = _idempotency_lookup(db, user, endpoint, idempotency_key)
    if replay is not None:
        return replay
    unit = db.query(PropertyUnit).filter(PropertyUnit.id == payload.unit_id,
                                         PropertyUnit.business_unit_id == bu_id).with_for_update().first()
    if not unit:
        raise HTTPException(404, "Unit not found in this BU")
    # CM-4: the sequential 400 below is pinned by test_mc16 — keep it.
    # The conditional claim is the concurrent backstop: on SQLite the
    # FOR UPDATE load is a no-op, so two concurrent leases of the same
    # unit would both pass the check — the loser gets a 409 here.
    if unit.status != "available":
        raise HTTPException(400, f"Unit is {unit.status}; only an available unit can be leased")
    tenant = db.query(Customer).filter(Customer.id == payload.tenant_id,
                                       Customer.business_unit_id == bu_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant not found in this BU")
    claimed = db.query(PropertyUnit).filter(
        PropertyUnit.id == unit.id, PropertyUnit.status == "available",
    ).update({"status": "rented"}, synchronize_session=False)
    if claimed == 0:
        db.refresh(unit)
        raise HTTPException(409, f"Unit {unit.unit_no} has already been leased or moved (status: {unit.status})")
    l = LeaseContract(business_unit_id=bu_id, created_by_id=user.id,
                      **{k: v for k, v in payload.model_dump().items() if k != "business_unit_id"})
    db.add(l)
    unit.status = "rented"
    db.commit()
    db.refresh(l)
    out = serialize_lease(l)
    _log_audit(db, user, "CREATE_LEASE", f"id={l.id}")
    _idempotency_store(db, user, endpoint, idempotency_key, out)
    return out


@router.post("/leases/{lease_id}/bill")
def bill_lease(lease_id: int, period: Optional[str] = None, user: User = Depends(get_current_user),
               db: Session = Depends(get_db),
               idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    """Create this month's rent invoice (draft) for an active lease."""
    l = db.query(LeaseContract).filter(LeaseContract.id == lease_id).with_for_update().first()
    if not l:
        raise HTTPException(404, "Lease not found")
    bu_scope(user, l.business_unit_id)
    endpoint = "POST /api/modules/leases/{lease_id}/bill"
    replay = _idempotency_lookup(db, user, endpoint, idempotency_key)
    if replay is not None:
        return replay
    if l.status != "active":
        raise HTTPException(400, "Lease is not active")
    period = validate_period(period or today_local().strftime("%Y-%m"))
    if l.last_billed_period == period:
        raise HTTPException(400, f"Rent already billed for {period}")
    # CM-4 conditional claim on last_billed_period, flushed (executed)
    # BEFORE create_invoice — which commits internally — so a second
    # concurrent bill for the same period cannot slip past the check
    # above on SQLite: the loser gets a 409 instead of a duplicate
    # rent invoice.
    claimed = db.query(LeaseContract).filter(
        LeaseContract.id == lease_id,
        or_(LeaseContract.last_billed_period.is_(None),
            LeaseContract.last_billed_period != period),
    ).update({"last_billed_period": period}, synchronize_session=False)
    if claimed == 0:
        db.refresh(l)
        raise HTTPException(409, f"Rent already billed for {period} (last billed: {l.last_billed_period})")
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
    out = {"invoice_id": inv["id"], "invoice_no": inv["invoice_no"], "period": period}
    _log_audit(db, user, "BILL_LEASE", f"lease_id={lease_id}")
    _idempotency_store(db, user, endpoint, idempotency_key, out)
    return out


# ------------------------------------------------------------------ principal report

@router.get("/principal-report")
def principal_report(business_unit_id: Optional[int] = None, period: Optional[str] = None,
                     limit: int = 500,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sales grouped by principal (brand owner) and brand, from invoice
    lines linked to inventory items. Amounts in MMK."""
    period = validate_period(period)
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(SalesInvoice).filter(SalesInvoice.status.in_(("sent", "partial", "paid")))
    if bu_id is not None:
        q = q.filter(SalesInvoice.business_unit_id == bu_id)
    if period:
        q = q.filter(func.strftime("%Y-%m", SalesInvoice.invoice_date) == period) \
            if db.bind.dialect.name == "sqlite" else \
            q.filter(func.to_char(SalesInvoice.invoice_date, "YYYY-MM") == period)
    groups: dict[str, dict] = {}
    for inv in q.limit(limit).all():
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
    cutoff = today_local() + timedelta(days=days)
    batches = db.query(StockBatch).filter(StockBatch.quantity > 0,
                                          StockBatch.expiry_date.isnot(None),
                                          StockBatch.expiry_date <= cutoff).all()
    notified = 0
    for b in batches:
        if b.expiry_notified_at and (utcnow() - b.expiry_notified_at).days < 7:
            continue
        item = b.item
        if not item:
            continue
        recipients = db.query(User).filter(
            User.is_active == True,  # noqa: E712
            ((User.role == "CEO") & (User.business_unit_id == item.business_unit_id))
            | (User.role.in_(("Chairman", "HO")))).all()
        days_left = (b.expiry_date - today_local()).days
        for u in recipients:
            db.add(Notification(
                user_id=u.id, type="batch_expiry",
                title="Stock batch expiring" if days_left >= 0 else "Stock batch EXPIRED",
                message=f"{item.name} — batch {b.batch_no} expires {b.expiry_date.isoformat()} "
                        f"({days_left} days), {float(b.quantity)} left",
                link="/admin/operations-center"))
            notified += 1
        b.expiry_notified_at = utcnow()
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
    t = db.query(StockTransfer).filter(StockTransfer.id == transfer_id).with_for_update().first()
    if not t:
        raise HTTPException(404, "Stock transfer not found")
    return t


def _post_transfer_journal(db: Session, bu_id: int, lines_spec, user_id: int,
                           transfer_id: int, description: str) -> bool:
    """Best-effort journal in one BU's books. Returns True when posted;
    failures are logged with the transfer id and surfaced to the caller as
    journal_posted=false (M-B1) rather than silently dropped."""
    try:
        from src.routers.accounting import create_entry
        create_entry(db, bu_id, today_local(), description, "stock_transfer",
                     user_id, lines_spec, source_id=transfer_id, post=True)
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("Transfer journal failed for transfer %s (BU %s): %s",
                       transfer_id, bu_id, exc)
        return False


@router.get("/stock-transfers")
def list_stock_transfers(from_business_unit_id: Optional[int] = None,
                         to_business_unit_id: Optional[int] = None,
                         status: Optional[str] = None,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from src.models import StockTransfer
    q = db.query(StockTransfer)
    if user.role in ("CEO", "Accountant") and user.business_unit_id:
        # A scoped user sees transfers in either direction for their BU —
        # outgoing and incoming — not just outgoing ones.
        bu = user.business_unit_id
        q = q.filter(or_(StockTransfer.from_business_unit_id == bu,
                         StockTransfer.to_business_unit_id == bu))
    else:
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
    try:
        db.flush()
    except IntegrityError:
        # M-D5: the per-BU unique constraint on transfer_no fired — a
        # concurrent create took this count+1 number first. 409, not a 500.
        db.rollback()
        raise HTTPException(409, f"Stock transfer number {t.transfer_no} already exists — please retry")
    for ln in payload.lines:
        db.add(StockTransferLine(transfer_id=t.id, inventory_item_id=ln.inventory_item_id,
                                 quantity=Decimal(str(ln.quantity))))
    db.commit()
    db.refresh(t)
    _log_audit(db, user, "CREATE_STOCK_TRANSFER", f"id={t.id}")
    return serialize_transfer(db, t)


@router.post("/stock-transfers/{transfer_id}/send")
def send_stock_transfer(transfer_id: int, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db),
                        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    """Stock out of the source BU at moving-average cost; the source BU's
    books post Dr 1180 Intercompany Receivable / Cr 1140 Inventory."""
    from src.models import StockMovement as SM
    t = _transfer_or_404(db, transfer_id)
    bu_scope(user, t.from_business_unit_id)
    endpoint = "POST /api/modules/stock-transfers/{transfer_id}/send"
    replay = _idempotency_lookup(db, user, endpoint, idempotency_key)
    if replay is not None:
        return replay
    if t.status != "draft":
        raise HTTPException(400, f"Cannot send a transfer in status {t.status}")
    # Conditional claim (backstop on SQLite, where the FOR UPDATE load
    # in _transfer_or_404 is a no-op): two concurrent sends must not
    # both draw the same stock — the loser gets a 409.
    from src.models import StockTransfer as _ST
    _claimed = db.query(_ST).filter(
        _ST.id == transfer_id, _ST.status == "draft",
    ).update({"status": "in_transit"}, synchronize_session=False)
    if _claimed == 0:
        db.refresh(t)
        raise HTTPException(409, f"Stock transfer {t.transfer_no} has already been sent or moved (status: {t.status})")
    # Aggregate duplicate lines per item before the stock check, so two
    # lines for the same item cannot jointly overdraw the stock.
    from collections import defaultdict
    wanted: dict[int, Decimal] = defaultdict(Decimal)
    for ln in t.lines:
        wanted[ln.inventory_item_id] += Decimal(str(ln.quantity))
    items = {}
    # Cm-4: lock item rows in canonical (sorted id) order.
    for item_id, qty in sorted(wanted.items()):
        item = db.query(InventoryItem).filter(InventoryItem.id == item_id).with_for_update().first()
        if item is None or Decimal(str(item.current_stock or 0)) < qty:
            raise HTTPException(400, f"Not enough stock of {item.name if item else item_id} in the source BU")
        items[item_id] = item
    total_cost = Decimal("0")
    for ln in t.lines:
        item = items[ln.inventory_item_id]
        qty = Decimal(str(ln.quantity))
        cost = Decimal(str(item.unit_cost or 0))
        ln.unit_cost = cost
        total_cost += qty * cost
        if not _stock_sub(db, item, qty):
            raise HTTPException(409, f"Stock for {item.name} changed while sending — please retry")
        item.updated_at = utcnow()
        db.add(SM(item_id=item.id, movement_type="out", quantity=qty,
                  reason=f"Stock transfer {t.transfer_no} to BU {t.to_business_unit_id}",
                  recorded_by_id=user.id))
    for item_id, qty in sorted(wanted.items()):
        consume_batches_fefo(db, items[item_id], qty)
    t.status = "in_transit"
    t.sent_at = utcnow()
    # SM-9 fail-hard: the source journal posts in this same transaction,
    # before the single commit — a journal failure rolls the whole send
    # back (500) instead of committing stock-out with journal_posted=false.
    journal_posted = None
    if total_cost > 0:
        try:
            from src.routers.accounting import _ensure_intercompany_accounts, account_by_code, create_entry
            _ensure_intercompany_accounts(db, t.from_business_unit_id)
            recv = account_by_code(db, t.from_business_unit_id, "1180")
            inv_acc = account_by_code(db, t.from_business_unit_id, "1140")
            if not (recv and inv_acc):
                raise HTTPException(500, "Intercompany accounts (1180/1140) are not configured for this BU")
            create_entry(db, t.from_business_unit_id, today_local(),
                         f"Stock transfer {t.transfer_no} sent to {t.to_business_unit.name if t.to_business_unit else ''}",
                         "stock_transfer", user.id,
                         [{"account_id": recv.id, "debit": total_cost, "credit": 0},
                          {"account_id": inv_acc.id, "debit": 0, "credit": total_cost}],
                         source_id=t.id, post=True)
            journal_posted = True
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise server_error("Transfer journal could not be posted; transfer left as draft",
                               exc, logger, context=f"transfer {t.transfer_no}")
    _commit_or_409(db, f"Stock transfer {t.transfer_no} send")
    db.refresh(t)
    out = serialize_transfer(db, t)
    out["journal_posted"] = journal_posted
    _log_audit(db, user, "SEND_STOCK_TRANSFER", f"transfer_id={transfer_id}")
    _idempotency_store(db, user, endpoint, idempotency_key, out)
    return out


@router.post("/stock-transfers/{transfer_id}/receive")
def receive_stock_transfer(transfer_id: int, user: User = Depends(get_current_user),
                           db: Session = Depends(get_db),
                           idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    """Stock into the destination BU at the transfer cost (moving average
    updated); destination books post Dr 1140 / Cr 2180."""
    from src.models import StockMovement as SM
    t = _transfer_or_404(db, transfer_id)
    # SM-15: only the DESTINATION BU (or Chairman/HO) may receive a
    # transfer — the source BU receiving its own in-transit stock back
    # used to be allowed, which let a source-BU user silently complete
    # a transfer the destination never saw.
    if user.role in ("CEO", "Accountant") and user.business_unit_id \
            and user.business_unit_id != t.to_business_unit_id:
        raise HTTPException(403, "Only the destination business unit (or Chairman/HO) can receive this transfer")
    endpoint = "POST /api/modules/stock-transfers/{transfer_id}/receive"
    replay = _idempotency_lookup(db, user, endpoint, idempotency_key)
    if replay is not None:
        return replay
    if t.status != "in_transit":
        raise HTTPException(400, f"Cannot receive a transfer in status {t.status}")
    # Conditional claim (backstop on SQLite): two concurrent receives
    # must not both stock the same goods in — the loser gets a 409.
    from src.models import StockTransfer as _ST
    _claimed = db.query(_ST).filter(
        _ST.id == transfer_id, _ST.status == "in_transit",
    ).update({"status": "received"}, synchronize_session=False)
    if _claimed == 0:
        db.refresh(t)
        raise HTTPException(409, f"Stock transfer {t.transfer_no} has already been received or moved (status: {t.status})")
    total_cost = Decimal("0")
    # Cm-4: process lines in canonical (source item name) order so two
    # concurrent receives touching the same destination items lock them
    # in the same order.
    for ln in sorted(t.lines, key=lambda l: (l.inventory_item.name or "")):
        src_item = ln.inventory_item
        qty = Decimal(str(ln.quantity))
        cost = Decimal(str(ln.unit_cost or 0))
        total_cost += qty * cost
        dst = db.query(InventoryItem).filter(
            InventoryItem.business_unit_id == t.to_business_unit_id,
            InventoryItem.name == src_item.name, InventoryItem.is_active == True).with_for_update().first()  # noqa: E712
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
        _stock_add(db, dst, qty)
        dst.updated_at = utcnow()
        ln.dest_inventory_item_id = dst.id
        db.add(SM(item_id=dst.id, movement_type="in", quantity=qty,
                  reason=f"Stock transfer {t.transfer_no} from BU {t.from_business_unit_id}",
                  recorded_by_id=user.id))
    t.status = "received"
    t.received_at = utcnow()
    # SM-9 fail-hard: the destination journal posts in this same
    # transaction, before the single commit — a journal failure rolls
    # the whole receipt back (500) instead of committing stock-in with
    # journal_posted=false.
    journal_posted = None
    if total_cost > 0:
        try:
            from src.routers.accounting import _ensure_intercompany_accounts, account_by_code, create_entry
            _ensure_intercompany_accounts(db, t.to_business_unit_id)
            inv_acc = account_by_code(db, t.to_business_unit_id, "1140")
            pay = account_by_code(db, t.to_business_unit_id, "2180")
            if not (inv_acc and pay):
                raise HTTPException(500, "Intercompany accounts (1140/2180) are not configured for this BU")
            create_entry(db, t.to_business_unit_id, today_local(),
                         f"Stock transfer {t.transfer_no} received from {t.from_business_unit.name if t.from_business_unit else ''}",
                         "stock_transfer", user.id,
                         [{"account_id": inv_acc.id, "debit": total_cost, "credit": 0},
                          {"account_id": pay.id, "debit": 0, "credit": total_cost}],
                         source_id=t.id, post=True)
            journal_posted = True
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise server_error("Transfer journal could not be posted; receipt rolled back",
                               exc, logger, context=f"transfer {t.transfer_no}")
    _commit_or_409(db, f"Stock transfer {t.transfer_no} receive")
    db.refresh(t)
    out = serialize_transfer(db, t)
    out["journal_posted"] = journal_posted
    _log_audit(db, user, "RECEIVE_STOCK_TRANSFER", f"transfer_id={transfer_id}")
    _idempotency_store(db, user, endpoint, idempotency_key, out)
    return out


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
        # Conditional claim — a concurrent send racing this cancel must
        # not both succeed; the loser gets a 409.
        from src.models import StockTransfer as _ST
        _claimed = db.query(_ST).filter(
            _ST.id == transfer_id, _ST.status == "draft",
        ).update({"status": "cancelled"}, synchronize_session=False)
        if _claimed == 0:
            db.refresh(t)
            raise HTTPException(409, f"Stock transfer {t.transfer_no} has already been sent or moved (status: {t.status})")
        t.status = "cancelled"
        db.commit()
        db.refresh(t)
        _log_audit(db, user, "CANCEL_STOCK_TRANSFER", f"transfer_id={transfer_id}")
        return serialize_transfer(db, t)
    if t.status != "in_transit":
        raise HTTPException(400, f"Cannot cancel a transfer in status {t.status}")
    from src.models import StockTransfer as _ST
    _claimed = db.query(_ST).filter(
        _ST.id == transfer_id, _ST.status == "in_transit",
    ).update({"status": "cancelled"}, synchronize_session=False)
    if _claimed == 0:
        db.refresh(t)
        raise HTTPException(409, f"Stock transfer {t.transfer_no} has already been received or moved (status: {t.status})")
    # CC-1: lock the restocked source items in canonical (sorted id) order.
    _item_ids = sorted({ln.inventory_item_id for ln in t.lines})
    _items = {}
    for _iid in _item_ids:
        _items[_iid] = db.query(InventoryItem).filter(
            InventoryItem.id == _iid).with_for_update().first()
    for ln in t.lines:
        item = _items.get(ln.inventory_item_id) or ln.inventory_item
        qty = Decimal(str(ln.quantity))
        _stock_add(db, item, qty)
        item.updated_at = utcnow()
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
    _commit_or_409(db, f"Stock transfer {t.transfer_no} cancel")
    db.refresh(t)
    _log_audit(db, user, "CANCEL_STOCK_TRANSFER", f"transfer_id={transfer_id}")
    return serialize_transfer(db, t)
