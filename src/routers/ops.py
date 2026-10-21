"""Operational extensions: action items, notifications, inventory, expense
approvals, staff records, customer metrics, and forecasting.

Access model mirrors the rest of the app:
- CEO: scoped to their own business unit
- Chairman / HO ("admin"): all business units
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.auth import get_current_user, require_roles
from src.database import get_db
from src.utils import validate_period
from src.models import (
    ActionItem,
    AttendanceRecord,
    BusinessUnit,
    Customer,
    CustomerMetric,
    DailyFinancial,
    Employee,
    ExpenseRequest,
    InventoryItem,
    InvoicePayment,
    ItemSerial,
    JournalEntry,
    LeaveRequest,
    LicenseDocument,
    Notification,
    PayrollLine,
    PayrollRun,
    PosSale,
    PosSaleLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReturn,
    PurchaseReturnLine,
    Quotation,
    QuotationLine,
    SalesInvoice,
    SalesInvoiceLine,
    SalesReturn,
    SalesReturnLine,
    StaffAdvance,
    StaffRecord,
    StockMovement,
    Supplier,
    User,
)

router = APIRouter(prefix="/api/ops", tags=["ops"])
require_admin = require_roles("Chairman", "HO")


# ---------------------------------------------------------------- helpers

def bu_scope(user: User, bu_id: Optional[int]) -> Optional[int]:
    """Resolve which BU the caller may touch. CEOs are pinned to their own."""
    if user.role == "CEO":
        if bu_id is not None and bu_id != user.business_unit_id:
            raise HTTPException(403, "CEOs can only access their own business unit")
        return user.business_unit_id
    return bu_id


def _serial_list(raw) -> list:
    """Serial numbers are stored as a JSON list in a Text column."""
    if not raw:
        return []
    try:
        import json
        val = json.loads(raw)
        return [str(s) for s in val] if isinstance(val, list) else []
    except Exception:
        return []


def _mark_line_serials_sold(db: Session, bu_id: int, lines, invoice_id=None, pos_sale_id=None) -> None:
    """Validate + mark serials sold for invoice/POS lines carrying serial_nos.

    Raises 400 when a serial is unknown for the line's item or not in
    stock. Lines without an inventory item or without serials are skipped.
    """
    for ln in lines:
        serials = _serial_list(getattr(ln, "serial_nos", None))
        if not serials:
            continue
        item_id = getattr(ln, "inventory_item_id", None)
        if not item_id:
            raise HTTPException(400, "Serial numbers need a stock item on the line")
        for sn in serials:
            row = db.query(ItemSerial).filter(
                ItemSerial.business_unit_id == bu_id,
                ItemSerial.inventory_item_id == item_id,
                ItemSerial.serial_no == sn).first()
            if not row:
                raise HTTPException(400, f"Serial {sn} is not registered for this item")
            if row.status != "in_stock":
                raise HTTPException(400, f"Serial {sn} is not in stock (status: {row.status})")
            row.status = "sold"
            row.sales_invoice_id = invoice_id
            row.pos_sale_id = pos_sale_id
            row.sold_at = datetime.utcnow()


def restock_batches(db: Session, item: InventoryItem, qty: Decimal, batch_no: str = "RETURN") -> None:
    """Put `qty` back into an item's batches (voids / transfer cancels).

    FEFO consumption doesn't record which batch a sale took from, so the
    quantity goes back onto the earliest-expiring batch (or a new batch
    when the item has none).
    """
    from src.models import StockBatch
    batch = (db.query(StockBatch).filter(StockBatch.item_id == item.id)
             .order_by(StockBatch.expiry_date.is_(None), StockBatch.expiry_date,
                       StockBatch.id).first())
    if batch:
        batch.quantity = Decimal(str(batch.quantity or 0)) + qty
    else:
        db.add(StockBatch(item_id=item.id, batch_no=batch_no, quantity=qty,
                          unit_cost=item.unit_cost))


def serialize_action_item(db: Session, item: ActionItem) -> dict:
    assignee = db.query(User).filter(User.id == item.assigned_to_id).first() if item.assigned_to_id else None
    return {
        "id": item.id,
        "business_unit_id": item.business_unit_id,
        "title": item.title,
        "description": item.description,
        "assigned_to_id": item.assigned_to_id,
        "assigned_to_name": assignee.full_name or assignee.username if assignee else None,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "status": item.status,
        "created_by_id": item.created_by_id,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


# ---------------------------------------------------------------- action items

class ActionItemIn(BaseModel):
    business_unit_id: Optional[int] = None
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    assigned_to_id: Optional[int] = None
    due_date: Optional[date] = None


@router.get("/action-items")
def list_action_items(
    business_unit_id: Optional[int] = None,
    status: Optional[str] = None,
    mine: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(ActionItem)
    if bu_id is not None:
        q = q.filter(ActionItem.business_unit_id == bu_id)
    if status:
        q = q.filter(ActionItem.status == status)
    if mine:
        q = q.filter(ActionItem.assigned_to_id == user.id)
    items = q.order_by(ActionItem.due_date.is_(None), ActionItem.due_date, ActionItem.created_at.desc()).all()
    return {"items": [serialize_action_item(db, i) for i in items]}


@router.post("/action-items")
def create_action_item(payload: ActionItemIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    item = ActionItem(
        business_unit_id=bu_id,
        title=payload.title.strip(),
        description=payload.description,
        assigned_to_id=payload.assigned_to_id,
        due_date=payload.due_date,
        status="pending",
        created_by_id=user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    if item.assigned_to_id and item.assigned_to_id != user.id:
        db.add(Notification(
            user_id=item.assigned_to_id, type="action_assigned",
            title="New action item assigned",
            message=item.title, link="/admin",
        ))
        db.commit()
    return serialize_action_item(db, item)


class ActionItemUpdate(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to_id: Optional[int] = None
    due_date: Optional[date] = None


@router.patch("/action-items/{item_id}")
def update_action_item(item_id: int, payload: ActionItemUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Action item not found")
    bu_scope(user, item.business_unit_id)
    if payload.status is not None:
        if payload.status not in ("pending", "in_progress", "done"):
            raise HTTPException(400, "Invalid status")
        item.status = payload.status
        item.completed_at = datetime.utcnow() if payload.status == "done" else None
    if payload.title is not None:
        item.title = payload.title.strip()
    if payload.description is not None:
        item.description = payload.description
    if payload.assigned_to_id is not None:
        item.assigned_to_id = payload.assigned_to_id
    if payload.due_date is not None:
        item.due_date = payload.due_date
    db.commit()
    db.refresh(item)
    return serialize_action_item(db, item)


@router.delete("/action-items/{item_id}")
def delete_action_item(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Action item not found")
    bu_scope(user, item.business_unit_id)
    db.delete(item)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- notifications

@router.get("/notifications")
def list_notifications(unread_only: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    rows = q.order_by(Notification.created_at.desc()).limit(100).all()
    unread = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).count()  # noqa: E712
    return {
        "unread_count": unread,
        "items": [
            {
                "id": n.id, "type": n.type, "title": n.title, "message": n.message,
                "link": n.link, "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
    }


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "Notification not found")
    n.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).update({"is_read": True})  # noqa: E712
    db.commit()
    return {"ok": True}


@router.post("/notifications/remind-missing")
def remind_missing(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Notify CEOs whose BU has not submitted today's DailyFinancial."""
    return {"notified": run_remind_missing_reports(db)}


def run_remind_missing_reports(db: Session) -> int:
    """Job-callable: notify CEOs whose BU hasn't submitted today."""
    today = date.today()
    submitted_bu_ids = {
        row[0] for row in db.query(DailyFinancial.business_unit_id).filter(DailyFinancial.report_date == today).all()
    }
    ceos = db.query(User).filter(User.role == "CEO", User.is_active == True).all()  # noqa: E712
    sent = 0
    for ceo in ceos:
        if ceo.business_unit_id and ceo.business_unit_id not in submitted_bu_ids:
            db.add(Notification(
                user_id=ceo.id, type="missing_report",
                title="Report not submitted today",
                message="Please submit today's business performance report.",
                link="/ceo",
            ))
            sent += 1
    db.commit()
    return sent


# ---------------------------------------------------------------- inventory

class InventoryItemIn(BaseModel):
    business_unit_id: Optional[int] = None
    name: str = Field(min_length=1, max_length=150)
    sku: Optional[str] = None
    category: Optional[str] = None
    unit: str = "pcs"
    reorder_level: float = 0
    unit_cost: Optional[float] = None
    initial_stock: float = 0
    brand: Optional[str] = None
    principal_id: Optional[int] = None
    requires_expiry: bool = False


def serialize_inventory_item(item: InventoryItem) -> dict:
    stock = float(item.current_stock or 0)
    reorder = float(item.reorder_level or 0)
    return {
        "id": item.id,
        "business_unit_id": item.business_unit_id,
        "name": item.name,
        "sku": item.sku,
        "category": item.category,
        "unit": item.unit,
        "current_stock": stock,
        "reorder_level": reorder,
        "is_low": stock <= reorder,
        "unit_cost": float(item.unit_cost) if item.unit_cost is not None else None,
        "brand": getattr(item, "brand", None),
        "principal_id": getattr(item, "principal_id", None),
        "requires_expiry": bool(getattr(item, "requires_expiry", False)),
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("/inventory")
def list_inventory(business_unit_id: Optional[int] = None, low_only: bool = False,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(InventoryItem).filter(InventoryItem.is_active == True)  # noqa: E712
    if bu_id is not None:
        q = q.filter(InventoryItem.business_unit_id == bu_id)
    items = [serialize_inventory_item(i) for i in q.order_by(InventoryItem.name).all()]
    if low_only:
        items = [i for i in items if i["is_low"]]
    return {"items": items, "low_count": sum(1 for i in items if i["is_low"])}


@router.post("/inventory")
def create_inventory_item(payload: InventoryItemIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    item = InventoryItem(
        business_unit_id=bu_id,
        name=payload.name.strip(),
        sku=payload.sku,
        category=payload.category,
        unit=payload.unit,
        current_stock=Decimal(str(payload.initial_stock)),
        reorder_level=Decimal(str(payload.reorder_level)),
        unit_cost=Decimal(str(payload.unit_cost)) if payload.unit_cost is not None else None,
        brand=payload.brand, principal_id=payload.principal_id,
        requires_expiry=payload.requires_expiry,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return serialize_inventory_item(item)


class MovementIn(BaseModel):
    movement_type: str  # in/out/adjustment
    quantity: float
    reason: Optional[str] = None
    # ERP Phase 3: for "out" movements that are sales, post COGS to the
    # journal at the item's moving-average cost (Dr 5100 / Cr 1140).
    post_cogs: bool = False


@router.post("/inventory/{item_id}/movements")
def record_movement(item_id: int, payload: MovementIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Inventory item not found")
    bu_scope(user, item.business_unit_id)
    qty = Decimal(str(payload.quantity))
    if payload.movement_type == "in":
        item.current_stock = (item.current_stock or 0) + qty
    elif payload.movement_type == "out":
        item.current_stock = (item.current_stock or 0) - qty
        try:
            from src.routers.modules import consume_batches_fefo
            consume_batches_fefo(db, item, qty)
        except Exception:
            pass  # batch tracking must never break a stock movement
    elif payload.movement_type == "adjustment":
        item.current_stock = qty
    else:
        raise HTTPException(400, "movement_type must be in/out/adjustment")
    item.updated_at = datetime.utcnow()
    db.add(StockMovement(item_id=item.id, movement_type=payload.movement_type,
                         quantity=qty, reason=payload.reason, recorded_by_id=user.id))
    _maybe_notify_low_stock(db, item)
    db.commit()
    db.refresh(item)
    if payload.post_cogs and payload.movement_type == "out" and (item.unit_cost or 0) > 0:
        try:
            from src.routers.accounting import account_by_code, create_entry
            inv = account_by_code(db, item.business_unit_id, "1140")
            cogs = account_by_code(db, item.business_unit_id, "5100")
            if inv and cogs:
                amount = qty * Decimal(str(item.unit_cost))
                create_entry(db, item.business_unit_id, date.today(),
                             f"COGS — {item.name} x{qty}", "inventory", user.id,
                             [{"account_id": cogs.id, "debit": amount, "credit": 0},
                              {"account_id": inv.id, "debit": 0, "credit": amount}],
                             source_id=item.id, post=True)
                db.commit()
        except Exception:
            db.rollback()
    return serialize_inventory_item(item)


class TransferIn(BaseModel):
    to_business_unit_id: int
    quantity: float


@router.post("/inventory/{item_id}/transfer")
def transfer_stock(item_id: int, payload: TransferIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Move stock to another BU (matched by item name; created there if new)."""
    src = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not src:
        raise HTTPException(404, "Inventory item not found")
    bu_scope(user, src.business_unit_id)
    if payload.to_business_unit_id == src.business_unit_id:
        raise HTTPException(400, "Source and destination are the same BU")
    if not db.query(BusinessUnit).filter(BusinessUnit.id == payload.to_business_unit_id).first():
        raise HTTPException(404, "Destination business unit not found")
    qty = Decimal(str(payload.quantity))
    if qty <= 0:
        raise HTTPException(400, "Quantity must be positive")
    if (src.current_stock or 0) < qty:
        raise HTTPException(400, "Not enough stock to transfer")
    dst = db.query(InventoryItem).filter(
        InventoryItem.business_unit_id == payload.to_business_unit_id,
        InventoryItem.name == src.name, InventoryItem.is_active == True).first()  # noqa: E712
    if not dst:
        dst = InventoryItem(business_unit_id=payload.to_business_unit_id, name=src.name,
                            sku=src.sku, category=src.category, unit=src.unit,
                            current_stock=0, reorder_level=src.reorder_level,
                            unit_cost=src.unit_cost)
        db.add(dst)
        db.flush()
    src.current_stock = (src.current_stock or 0) - qty
    dst.current_stock = (dst.current_stock or 0) + qty
    src.updated_at = dst.updated_at = datetime.utcnow()
    db.add(StockMovement(item_id=src.id, movement_type="out", quantity=qty,
                         reason=f"Transfer to BU {payload.to_business_unit_id}", recorded_by_id=user.id))
    db.add(StockMovement(item_id=dst.id, movement_type="in", quantity=qty,
                         reason=f"Transfer from BU {src.business_unit_id}", recorded_by_id=user.id))
    db.commit()
    db.refresh(dst)
    return {"ok": True, "destination_item": serialize_inventory_item(dst),
            "source_stock": float(src.current_stock)}


def _maybe_notify_low_stock(db: Session, item: InventoryItem) -> None:
    """Alert the BU's CEO + all Chairman/HO when stock hits the reorder level.

    Throttled: at most one alert per item per 24h so repeated movements while
    low don't spam the notification center.
    """
    stock = float(item.current_stock or 0)
    reorder = float(item.reorder_level or 0)
    if reorder <= 0 or stock > reorder:
        return
    if item.low_notified_at and item.low_notified_at > datetime.utcnow() - timedelta(hours=24):
        return
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == item.business_unit_id).first()
    bu_name = bu.name if bu else f"BU {item.business_unit_id}"
    title = f"Low stock: {item.name} ({bu_name})"
    message = f"Stock {stock:g} {item.unit or ''} at or below reorder level {reorder:g}."
    admins = db.query(User).filter(User.role.in_(["Chairman", "HO"])).all()
    ceos = db.query(User).filter(User.role == "CEO", User.business_unit_id == item.business_unit_id).all()
    seen = set()
    for u in admins + ceos:
        if u.id in seen:
            continue
        seen.add(u.id)
        db.add(Notification(user_id=u.id, type="low_stock",
                            title=title, message=message,
                            link="/admin/operations-center"))
    item.low_notified_at = datetime.utcnow()


@router.get("/inventory/{item_id}/movements")
def list_movements(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Inventory item not found")
    bu_scope(user, item.business_unit_id)
    rows = db.query(StockMovement).filter(StockMovement.item_id == item_id).order_by(StockMovement.created_at.desc()).limit(100).all()
    return {"items": [
        {"id": m.id, "movement_type": m.movement_type, "quantity": float(m.quantity),
         "reason": m.reason, "created_at": m.created_at.isoformat() if m.created_at else None}
        for m in rows
    ]}


@router.delete("/inventory/{item_id}")
def deactivate_inventory_item(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Inventory item not found")
    bu_scope(user, item.business_unit_id)
    item.is_active = False
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- expenses

class ExpenseIn(BaseModel):
    business_unit_id: Optional[int] = None
    title: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0)
    category: Optional[str] = None
    description: Optional[str] = None
    receipt_url: Optional[str] = None
    project_id: Optional[int] = None


def serialize_expense(db: Session, e: ExpenseRequest) -> dict:
    submitter = db.query(User).filter(User.id == e.submitted_by_id).first()
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == e.business_unit_id).first()
    return {
        "id": e.id,
        "business_unit_id": e.business_unit_id,
        "bu_name": bu.name if bu else None,
        "submitted_by_id": e.submitted_by_id,
        "submitted_by_name": (submitter.full_name or submitter.username) if submitter else None,
        "title": e.title,
        "amount": float(e.amount),
        "category": e.category,
        "description": e.description,
        "receipt_url": e.receipt_url,
        "status": e.status,
        "approval_stage": getattr(e, "approval_stage", "final") or "final",
        "stage_label": ("Waiting HO review" if (getattr(e, "approval_stage", "final") == "first" and e.status == "pending")
                        else "Waiting final approval" if e.status == "pending" else None),
        "review_notes": e.review_notes,
        "reviewed_at": e.reviewed_at.isoformat() if e.reviewed_at else None,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/expenses")
def list_expenses(business_unit_id: Optional[int] = None, status: Optional[str] = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(ExpenseRequest)
    if bu_id is not None:
        q = q.filter(ExpenseRequest.business_unit_id == bu_id)
    if user.role == "CEO":
        q = q.filter(ExpenseRequest.submitted_by_id == user.id)
    if status:
        q = q.filter(ExpenseRequest.status == status)
    rows = q.order_by(ExpenseRequest.created_at.desc()).limit(200).all()
    return {"items": [serialize_expense(db, e) for e in rows]}


# Expenses above this amount (Ks) go through two-stage approval:
# HO reviews first, then the Chairman gives final approval.
TWO_STAGE_THRESHOLD = Decimal("1000000")


@router.post("/expenses")
def submit_expense(payload: ExpenseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    amount = Decimal(str(payload.amount))
    stage = "first" if amount > TWO_STAGE_THRESHOLD else "final"
    e = ExpenseRequest(
        business_unit_id=bu_id,
        submitted_by_id=user.id,
        title=payload.title.strip(),
        amount=amount,
        category=payload.category,
        description=payload.description,
        receipt_url=payload.receipt_url,
        status="pending",
        approval_stage=stage,
        project_id=payload.project_id,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    # Notify the right approvers for this stage
    roles = ["HO"] if stage == "first" else ["Chairman", "HO"]
    for approver in db.query(User).filter(User.role.in_(roles), User.is_active == True).all():  # noqa: E712
        db.add(Notification(
            user_id=approver.id, type="expense_pending",
            title="Expense awaiting approval" if stage == "final" else "Expense awaiting HO review",
            message=f"{e.title} — Ks {float(e.amount):,.0f}", link="/admin/expenses",
        ))
    db.commit()
    return serialize_expense(db, e)


class ExpenseReviewIn(BaseModel):
    decision: str  # approved/rejected
    notes: Optional[str] = None


@router.post("/expenses/{expense_id}/review")
def review_expense(expense_id: int, payload: ExpenseReviewIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    e = db.query(ExpenseRequest).filter(ExpenseRequest.id == expense_id).first()
    if not e:
        raise HTTPException(404, "Expense not found")
    if e.status != "pending":
        raise HTTPException(400, "Expense already reviewed")
    if payload.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved/rejected")
    stage = getattr(e, "approval_stage", "final") or "final"
    if stage == "first":
        # Stage 1 of two-stage approval: HO only.
        if user.role != "HO":
            raise HTTPException(403, "Large expenses need HO review first")
        if payload.decision == "approved":
            e.approval_stage = "final"
            e.review_notes = payload.notes
            for chairman in db.query(User).filter(User.role == "Chairman", User.is_active == True).all():  # noqa: E712
                db.add(Notification(
                    user_id=chairman.id, type="expense_pending",
                    title="Expense awaiting final approval",
                    message=f"{e.title} — Ks {float(e.amount):,.0f} (HO approved)", link="/admin/expenses",
                ))
            db.commit()
            return serialize_expense(db, e)
        # rejected at stage 1 falls through to the normal rejection below
    e.status = payload.decision
    e.reviewed_by_id = user.id
    e.review_notes = payload.notes
    e.reviewed_at = datetime.utcnow()
    db.add(Notification(
        user_id=e.submitted_by_id, type="expense_decision",
        title=f"Expense {payload.decision}",
        message=f"{e.title} — Ks {float(e.amount):,.0f}", link="/ceo",
    ))
    db.commit()
    if payload.decision == "approved":
        try:
            from src.routers.accounting import auto_post_expense
            auto_post_expense(db, e, user.id)
        except Exception:
            db.rollback()  # accounting must never break the approval flow
    return serialize_expense(db, e)


@router.post("/expenses/{expense_id}/receipt")
async def upload_expense_receipt(expense_id: int, file: UploadFile = File(...),
                                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Attach a receipt photo/file to an expense (R2 when configured)."""
    from src.r2_storage import upload_fileobj
    from src.upload_validation import content_matches_extension

    e = db.query(ExpenseRequest).filter(ExpenseRequest.id == expense_id).first()
    if not e:
        raise HTTPException(404, "Expense not found")
    if user.role == "CEO" and e.submitted_by_id != user.id:
        raise HTTPException(403, "Not your expense")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Receipt file is too large (max 10 MB)")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "pdf"):
        raise HTTPException(400, "Receipt must be an image or PDF")
    from io import BytesIO
    bio = BytesIO(data)
    if not content_matches_extension(ext, bio):
        raise HTTPException(400, "File content does not match its extension")
    bio.seek(0)
    url = upload_fileobj(bio, file.filename, folder="expense-receipts")
    if not url:
        raise HTTPException(503, "File storage is not available")
    e.receipt_url = url
    db.commit()
    return serialize_expense(db, e)


# ---------------------------------------------------------------- staff

class StaffIn(BaseModel):
    business_unit_id: Optional[int] = None
    report_date: date
    total_staff: int = 0
    present: int = 0
    absent: int = 0
    new_hires: int = 0
    resignations: int = 0
    notes: Optional[str] = None


@router.post("/staff")
def submit_staff(payload: StaffIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    row = db.query(StaffRecord).filter(StaffRecord.business_unit_id == bu_id, StaffRecord.report_date == payload.report_date).first()
    if row is None:
        row = StaffRecord(business_unit_id=bu_id, report_date=payload.report_date)
        db.add(row)
    row.total_staff = payload.total_staff
    row.present = payload.present
    row.absent = payload.absent
    row.new_hires = payload.new_hires
    row.resignations = payload.resignations
    row.notes = payload.notes
    row.submitted_by_id = user.id
    db.commit()
    return {"ok": True, "id": row.id}


@router.get("/staff")
def list_staff(business_unit_id: Optional[int] = None, days: int = 30,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    since = date.today() - timedelta(days=days)
    q = db.query(StaffRecord).filter(StaffRecord.report_date >= since)
    if bu_id is not None:
        q = q.filter(StaffRecord.business_unit_id == bu_id)
    rows = q.order_by(StaffRecord.report_date.desc()).all()
    return {"items": [
        {"id": r.id, "business_unit_id": r.business_unit_id, "report_date": r.report_date.isoformat(),
         "total_staff": r.total_staff, "present": r.present, "absent": r.absent,
         "new_hires": r.new_hires, "resignations": r.resignations, "notes": r.notes}
        for r in rows
    ]}


# ---------------------------------------------------------------- customers

class CustomerIn(BaseModel):
    business_unit_id: Optional[int] = None
    report_date: date
    footfall: Optional[int] = None
    transactions: Optional[int] = None
    complaints: int = 0
    satisfaction_score: Optional[float] = None
    notes: Optional[str] = None


@router.post("/customers")
def submit_customers(payload: CustomerIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    if payload.satisfaction_score is not None and not (1.0 <= payload.satisfaction_score <= 5.0):
        raise HTTPException(400, "satisfaction_score must be between 1.0 and 5.0")
    row = db.query(CustomerMetric).filter(CustomerMetric.business_unit_id == bu_id, CustomerMetric.report_date == payload.report_date).first()
    if row is None:
        row = CustomerMetric(business_unit_id=bu_id, report_date=payload.report_date)
        db.add(row)
    row.footfall = payload.footfall
    row.transactions = payload.transactions
    row.complaints = payload.complaints
    row.satisfaction_score = Decimal(str(payload.satisfaction_score)) if payload.satisfaction_score is not None else None
    row.notes = payload.notes
    row.submitted_by_id = user.id
    db.commit()
    return {"ok": True, "id": row.id}


@router.get("/customers")
def list_customers(business_unit_id: Optional[int] = None, days: int = 30,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    since = date.today() - timedelta(days=days)
    q = db.query(CustomerMetric).filter(CustomerMetric.report_date >= since)
    if bu_id is not None:
        q = q.filter(CustomerMetric.business_unit_id == bu_id)
    rows = q.order_by(CustomerMetric.report_date.desc()).all()
    return {"items": [
        {"id": r.id, "business_unit_id": r.business_unit_id, "report_date": r.report_date.isoformat(),
         "footfall": r.footfall, "transactions": r.transactions, "complaints": r.complaints,
         "satisfaction_score": float(r.satisfaction_score) if r.satisfaction_score is not None else None,
         "notes": r.notes}
        for r in rows
    ]}


# ---------------------------------------------------------------- forecast

@router.get("/forecast")
def forecast(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Month-end projection from MTD daily revenue/cash-flow run rate."""
    bu_id = bu_scope(user, business_unit_id)
    today = date.today()
    month_start = today.replace(day=1)
    q = db.query(DailyFinancial).filter(DailyFinancial.report_date >= month_start, DailyFinancial.report_date <= today)
    if bu_id is not None:
        q = q.filter(DailyFinancial.business_unit_id == bu_id)
    rows = q.all()
    import calendar
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    mtd_revenue = sum(float(r.revenue or 0) for r in rows)
    mtd_cash_flow = sum(float(r.cash_flow or 0) for r in rows)
    reporting_days = len({r.report_date for r in rows})
    scale = (days_in_month / days_elapsed) if days_elapsed else 1.0
    return {
        "month": today.strftime("%B %Y"),
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "reporting_days": reporting_days,
        "mtd_revenue": round(mtd_revenue, 2),
        "mtd_cash_flow": round(mtd_cash_flow, 2),
        "projected_revenue": round(mtd_revenue * scale, 2),
        "projected_cash_flow": round(mtd_cash_flow * scale, 2),
        "daily_avg_revenue": round(mtd_revenue / days_elapsed, 2) if days_elapsed else 0,
        "daily_avg_cash_flow": round(mtd_cash_flow / days_elapsed, 2) if days_elapsed else 0,
    }


# ---------------------------------------------------------------- licenses & documents

def serialize_document(db: Session, d: LicenseDocument) -> dict:
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == d.business_unit_id).first()
    days_left = (d.expiry_date - date.today()).days if d.expiry_date else None
    return {
        "id": d.id,
        "business_unit_id": d.business_unit_id,
        "bu_name": bu.name if bu else None,
        "title": d.title,
        "document_type": d.document_type,
        "issue_date": d.issue_date.isoformat() if d.issue_date else None,
        "expiry_date": d.expiry_date.isoformat() if d.expiry_date else None,
        "days_left": days_left,
        "notes": d.notes,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


class DocumentIn(BaseModel):
    business_unit_id: Optional[int] = None
    title: str = Field(min_length=1, max_length=255)
    document_type: str = "license"
    issue_date: Optional[date] = None
    expiry_date: date
    notes: Optional[str] = None


@router.get("/documents")
def list_documents(business_unit_id: Optional[int] = None, expiring_soon: bool = False,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(LicenseDocument)
    if bu_id is not None:
        q = q.filter(LicenseDocument.business_unit_id == bu_id)
    rows = q.order_by(LicenseDocument.expiry_date.asc()).all()
    items = [serialize_document(db, d) for d in rows]
    if expiring_soon:
        items = [i for i in items if i["days_left"] is not None and i["days_left"] <= 30]
    return {"items": items, "count": len(items)}


@router.post("/documents")
def create_document(payload: DocumentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    d = LicenseDocument(
        business_unit_id=bu_id, title=payload.title,
        document_type=payload.document_type or "license",
        issue_date=payload.issue_date, expiry_date=payload.expiry_date,
        notes=payload.notes, created_by_id=user.id,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return serialize_document(db, d)


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.query(LicenseDocument).filter(LicenseDocument.id == document_id).first()
    if not d:
        raise HTTPException(404, "Document not found")
    bu_scope(user, d.business_unit_id)
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.post("/documents/check-expiry")
def check_document_expiry(days: int = 30, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Notify each BU's CEO + all Chairman/HO about documents expiring within
    `days`. Throttled per document to one alert per 7 days."""
    return run_check_document_expiry(db, days)


def run_check_document_expiry(db: Session, days: int = 30) -> dict:
    """Job-callable: notify CEOs + Chairman/HO about documents expiring soon."""
    today = date.today()
    horizon = today + timedelta(days=days)
    docs = db.query(LicenseDocument).filter(
        LicenseDocument.expiry_date >= today, LicenseDocument.expiry_date <= horizon).all()
    admins = db.query(User).filter(User.role.in_(["Chairman", "HO"]), User.is_active == True).all()  # noqa: E712
    sent = 0
    for d in docs:
        if d.expiry_notified_at and d.expiry_notified_at > datetime.utcnow() - timedelta(days=7):
            continue
        bu = db.query(BusinessUnit).filter(BusinessUnit.id == d.business_unit_id).first()
        bu_name = bu.name if bu else f"BU {d.business_unit_id}"
        days_left = (d.expiry_date - today).days
        title = f"Document expiring: {d.title} ({bu_name})"
        message = f"{d.document_type} expires in {days_left} days ({d.expiry_date.isoformat()})."
        ceos = db.query(User).filter(User.role == "CEO", User.business_unit_id == d.business_unit_id, User.is_active == True).all()  # noqa: E712
        seen = set()
        for u in admins + ceos:
            if u.id in seen:
                continue
            seen.add(u.id)
            db.add(Notification(user_id=u.id, type="document_expiry",
                                title=title, message=message, link="/admin/operations-center"))
            sent += 1
        d.expiry_notified_at = datetime.utcnow()
    db.commit()
    return {"checked": len(docs), "notified": sent}


# ---------------------------------------------------------------- monthly chairman PDF report

@router.get("/monthly-report")
def monthly_report(month: Optional[str] = None, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """One-page-per-BU monthly summary PDF for the Chairman (admin roles only).

    `month` format YYYY-MM; defaults to the current month.
    """
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from sqlalchemy import func

    today = date.today()
    month = validate_period(month)
    if month:
        y, m = (int(x) for x in month.split("-"))
        month_start = date(y, m, 1)
    else:
        month_start = date(today.year, today.month, 1)
    import calendar as _cal
    month_end = date(month_start.year, month_start.month, _cal.monthrange(month_start.year, month_start.month)[1])
    days_in_month = (month_end - month_start).days + 1

    from src.models import MonthlyTarget

    fin = (db.query(DailyFinancial.business_unit_id,
                    func.coalesce(func.sum(DailyFinancial.revenue), 0),
                    func.coalesce(func.sum(DailyFinancial.cash_flow), 0),
                    func.count(func.distinct(DailyFinancial.report_date)))
           .filter(DailyFinancial.report_date >= month_start, DailyFinancial.report_date <= month_end)
           .group_by(DailyFinancial.business_unit_id).all())
    fin_map = {row[0]: (float(row[1]), float(row[2]), int(row[3])) for row in fin}
    targets = {t.business_unit_id: t for t in db.query(MonthlyTarget).filter(MonthlyTarget.target_month == month_start).all()}
    bus = db.query(BusinessUnit).order_by(BusinessUnit.name).all()

    rows = []
    for bu in bus:
        rev, cf, rep_days = fin_map.get(bu.id, (0.0, 0.0, 0))
        t = targets.get(bu.id)
        trev = float(t.monthly_revenue_target) if t and t.monthly_revenue_target else 0.0
        ach = (rev / trev * 100) if trev else None
        rows.append({"name": bu.name, "revenue": rev, "cash_flow": cf,
                     "reports": rep_days, "target": trev, "ach": ach})

    totals = {
        "revenue": sum(r["revenue"] for r in rows),
        "cash_flow": sum(r["cash_flow"] for r in rows),
        "target": sum(r["target"] for r in rows),
    }
    totals["ach"] = (totals["revenue"] / totals["target"] * 100) if totals["target"] else None

    # Alert data
    low_items = [i for i in db.query(InventoryItem).filter(InventoryItem.is_active == True).all()  # noqa: E712
                 if float(i.current_stock or 0) <= float(i.reorder_level or 0) and float(i.reorder_level or 0) > 0]
    pending_expenses = db.query(ExpenseRequest).filter(ExpenseRequest.status == "pending").count()
    expiring_docs = [d for d in db.query(LicenseDocument).all()
                     if d.expiry_date and 0 <= (d.expiry_date - today).days <= 30]

    from fpdf import FPDF

    def t(text):
        """Strip characters the core latin-1 PDF font can't render."""
        return (text or "").encode("latin-1", errors="replace").decode("latin-1")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    W = pdf.w - 20

    pdf.set_font("helvetica", "B", 18)
    pdf.cell(W, 10, "Business Operations - Monthly Report", ln=1)
    pdf.set_font("helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    label = month_start.strftime("%B %Y")
    pdf.cell(W, 6, f"{label}  |  Generated {today.isoformat()}", ln=1)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    def money(v):
        return f"Ks {v:,.0f}"

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Portfolio Summary", ln=1)
    pdf.set_font("helvetica", "", 11)
    summary = [
        ("Total revenue", money(totals["revenue"])),
        ("Total cash flow", money(totals["cash_flow"])),
        ("Revenue target", money(totals["target"]) if totals["target"] else "-"),
        ("Overall achievement", f"{totals['ach']:.1f}%" if totals["ach"] is not None else "-"),
        ("Business units", str(len(rows))),
    ]
    for k, v in summary:
        pdf.cell(60, 6.5, k)
        pdf.cell(0, 6.5, v, ln=1)
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Business Unit Performance", ln=1)
    headers = [("BU", 52), ("Revenue", 30), ("Cash Flow", 30), ("Target", 30), ("Ach. %", 18), ("Reports", 20)]
    pdf.set_font("helvetica", "B", 10)
    for h, w in headers:
        pdf.cell(w, 7, h, border=1)
    pdf.ln()
    pdf.set_font("helvetica", "", 10)
    for r in sorted(rows, key=lambda x: -(x["ach"] or 0)):
        vals = [t(r["name"][:26]), f"{r['revenue']:,.0f}", f"{r['cash_flow']:,.0f}",
                f"{r['target']:,.0f}" if r["target"] else "-",
                f"{r['ach']:.0f}%" if r["ach"] is not None else "-",
                f"{r['reports']}/{days_in_month}"]
        for (_, w), v in zip(headers, vals):
            pdf.cell(w, 6.5, v, border=1)
        pdf.ln()
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Alerts", ln=1)
    pdf.set_font("helvetica", "", 11)
    alerts = []
    no_report = [r["name"] for r in rows if r["reports"] == 0]
    if no_report:
        alerts.append(f"No reports this month: {', '.join(no_report)}")
    if low_items:
        names = ", ".join(f"{i.name}" for i in low_items[:8])
        alerts.append(f"Low stock items ({len(low_items)}): {names}")
    if pending_expenses:
        alerts.append(f"Expense requests pending approval: {pending_expenses}")
    for d in expiring_docs:
        alerts.append(f"Document expiring soon: {d.title} - {d.expiry_date.isoformat()}")
    if not alerts:
        alerts.append("No alerts.")
    for a in alerts:
        pdf.multi_cell(W, 6, t(f"- {a}"))

    buf = BytesIO(pdf.output())
    filename = f"monthly_report_{month_start.strftime('%Y-%m')}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- Excel export

@router.get("/export-excel")
def export_excel(tab: str = "actions", business_unit_id: Optional[int] = None,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download one Operations Center tab as an .xlsx workbook."""
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    bu_id = bu_scope(user, business_unit_id)
    bu_names = {bu.id: bu.name for bu in db.query(BusinessUnit).all()}
    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    def build(title, headers, rows):
        wb = Workbook()
        ws = wb.active
        ws.title = title[:31]
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        for r in rows:
            ws.append(r)
        for col in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col) + 3
            ws.column_dimensions[col[0].column_letter].width = min(max(width, 12), 42)
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="ops_{tab}.xlsx"'},
        )

    def scoped(q, model):
        return q.filter(model.business_unit_id == bu_id).all() if bu_id is not None else q.all()

    if tab == "actions":
        q = db.query(ActionItem).order_by(ActionItem.created_at.desc())
        rows = scoped(q, ActionItem)
        return build("Action Items",
                     ["Title", "BU", "Status", "Due Date", "Assigned To", "Created"],
                     [[i.title, bu_names.get(i.business_unit_id, ""), i.status,
                       i.due_date.isoformat() if i.due_date else "",
                       (db.query(User).get(i.assigned_to_id).full_name if i.assigned_to_id else "") or "",
                       i.created_at.strftime("%Y-%m-%d %H:%M") if i.created_at else ""] for i in rows])

    if tab == "inventory":
        q = db.query(InventoryItem).filter(InventoryItem.is_active == True)  # noqa: E712
        rows = scoped(q, InventoryItem)
        return build("Inventory",
                     ["Item", "BU", "Stock", "Unit", "Reorder Level", "Low Stock"],
                     [[i.name, bu_names.get(i.business_unit_id, ""), float(i.current_stock or 0),
                       i.unit or "", float(i.reorder_level or 0),
                       "YES" if float(i.current_stock or 0) <= float(i.reorder_level or 0) and float(i.reorder_level or 0) > 0 else ""] for i in rows])

    if tab == "expenses":
        q = db.query(ExpenseRequest).order_by(ExpenseRequest.created_at.desc())
        rows = scoped(q, ExpenseRequest)
        return build("Expenses",
                     ["Title", "Amount (Ks)", "Category", "BU", "Status", "Submitted By", "Submitted"],
                     [[e.title, float(e.amount or 0), e.category or "", bu_names.get(e.business_unit_id, ""),
                       e.status,
                       (db.query(User).get(e.submitted_by_id).full_name if e.submitted_by_id else "") or "",
                       e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else ""] for e in rows])

    if tab == "documents":
        q = db.query(LicenseDocument).order_by(LicenseDocument.expiry_date.asc())
        rows = scoped(q, LicenseDocument)
        return build("Documents",
                     ["Title", "Type", "BU", "Issue Date", "Expiry Date", "Days Left", "Notes"],
                     [[d.title, d.document_type, bu_names.get(d.business_unit_id, ""),
                       d.issue_date.isoformat() if d.issue_date else "",
                       d.expiry_date.isoformat() if d.expiry_date else "",
                       (d.expiry_date - date.today()).days if d.expiry_date else "",
                       d.notes or ""] for d in rows])

    if tab == "staff":
        q = db.query(StaffRecord).order_by(StaffRecord.report_date.desc()).limit(500)
        rows = scoped(q, StaffRecord)
        return build("Staff",
                     ["Date", "BU", "Total", "Present", "Absent", "New Hires", "Resignations"],
                     [[s.report_date.isoformat() if s.report_date else "", bu_names.get(s.business_unit_id, ""),
                       s.total_staff, s.present, s.absent, s.new_hires, s.resignations] for s in rows])

    if tab == "customers":
        q = db.query(CustomerMetric).order_by(CustomerMetric.report_date.desc()).limit(500)
        rows = scoped(q, CustomerMetric)
        return build("Customers",
                     ["Date", "BU", "Footfall", "Transactions", "Complaints", "Satisfaction (1-5)"],
                     [[c.report_date.isoformat() if c.report_date else "", bu_names.get(c.business_unit_id, ""),
                       c.footfall, c.transactions, c.complaints,
                       float(c.satisfaction_score) if c.satisfaction_score is not None else ""] for c in rows])

    raise HTTPException(400, "tab must be actions/inventory/expenses/documents/staff/customers")


# ================================================================== suppliers & purchasing (ERP Phase 3)

def serialize_supplier(s: Supplier) -> dict:
    return {
        "id": s.id, "business_unit_id": s.business_unit_id, "name": s.name,
        "contact_person": s.contact_person, "phone": s.phone, "email": s.email,
        "address": s.address, "is_active": s.is_active,
        "scope": "Group" if s.business_unit_id is None else (s.business_unit.name if s.business_unit else "BU"),
    }


class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    business_unit_id: Optional[int] = None  # None = group-shared
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


@router.get("/suppliers")
def list_suppliers(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Supplier).filter(Supplier.is_active == True)  # noqa: E712
    if user.role == "CEO":
        q = q.filter((Supplier.business_unit_id == user.business_unit_id) | (Supplier.business_unit_id == None))  # noqa: E711
    elif business_unit_id:
        q = q.filter((Supplier.business_unit_id == business_unit_id) | (Supplier.business_unit_id == None))  # noqa: E711
    rows = q.order_by(Supplier.name).all()
    return {"items": [serialize_supplier(s) for s in rows]}


@router.post("/suppliers")
def create_supplier(payload: SupplierIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CEO":
        payload.business_unit_id = user.business_unit_id
    s = Supplier(**payload.model_dump(), created_by_id=user.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return serialize_supplier(s)


@router.delete("/suppliers/{supplier_id}")
def delete_supplier(supplier_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not s:
        raise HTTPException(404, "Supplier not found")
    if s.business_unit_id is not None:
        bu_scope(user, s.business_unit_id)
    s.is_active = False
    db.commit()
    return {"ok": True}


def po_total(po: PurchaseOrder) -> Decimal:
    return sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_cost)) for l in po.lines), Decimal("0"))


def serialize_po(db: Session, po: PurchaseOrder) -> dict:
    return {
        "id": po.id, "po_no": po.po_no, "business_unit_id": po.business_unit_id,
        "bu_name": po.business_unit.name if po.business_unit else None,
        "supplier_id": po.supplier_id,
        "supplier_name": po.supplier.name if po.supplier else None,
        "status": po.status, "order_date": po.order_date.isoformat() if po.order_date else None,
        "notes": po.notes, "total": float(po_total(po)),
        "approval_stage": getattr(po, "approval_stage", None),
        "stage_label": ("Waiting HO review" if getattr(po, "approval_stage", None) == "first" and po.status == "draft"
                        else "Waiting final approval" if getattr(po, "approval_stage", None) == "final" and po.status == "draft"
                        else "Approval rejected" if getattr(po, "approval_stage", None) == "rejected" else None),
        "currency": getattr(po, "currency", "MMK") or "MMK",
        "exchange_rate": float(getattr(po, "exchange_rate", 1) or 1),
        "received_at": po.received_at.isoformat() if po.received_at else None,
        "paid_at": po.paid_at.isoformat() if po.paid_at else None,
        "lines": [{"id": l.id, "item_name": l.item_name, "inventory_item_id": l.inventory_item_id,
                   "quantity": float(l.quantity), "unit_cost": float(l.unit_cost),
                   "line_total": float(Decimal(str(l.quantity)) * Decimal(str(l.unit_cost)))} for l in po.lines],
    }


class POLineIn(BaseModel):
    item_name: str = Field(min_length=1, max_length=150)
    inventory_item_id: Optional[int] = None
    quantity: float = Field(gt=0)
    unit_cost: float = Field(ge=0)


class POIn(BaseModel):
    business_unit_id: int
    supplier_id: int
    order_date: date
    notes: Optional[str] = None
    currency: str = "MMK"
    exchange_rate: Optional[float] = None  # MMK per 1 unit; defaults to latest rate
    project_id: Optional[int] = None
    lines: list[POLineIn] = Field(min_length=1)


@router.get("/purchase-orders")
def list_pos(business_unit_id: Optional[int] = None, status: Optional[str] = None,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CEO":
        business_unit_id = user.business_unit_id
    q = db.query(PurchaseOrder)
    if business_unit_id:
        q = q.filter(PurchaseOrder.business_unit_id == business_unit_id)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    rows = q.order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.id.desc()).limit(200).all()
    return {"items": [serialize_po(db, po) for po in rows]}


@router.post("/purchase-orders")
def create_po(payload: POIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_scope(user, payload.business_unit_id)
    supplier = db.query(Supplier).filter(Supplier.id == payload.supplier_id, Supplier.is_active == True).first()  # noqa: E712
    if not supplier:
        raise HTTPException(404, "Supplier not found")
    count = db.query(PurchaseOrder).filter(PurchaseOrder.business_unit_id == payload.business_unit_id).count()
    from src.routers.accounting import latest_rate
    fx = (Decimal(str(payload.exchange_rate)) if payload.exchange_rate
          else latest_rate(db, payload.currency))
    # ERP completion: POs over the two-stage threshold (in MMK, like the
    # journals) start at approval stage "first" and cannot be ordered
    # until HO + Chairman have approved.
    total_mmk = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_cost)) for l in payload.lines),
                    Decimal("0")) * Decimal(str(fx or 1))
    po = PurchaseOrder(
        business_unit_id=payload.business_unit_id, supplier_id=payload.supplier_id,
        po_no=f"PO-{payload.business_unit_id}-{count + 1:04d}", status="draft",
        order_date=payload.order_date, notes=payload.notes, created_by_id=user.id,
        currency=(payload.currency or "MMK").upper(),
        exchange_rate=fx,
        project_id=payload.project_id,
        approval_stage="first" if total_mmk > TWO_STAGE_THRESHOLD else None,
    )
    db.add(po)
    db.flush()
    for ln in payload.lines:
        db.add(PurchaseOrderLine(po_id=po.id, item_name=ln.item_name,
                                 inventory_item_id=ln.inventory_item_id,
                                 quantity=Decimal(str(ln.quantity)),
                                 unit_cost=Decimal(str(ln.unit_cost))))
    db.commit()
    db.refresh(po)
    return serialize_po(db, po)


def _po_or_404(db: Session, po_id: int) -> PurchaseOrder:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    return po


def _post_po_journal(db: Session, po: PurchaseOrder, user_id: int, kind: str) -> None:
    """kind=receive: Dr 1140 Inventory / Cr 2110 AP. kind=pay: Dr 2110 / Cr 1110."""
    try:
        from src.routers.accounting import account_by_code, create_entry
        # Journals are MMK; foreign-currency POs convert at the PO rate.
        total = po_total(po) * Decimal(str(getattr(po, "exchange_rate", 1) or 1))
        if total <= 0:
            return
        if kind == "receive":
            dr = account_by_code(db, po.business_unit_id, "1140")
            cr = account_by_code(db, po.business_unit_id, "2110")
            desc = f"Goods received {po.po_no} — {po.supplier.name if po.supplier else ''}"
        else:
            dr = account_by_code(db, po.business_unit_id, "2110")
            cr = account_by_code(db, po.business_unit_id, "1110")
            desc = f"Supplier payment {po.po_no} — {po.supplier.name if po.supplier else ''}"
        if dr and cr:
            create_entry(db, po.business_unit_id, date.today(), desc, "purchase",
                         user_id, [{"account_id": dr.id, "debit": total, "credit": 0},
                                   {"account_id": cr.id, "debit": 0, "credit": total}],
                         source_id=po.id, post=True)
            db.commit()
    except Exception:
        db.rollback()


@router.post("/purchase-orders/{po_id}/order")
def order_po(po_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    po = _po_or_404(db, po_id)
    bu_scope(user, po.business_unit_id)
    if po.status != "draft":
        raise HTTPException(400, f"Cannot order a PO in status {po.status}")
    stage = getattr(po, "approval_stage", None)
    if stage in ("first", "final"):
        raise HTTPException(400, "This PO is over the approval threshold and is still awaiting approval")
    if stage == "rejected":
        raise HTTPException(400, "This PO's approval was rejected — edit and resubmit it first")
    po.status = "ordered"
    db.commit()
    return serialize_po(db, po)


@router.post("/purchase-orders/{po_id}/approve")
def approve_po(po_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Two-stage approval, mirroring expenses: HO clears stage "first",
    the Chairman gives the final approval."""
    po = _po_or_404(db, po_id)
    if po.status != "draft":
        raise HTTPException(400, "Only draft POs can be approved")
    stage = getattr(po, "approval_stage", None)
    if stage == "first":
        if user.role != "HO":
            raise HTTPException(403, "Large POs need HO review first")
        po.approval_stage = "final"
    elif stage == "final":
        if user.role != "Chairman":
            raise HTTPException(403, "Final PO approval is the Chairman's")
        po.approval_stage = "approved"
    else:
        raise HTTPException(400, "This PO is not awaiting approval")
    db.commit()
    db.refresh(po)
    return serialize_po(db, po)


@router.post("/purchase-orders/{po_id}/reject-approval")
def reject_po_approval(po_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    po = _po_or_404(db, po_id)
    if po.status != "draft":
        raise HTTPException(400, "Only draft POs can be rejected")
    stage = getattr(po, "approval_stage", None)
    if stage == "first" and user.role != "HO":
        raise HTTPException(403, "Large POs need HO review first")
    if stage == "final" and user.role != "Chairman":
        raise HTTPException(403, "Final PO approval is the Chairman's")
    if stage not in ("first", "final"):
        raise HTTPException(400, "This PO is not awaiting approval")
    po.approval_stage = "rejected"
    db.commit()
    db.refresh(po)
    return serialize_po(db, po)


class POUpdate(BaseModel):
    notes: Optional[str] = None
    lines: Optional[list[POLineIn]] = None


@router.patch("/purchase-orders/{po_id}")
def update_po(po_id: int, payload: POUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Edit a draft PO. Editing a rejected PO resubmits it: the approval
    stage is recomputed from the new total."""
    po = _po_or_404(db, po_id)
    bu_scope(user, po.business_unit_id)
    if po.status != "draft":
        raise HTTPException(400, "Only draft POs can be edited")
    if payload.notes is not None:
        po.notes = payload.notes
    if payload.lines is not None:
        if not payload.lines:
            raise HTTPException(400, "A PO needs at least one line")
        for old in list(po.lines):
            db.delete(old)
        db.flush()
        for ln in payload.lines:
            db.add(PurchaseOrderLine(po_id=po.id, item_name=ln.item_name,
                                     inventory_item_id=ln.inventory_item_id,
                                     quantity=Decimal(str(ln.quantity)),
                                     unit_cost=Decimal(str(ln.unit_cost))))
        db.flush()
    if getattr(po, "approval_stage", None) in ("rejected", "first", "final"):
        total_mmk = po_total(po) * Decimal(str(getattr(po, "exchange_rate", 1) or 1))
        po.approval_stage = "first" if total_mmk > TWO_STAGE_THRESHOLD else None
    db.commit()
    db.refresh(po)
    return serialize_po(db, po)


@router.post("/purchase-orders/{po_id}/receive")
def receive_po(po_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Receive goods: stock-in each line at moving-average cost, post AP journal."""
    po = _po_or_404(db, po_id)
    bu_scope(user, po.business_unit_id)
    if po.status not in ("draft", "ordered"):
        raise HTTPException(400, f"Cannot receive a PO in status {po.status}")
    fx = Decimal(str(getattr(po, "exchange_rate", 1) or 1))
    for ln in po.lines:
        item = None
        if ln.inventory_item_id:
            item = db.query(InventoryItem).filter(InventoryItem.id == ln.inventory_item_id).first()
        if item is None:
            item = db.query(InventoryItem).filter(
                InventoryItem.business_unit_id == po.business_unit_id,
                InventoryItem.name == ln.item_name, InventoryItem.is_active == True).first()  # noqa: E712
        if item is None:
            item = InventoryItem(business_unit_id=po.business_unit_id, name=ln.item_name,
                                 current_stock=0, unit_cost=ln.unit_cost * fx)
            db.add(item)
            db.flush()
        ln.inventory_item_id = item.id
        old_qty = Decimal(str(item.current_stock or 0))
        old_avg = Decimal(str(item.unit_cost or 0))
        in_qty = Decimal(str(ln.quantity))
        in_cost = Decimal(str(ln.unit_cost)) * fx  # inventory is valued in MMK
        new_qty = old_qty + in_qty
        # Moving (weighted) average cost — the standard valuation formula.
        item.unit_cost = ((old_qty * old_avg + in_qty * in_cost) / new_qty) if new_qty > 0 else in_cost
        item.current_stock = new_qty
        item.updated_at = datetime.utcnow()
        db.add(StockMovement(item_id=item.id, movement_type="in", quantity=in_qty,
                             reason=f"PO {po.po_no} received", recorded_by_id=user.id))
        _maybe_notify_low_stock(db, item)
    po.status = "received"
    po.received_at = datetime.utcnow()
    db.commit()
    _post_po_journal(db, po, user.id, "receive")
    db.refresh(po)
    return serialize_po(db, po)


@router.post("/purchase-orders/{po_id}/pay")
def pay_po(po_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    po = _po_or_404(db, po_id)
    bu_scope(user, po.business_unit_id)
    if po.status != "received":
        raise HTTPException(400, "Only received POs can be paid")
    po.status = "paid"
    po.paid_at = datetime.utcnow()
    db.commit()
    _post_po_journal(db, po, user.id, "pay")
    db.refresh(po)
    return serialize_po(db, po)


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_po(po_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    po = _po_or_404(db, po_id)
    bu_scope(user, po.business_unit_id)
    if po.status in ("received", "paid"):
        raise HTTPException(400, f"Cannot cancel a {po.status} PO")
    po.status = "cancelled"
    db.commit()
    return serialize_po(db, po)


# ================================================================== sales, invoices, aging (ERP Phase 4)

def serialize_customer(c: Customer) -> dict:
    return {
        "id": c.id, "business_unit_id": c.business_unit_id, "name": c.name,
        "contact_person": c.contact_person, "phone": c.phone, "email": c.email,
        "address": c.address, "credit_limit": float(c.credit_limit) if c.credit_limit is not None else None,
        "is_active": c.is_active,
    }


class CustomerIn(BaseModel):
    business_unit_id: int
    name: str = Field(min_length=1, max_length=150)
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    credit_limit: Optional[float] = None


@router.get("/sales-customers")
def list_sales_customers(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CEO":
        business_unit_id = user.business_unit_id
    q = db.query(Customer).filter(Customer.is_active == True)  # noqa: E712
    if business_unit_id:
        q = q.filter(Customer.business_unit_id == business_unit_id)
    return {"items": [serialize_customer(c) for c in q.order_by(Customer.name).all()]}


@router.post("/sales-customers")
def create_sales_customer(payload: CustomerIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_scope(user, payload.business_unit_id)
    c = Customer(**payload.model_dump(), created_by_id=user.id)
    db.add(c)
    db.commit()
    db.refresh(c)
    return serialize_customer(c)


@router.delete("/sales-customers/{customer_id}")
def delete_sales_customer(customer_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(404, "Customer not found")
    bu_scope(user, c.business_unit_id)
    c.is_active = False
    db.commit()
    return {"ok": True}


def sales_returns_total(db: Session, inv: SalesInvoice) -> tuple[Decimal, Decimal, Decimal]:
    """(net, tax, total) of posted sales returns against this invoice.

    Each return line reverses at the invoice line's price; tax is the
    invoice's rate applied to the returned net — the same split the send
    journal used, proportionally.
    """
    rows = (db.query(SalesReturnLine)
            .join(SalesReturn, SalesReturnLine.return_id == SalesReturn.id)
            .filter(SalesReturn.invoice_id == inv.id,
                    SalesReturn.status == "posted").all())
    net = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_price)) for l in rows), Decimal("0"))
    tax = (net * Decimal(str(inv.tax_rate or 0)) / Decimal("100")).quantize(Decimal("0.01"))
    return net, tax, net + tax


def invoice_amounts(db: Session, inv: SalesInvoice) -> dict:
    subtotal = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_price)) for l in inv.lines), Decimal("0"))
    tax = (subtotal * Decimal(str(inv.tax_rate or 0)) / Decimal("100")).quantize(Decimal("0.01"))
    total = subtotal + tax
    # Query payments directly: the inv.payments relationship collection can
    # be stale within a session right after adding a payment row.
    paid = db.query(func.coalesce(func.sum(InvoicePayment.amount), 0)).filter(
        InvoicePayment.invoice_id == inv.id).scalar()
    paid = Decimal(str(paid or 0))
    # ERP completion: posted credit notes (sales returns) reduce what the
    # customer still owes. Defaults to 0 when there are no returns.
    _, _, returns = sales_returns_total(db, inv)
    return {"subtotal": subtotal, "tax": tax, "total": total, "paid": paid,
            "returns": returns, "balance": total - returns - paid}


def serialize_invoice(db: Session, inv: SalesInvoice) -> dict:
    a = invoice_amounts(db, inv)
    overdue = (inv.status in ("sent", "partial") and inv.due_date is not None
               and inv.due_date < date.today() and a["balance"] > 0)
    return {
        "id": inv.id, "invoice_no": inv.invoice_no, "business_unit_id": inv.business_unit_id,
        "bu_name": inv.business_unit.name if inv.business_unit else None,
        "customer_id": inv.customer_id,
        "customer_name": inv.customer.name if inv.customer else None,
        "status": "overdue" if overdue else inv.status,
        "stored_status": inv.status,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "tax_rate": float(inv.tax_rate or 0), "notes": inv.notes,
        "currency": getattr(inv, "currency", "MMK") or "MMK",
        "exchange_rate": float(getattr(inv, "exchange_rate", 1) or 1),
        "subtotal": float(a["subtotal"]), "tax_amount": float(a["tax"]),
        "total": float(a["total"]), "paid": float(a["paid"]), "balance": float(a["balance"]),
        "returns": float(a.get("returns") or 0),
        "lines": [{"id": l.id, "description": l.description, "quantity": float(l.quantity),
                   "unit_price": float(l.unit_price),
                   "serial_nos": _serial_list(getattr(l, "serial_nos", None)),
                   "inventory_item_id": l.inventory_item_id,
                   "line_total": float(Decimal(str(l.quantity)) * Decimal(str(l.unit_price)))} for l in inv.lines],
        "payments": [{"id": p.id, "amount": float(p.amount),
                      "payment_date": p.payment_date.isoformat() if p.payment_date else None,
                      "method": p.method} for p in inv.payments],
    }


class InvoiceLineIn(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    quantity: float = Field(gt=0, default=1)
    unit_price: float = Field(ge=0)
    inventory_item_id: Optional[int] = None
    serial_nos: Optional[list[str]] = None  # optional serial/IMEI numbers sold


class InvoiceIn(BaseModel):
    business_unit_id: int
    customer_id: int
    invoice_date: date
    due_date: Optional[date] = None
    tax_rate: float = 5
    notes: Optional[str] = None
    currency: str = "MMK"
    exchange_rate: Optional[float] = None  # MMK per 1 unit; defaults to latest rate
    project_id: Optional[int] = None
    lines: list[InvoiceLineIn] = Field(min_length=1)


@router.get("/invoices")
def list_invoices(business_unit_id: Optional[int] = None, status: Optional[str] = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CEO":
        business_unit_id = user.business_unit_id
    q = db.query(SalesInvoice)
    if business_unit_id:
        q = q.filter(SalesInvoice.business_unit_id == business_unit_id)
    if status:
        q = q.filter(SalesInvoice.status == status)
    rows = q.order_by(SalesInvoice.invoice_date.desc(), SalesInvoice.id.desc()).limit(200).all()
    return {"items": [serialize_invoice(db, inv) for inv in rows]}


@router.post("/invoices")
def create_invoice(payload: InvoiceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_scope(user, payload.business_unit_id)
    cust = db.query(Customer).filter(Customer.id == payload.customer_id, Customer.is_active == True).first()  # noqa: E712
    if not cust or cust.business_unit_id != payload.business_unit_id:
        raise HTTPException(404, "Customer not found in this BU")
    count = db.query(SalesInvoice).filter(SalesInvoice.business_unit_id == payload.business_unit_id).count()
    from src.routers.accounting import latest_rate
    inv = SalesInvoice(
        business_unit_id=payload.business_unit_id, customer_id=payload.customer_id,
        invoice_no=f"INV-{payload.business_unit_id}-{count + 1:04d}", status="draft",
        invoice_date=payload.invoice_date, due_date=payload.due_date,
        tax_rate=Decimal(str(payload.tax_rate)), notes=payload.notes, created_by_id=user.id,
        currency=(payload.currency or "MMK").upper(),
        exchange_rate=(Decimal(str(payload.exchange_rate)) if payload.exchange_rate
                       else latest_rate(db, payload.currency)),
        project_id=payload.project_id,
    )
    db.add(inv)
    db.flush()
    import json as _json
    for ln in payload.lines:
        db.add(SalesInvoiceLine(invoice_id=inv.id, description=ln.description,
                                quantity=Decimal(str(ln.quantity)),
                                unit_price=Decimal(str(ln.unit_price)),
                                inventory_item_id=ln.inventory_item_id,
                                serial_nos=_json.dumps(ln.serial_nos) if ln.serial_nos else None))
    db.commit()
    db.refresh(inv)
    return serialize_invoice(db, inv)


def _invoice_or_404(db: Session, invoice_id: int) -> SalesInvoice:
    inv = db.query(SalesInvoice).filter(SalesInvoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


def _post_invoice_journal(db: Session, inv: SalesInvoice, user_id: int, kind: str, amount: Decimal = Decimal("0")) -> None:
    try:
        from src.routers.accounting import account_by_code, create_entry
        bu = inv.business_unit_id
        # Journals are MMK (functional currency); foreign-currency
        # invoices convert at the invoice exchange rate.
        fx = Decimal(str(getattr(inv, "exchange_rate", 1) or 1))
        if kind == "send":
            a = invoice_amounts(db, inv)
            ar = account_by_code(db, bu, "1130")
            sales = account_by_code(db, bu, "4100")
            tax_acc = account_by_code(db, bu, "2130")
            if not (ar and sales):
                return
            lines = [{"account_id": ar.id, "debit": a["total"] * fx, "credit": 0},
                     {"account_id": sales.id, "debit": 0, "credit": a["subtotal"] * fx}]
            if a["tax"] > 0 and tax_acc:
                lines.append({"account_id": tax_acc.id, "debit": 0, "credit": a["tax"] * fx})
            create_entry(db, bu, inv.invoice_date, f"Invoice {inv.invoice_no} — {inv.customer.name if inv.customer else ''}",
                         "invoice", user_id, lines, source_id=inv.id, post=True)
        else:  # payment
            cash = account_by_code(db, bu, "1110")
            ar = account_by_code(db, bu, "1130")
            if not (cash and ar):
                return
            create_entry(db, bu, date.today(), f"Payment received {inv.invoice_no}",
                         "invoice", user_id,
                         [{"account_id": cash.id, "debit": amount * fx, "credit": 0},
                          {"account_id": ar.id, "debit": 0, "credit": amount * fx}],
                         source_id=inv.id, post=True)
        db.commit()
    except Exception:
        db.rollback()


@router.post("/invoices/{invoice_id}/send")
def send_invoice(invoice_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inv = _invoice_or_404(db, invoice_id)
    bu_scope(user, inv.business_unit_id)
    if inv.status != "draft":
        raise HTTPException(400, f"Cannot send an invoice in status {inv.status}")
    # Credit-limit enforcement: outstanding (other sent invoices of this
    # customer, converted to MMK) + this invoice must stay within the limit.
    cust = inv.customer
    if cust is not None and getattr(cust, "credit_limit", None):
        limit = Decimal(str(cust.credit_limit))
        outstanding = Decimal("0")
        for other in db.query(SalesInvoice).filter(
                SalesInvoice.customer_id == cust.id,
                SalesInvoice.status.in_(("sent", "partial"))).all():
            outstanding += invoice_amounts(db, other)["balance"] * Decimal(str(getattr(other, "exchange_rate", 1) or 1))
        this_total = invoice_amounts(db, inv)["total"] * Decimal(str(getattr(inv, "exchange_rate", 1) or 1))
        if outstanding + this_total > limit:
            raise HTTPException(400, f"Credit limit exceeded for {cust.name}: limit Ks {float(limit):,.0f}, "
                                     f"outstanding Ks {float(outstanding):,.0f}, this invoice Ks {float(this_total):,.0f}")
    # Serial-tracked lines: validate + mark the units sold with this invoice.
    _mark_line_serials_sold(db, inv.business_unit_id, inv.lines, invoice_id=inv.id)
    inv.status = "sent"
    inv.sent_at = datetime.utcnow()
    db.commit()
    _post_invoice_journal(db, inv, user.id, "send")
    db.refresh(inv)
    return serialize_invoice(db, inv)


class PaymentIn(BaseModel):
    amount: float = Field(gt=0)
    payment_date: date
    method: str = "cash"
    notes: Optional[str] = None


@router.post("/invoices/{invoice_id}/payments")
def record_payment(invoice_id: int, payload: PaymentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inv = _invoice_or_404(db, invoice_id)
    bu_scope(user, inv.business_unit_id)
    if inv.status not in ("sent", "partial"):
        raise HTTPException(400, f"Cannot take payment on a {inv.status} invoice")
    amt = Decimal(str(payload.amount))
    a = invoice_amounts(db, inv)
    if amt > a["balance"]:
        raise HTTPException(400, f"Payment {amt} exceeds balance {a['balance']}")
    db.add(InvoicePayment(invoice_id=inv.id, amount=amt, payment_date=payload.payment_date,
                          method=payload.method, notes=payload.notes, received_by_id=user.id))
    db.flush()
    a2 = invoice_amounts(db, inv)
    inv.status = "paid" if a2["balance"] <= 0 else "partial"
    db.commit()
    _post_invoice_journal(db, inv, user.id, "payment", amt)
    try:
        from src.models import PropertyUnit as _PU
        from src.routers.modules import allocate_installments
        allocate_installments(db, inv.id)
        if inv.status == "paid":
            unit = db.query(_PU).filter(_PU.sale_invoice_id == inv.id).first()
            if unit and unit.status == "reserved":
                unit.status = "sold"
                db.commit()
    except Exception:
        db.rollback()
    db.refresh(inv)
    return serialize_invoice(db, inv)


@router.post("/invoices/{invoice_id}/cancel")
def cancel_invoice(invoice_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inv = _invoice_or_404(db, invoice_id)
    bu_scope(user, inv.business_unit_id)
    if inv.status != "draft":
        raise HTTPException(400, "Only draft invoices can be cancelled")
    inv.status = "cancelled"
    db.commit()
    return serialize_invoice(db, inv)


def _bucket(days: int) -> str:
    if days <= 0:
        return "current"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


@router.get("/aging")
def aging_report(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AR aging (unpaid invoices by days past due) + AP aging (received,
    unpaid POs by days since receipt)."""
    if user.role == "CEO":
        business_unit_id = user.business_unit_id
    today_d = date.today()
    ar_buckets = {k: Decimal("0") for k in ("current", "1-30", "31-60", "61-90", "90+")}
    ar_items = []
    q = db.query(SalesInvoice).filter(SalesInvoice.status.in_(("sent", "partial")))
    if business_unit_id:
        q = q.filter(SalesInvoice.business_unit_id == business_unit_id)
    for inv in q.all():
        a = invoice_amounts(db, inv)
        if a["balance"] <= 0:
            continue
        days = (today_d - inv.due_date).days if inv.due_date else 0
        b = _bucket(days)
        ar_buckets[b] += a["balance"]
        ar_items.append({"invoice_no": inv.invoice_no, "customer": inv.customer.name if inv.customer else None,
                         "bu_name": inv.business_unit.name if inv.business_unit else None,
                         "due_date": inv.due_date.isoformat() if inv.due_date else None,
                         "days_overdue": max(days, 0), "bucket": b, "balance": float(a["balance"])})
    ap_buckets = {k: Decimal("0") for k in ("current", "1-30", "31-60", "61-90", "90+")}
    ap_items = []
    qp = db.query(PurchaseOrder).filter(PurchaseOrder.status == "received")
    if business_unit_id:
        qp = qp.filter(PurchaseOrder.business_unit_id == business_unit_id)
    for po in qp.all():
        total = po_total(po)
        ref = po.received_at.date() if po.received_at else po.order_date
        days = (today_d - ref).days
        b = _bucket(days)
        ap_buckets[b] += total
        ap_items.append({"po_no": po.po_no, "supplier": po.supplier.name if po.supplier else None,
                         "bu_name": po.business_unit.name if po.business_unit else None,
                         "received_date": ref.isoformat(), "days": max(days, 0),
                         "bucket": b, "balance": float(total)})
    return {
        "ar": {"buckets": {k: float(v) for k, v in ar_buckets.items()},
               "total": float(sum(ar_buckets.values(), Decimal("0"))), "items": ar_items},
        "ap": {"buckets": {k: float(v) for k, v in ap_buckets.items()},
               "total": float(sum(ap_buckets.values(), Decimal("0"))), "items": ap_items},
    }


# ================================================================== HR & payroll (ERP Phase 5)

def serialize_employee(e: Employee) -> dict:
    return {
        "id": e.id, "business_unit_id": e.business_unit_id, "name": e.name,
        "position": e.position, "phone": e.phone,
        "base_salary": float(e.base_salary or 0),
        "joined_date": e.joined_date.isoformat() if e.joined_date else None,
        "is_active": e.is_active,
    }


class EmployeeIn(BaseModel):
    business_unit_id: int
    name: str = Field(min_length=1, max_length=150)
    position: Optional[str] = None
    phone: Optional[str] = None
    base_salary: float = 0
    joined_date: Optional[date] = None


@router.get("/employees")
def list_employees(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CEO":
        business_unit_id = user.business_unit_id
    q = db.query(Employee).filter(Employee.is_active == True)  # noqa: E712
    if business_unit_id:
        q = q.filter(Employee.business_unit_id == business_unit_id)
    return {"items": [serialize_employee(e) for e in q.order_by(Employee.name).all()]}


@router.post("/employees")
def create_employee(payload: EmployeeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_scope(user, payload.business_unit_id)
    e = Employee(**payload.model_dump(), created_by_id=user.id)
    db.add(e)
    db.commit()
    db.refresh(e)
    return serialize_employee(e)


@router.delete("/employees/{employee_id}")
def delete_employee(employee_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.query(Employee).filter(Employee.id == employee_id).first()
    if not e:
        raise HTTPException(404, "Employee not found")
    bu_scope(user, e.business_unit_id)
    e.is_active = False
    db.commit()
    return {"ok": True}


def serialize_leave(db: Session, lv: LeaveRequest) -> dict:
    days = (lv.end_date - lv.start_date).days + 1 if lv.start_date and lv.end_date else 0
    return {
        "id": lv.id, "business_unit_id": lv.business_unit_id, "employee_id": lv.employee_id,
        "employee_name": lv.employee.name if lv.employee else None,
        "leave_type": lv.leave_type,
        "start_date": lv.start_date.isoformat() if lv.start_date else None,
        "end_date": lv.end_date.isoformat() if lv.end_date else None,
        "days": days, "reason": lv.reason, "status": lv.status,
    }


class LeaveIn(BaseModel):
    employee_id: int
    leave_type: str = "annual"
    start_date: date
    end_date: date
    reason: Optional[str] = None


@router.get("/leave-requests")
def list_leave(business_unit_id: Optional[int] = None, status: Optional[str] = None,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CEO":
        business_unit_id = user.business_unit_id
    q = db.query(LeaveRequest)
    if business_unit_id:
        q = q.filter(LeaveRequest.business_unit_id == business_unit_id)
    if status:
        q = q.filter(LeaveRequest.status == status)
    rows = q.order_by(LeaveRequest.start_date.desc()).limit(200).all()
    return {"items": [serialize_leave(db, lv) for lv in rows]}


@router.post("/leave-requests")
def create_leave(payload: LeaveIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_active == True).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    bu_scope(user, emp.business_unit_id)
    if payload.end_date < payload.start_date:
        raise HTTPException(400, "End date is before start date")
    lv = LeaveRequest(business_unit_id=emp.business_unit_id, **payload.model_dump())
    db.add(lv)
    db.commit()
    db.refresh(lv)
    return serialize_leave(db, lv)


class LeaveReview(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")


@router.post("/leave-requests/{leave_id}/review")
def review_leave(leave_id: int, payload: LeaveReview, user: User = Depends(require_roles("Chairman", "HO", "CEO")), db: Session = Depends(get_db)):
    lv = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if not lv:
        raise HTTPException(404, "Leave request not found")
    bu_scope(user, lv.business_unit_id)
    if lv.status != "pending":
        raise HTTPException(400, f"Leave already {lv.status}")
    lv.status = payload.decision
    lv.reviewed_by_id = user.id
    lv.reviewed_at = datetime.utcnow()
    db.commit()
    return serialize_leave(db, lv)


def serialize_payroll(db: Session, run: PayrollRun) -> dict:
    lines = []
    totals = {"gross": Decimal("0"), "deductions": Decimal("0"), "net": Decimal("0"),
              "advance_deductions": Decimal("0")}
    for l in run.lines:
        gross = Decimal(str(l.base_salary or 0)) + Decimal(str(l.allowances or 0))
        adv_ded = Decimal(str(getattr(l, "advance_deduction", 0) or 0))
        # Displayed deductions include the staff-advance repayment portion.
        all_deductions = Decimal(str(l.deductions or 0)) + adv_ded
        net = gross - all_deductions
        totals["gross"] += gross
        totals["deductions"] += all_deductions
        totals["advance_deductions"] += adv_ded
        totals["net"] += net
        lines.append({
            "id": l.id, "employee_id": l.employee_id,
            "employee_name": l.employee.name if l.employee else None,
            "base_salary": float(l.base_salary or 0), "allowances": float(l.allowances or 0),
            "deductions": float(all_deductions),
            "manual_deductions": float(l.deductions or 0),
            "advance_deduction": float(adv_ded),
            "gross": float(gross), "net": float(net),
        })
    return {
        "id": run.id, "business_unit_id": run.business_unit_id,
        "bu_name": run.business_unit.name if run.business_unit else None,
        "period": run.period, "status": run.status,
        "paid_at": run.paid_at.isoformat() if run.paid_at else None,
        "lines": lines,
        "total_gross": float(totals["gross"]), "total_deductions": float(totals["deductions"]),
        "total_advance_deductions": float(totals["advance_deductions"]),
        "total_net": float(totals["net"]),
    }


class PayrollCreate(BaseModel):
    business_unit_id: int
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("/payroll-runs")
def list_payroll(business_unit_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CEO":
        business_unit_id = user.business_unit_id
    q = db.query(PayrollRun)
    if business_unit_id:
        q = q.filter(PayrollRun.business_unit_id == business_unit_id)
    rows = q.order_by(PayrollRun.period.desc()).limit(50).all()
    return {"items": [serialize_payroll(db, r) for r in rows]}


@router.post("/payroll-runs")
def create_payroll(payload: PayrollCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Generate a draft run from the BU's active employees + base salaries."""
    bu_scope(user, payload.business_unit_id)
    existing = db.query(PayrollRun).filter(
        PayrollRun.business_unit_id == payload.business_unit_id,
        PayrollRun.period == payload.period).first()
    if existing:
        raise HTTPException(400, f"Payroll for {payload.period} already exists")
    employees = db.query(Employee).filter(
        Employee.business_unit_id == payload.business_unit_id,
        Employee.is_active == True).all()  # noqa: E712
    if not employees:
        raise HTTPException(400, "No active employees in this BU")
    run = PayrollRun(business_unit_id=payload.business_unit_id, period=payload.period,
                     status="draft", created_by_id=user.id)
    db.add(run)
    db.flush()
    for emp in employees:
        db.add(PayrollLine(run_id=run.id, employee_id=emp.id, base_salary=emp.base_salary))
    db.commit()
    db.refresh(run)
    return serialize_payroll(db, run)


class PayrollLineUpdate(BaseModel):
    allowances: Optional[float] = None
    deductions: Optional[float] = None


@router.patch("/payroll-runs/{run_id}/lines/{line_id}")
def update_payroll_line(run_id: int, line_id: int, payload: PayrollLineUpdate,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.query(PayrollRun).filter(PayrollRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Payroll run not found")
    bu_scope(user, run.business_unit_id)
    if run.status != "draft":
        raise HTTPException(400, "Only draft runs can be edited")
    line = db.query(PayrollLine).filter(PayrollLine.id == line_id, PayrollLine.run_id == run_id).first()
    if not line:
        raise HTTPException(404, "Payroll line not found")
    if payload.allowances is not None:
        line.allowances = Decimal(str(payload.allowances))
    if payload.deductions is not None:
        line.deductions = Decimal(str(payload.deductions))
    db.commit()
    db.refresh(run)
    return serialize_payroll(db, run)


@router.post("/payroll-runs/{run_id}/approve")
def approve_payroll(run_id: int, user: User = Depends(require_roles("Chairman", "HO")), db: Session = Depends(get_db)):
    run = db.query(PayrollRun).filter(PayrollRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Payroll run not found")
    if run.status != "draft":
        raise HTTPException(400, f"Run is {run.status}")
    run.status = "approved"
    run.approved_by_id = user.id
    db.commit()
    db.refresh(run)
    return serialize_payroll(db, run)


@router.post("/payroll-runs/{run_id}/pay")
def pay_payroll(run_id: int, user: User = Depends(require_roles("Chairman", "HO")), db: Session = Depends(get_db)):
    run = db.query(PayrollRun).filter(PayrollRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Payroll run not found")
    if run.status != "approved":
        raise HTTPException(400, "Payroll must be approved before paying")
    # ERP completion: staff-advance repayments are applied HERE, at pay
    # time — the safest hook. A run can only transition approved -> paid
    # once, so a deduction can never double-apply, and there is no un-pay
    # path that would need to restore balances. Each line collects
    # min(monthly_deduction, remaining) per open advance, capped so the
    # net pay never goes below zero.
    for line in run.lines:
        if Decimal(str(getattr(line, "advance_deduction", 0) or 0)) > 0:
            continue
        gross = Decimal(str(line.base_salary or 0)) + Decimal(str(line.allowances or 0))
        capacity = gross - Decimal(str(line.deductions or 0))
        if capacity <= 0:
            continue
        advances = (db.query(StaffAdvance)
                    .filter(StaffAdvance.employee_id == line.employee_id,
                            StaffAdvance.status == "approved",
                            StaffAdvance.remaining_amount > 0)
                    .order_by(StaffAdvance.advance_date, StaffAdvance.id).all())
        collected = Decimal("0")
        for adv in advances:
            if collected >= capacity:
                break
            take = min(Decimal(str(adv.monthly_deduction or 0)),
                       Decimal(str(adv.remaining_amount or 0)),
                       capacity - collected)
            if take <= 0:
                continue
            adv.remaining_amount = Decimal(str(adv.remaining_amount)) - take
            if adv.remaining_amount <= 0:
                adv.remaining_amount = Decimal("0")
                adv.status = "settled"
            collected += take
        line.advance_deduction = collected
    db.flush()
    totals = serialize_payroll(db, run)
    run.status = "paid"
    run.paid_at = datetime.utcnow()
    db.commit()
    try:
        from src.routers.accounting import account_by_code, create_entry
        bu = run.business_unit_id
        sal = account_by_code(db, bu, "6200")
        cash = account_by_code(db, bu, "1110")
        if sal and cash:
            y, m = run.period.split("-")
            lines = [{"account_id": sal.id, "debit": Decimal(str(totals["total_gross"])), "credit": 0},
                     {"account_id": cash.id, "debit": 0, "credit": Decimal(str(totals["total_net"]))}]
            if totals["total_deductions"] > 0:
                # The advance-repayment portion credits 1240 (reducing the
                # receivable); the rest credits 2120 as before.
                adv_total = Decimal(str(totals.get("total_advance_deductions") or 0))
                other_ded = Decimal(str(totals["total_deductions"])) - adv_total
                if other_ded > 0:
                    acc = account_by_code(db, bu, "2120")
                    if acc:
                        lines.append({"account_id": acc.id, "debit": 0, "credit": other_ded})
                if adv_total > 0:
                    from src.routers.accounting import _ensure_advance_account
                    _ensure_advance_account(db, bu)
                    adv_acc = account_by_code(db, bu, "1240")
                    if adv_acc:
                        lines.append({"account_id": adv_acc.id, "debit": 0, "credit": adv_total})
            create_entry(db, bu, date(int(y), int(m), 1), f"Payroll {run.period} — {run.business_unit.name if run.business_unit else ''}",
                         "payroll", user.id, lines, source_id=run.id, post=True)
            db.commit()
    except Exception:
        db.rollback()
    db.refresh(run)
    return serialize_payroll(db, run)


# ------------------------------------------------------- D10/D11: payslip & customer statement

def _pdf_safe(t) -> str:
    return str(t if t is not None else "").encode("latin-1", errors="replace").decode("latin-1")


@router.get("/payroll-runs/{run_id}/lines/{line_id}/payslip")
def payslip_pdf(run_id: int, line_id: int, user: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from fpdf import FPDF

    run = db.query(PayrollRun).filter(PayrollRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Payroll run not found")
    line = db.query(PayrollLine).filter(PayrollLine.id == line_id,
                                        PayrollLine.run_id == run_id).first()
    if not line or not line.employee:
        raise HTTPException(404, "Payslip line not found")
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == run.business_unit_id).first()
    emp = line.employee
    pdf = FPDF(orientation="P", unit="mm", format="A5")
    pdf.add_page()
    pdf.set_font("helvetica", "B", 15)
    pdf.cell(0, 9, "PAYSLIP", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 6, _pdf_safe(f"{bu.name if bu else ''} — {run.period}"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)
    pdf.set_font("helvetica", "", 10)
    rows = [
        ("Employee", emp.name), ("Position", emp.position or "-"),
        ("Period", run.period),
        ("", ""),
        ("Base salary", f"Ks {float(line.base_salary):,.0f}"),
        ("Allowances", f"Ks {float(line.allowances):,.0f}"),
        ("Gross pay", f"Ks {float(line.base_salary) + float(line.allowances):,.0f}"),
        ("Deductions", f"Ks {float(line.deductions):,.0f}"),
        ("NET PAY", f"Ks {float(line.base_salary) + float(line.allowances) - float(line.deductions):,.0f}"),
    ]
    for label, value in rows:
        pdf.set_font("helvetica", "B" if label == "NET PAY" else "", 10)
        pdf.cell(55, 7, _pdf_safe(label))
        pdf.cell(0, 7, _pdf_safe(value), new_x="LMARGIN", new_y="NEXT")
    buf = BytesIO(pdf.output())
    return StreamingResponse(buf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="payslip_{run.period}_{emp.name.replace(" ", "_")}.pdf"'})


@router.get("/sales-customers/{customer_id}/statement")
def customer_statement(customer_id: int, format: str = "pdf",
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    cust = db.query(Customer).filter(Customer.id == customer_id).first()
    if not cust:
        raise HTTPException(404, "Customer not found")
    if user.role == "CEO" and cust.business_unit_id != user.business_unit_id:
        raise HTTPException(403, "Not your customer")
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == cust.business_unit_id).first()
    invs = db.query(SalesInvoice).filter(SalesInvoice.customer_id == customer_id).order_by(
        SalesInvoice.invoice_date, SalesInvoice.id).all()
    rows = []
    total_billed = Decimal("0")
    total_paid = Decimal("0")
    for inv in invs:
        a = invoice_amounts(db, inv)
        total_billed += a["total"]
        total_paid += a["paid"]
        overdue = (inv.status in ("sent", "partial") and inv.due_date is not None
                   and inv.due_date < date.today() and a["balance"] > 0)
        rows.append({
            "date": inv.invoice_date.isoformat(), "invoice_no": inv.invoice_no,
            "status": "overdue" if overdue else inv.status, "total": float(a["total"]), "paid": float(a["paid"]),
            "balance": float(a["balance"]),
            "currency": getattr(inv, "currency", "MMK") or "MMK",
        })
    balance = total_billed - total_paid
    if format == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Statement"
        ws.append([f"Statement of Account — {cust.name}"])
        ws.append([bu.name if bu else ""])
        ws.append([])
        ws.append(["Date", "Invoice", "Status", "Total", "Paid", "Balance", "Currency"])
        for r in rows:
            ws.append([r["date"], r["invoice_no"], r["status"], r["total"], r["paid"], r["balance"], r["currency"]])
        ws.append([])
        ws.append(["", "", "TOTALS", float(total_billed), float(total_paid), float(balance), ""])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f'attachment; filename="statement_{cust.name.replace(" ", "_")}.xlsx"'})
    from fpdf import FPDF
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 9, "STATEMENT OF ACCOUNT", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 6, _pdf_safe(f"{cust.name} — {bu.name if bu else ''}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _pdf_safe(f"As of {date.today().isoformat()}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("helvetica", "B", 9)
    widths = [24, 30, 22, 34, 34, 34, 12]
    for i, h in enumerate(["Date", "Invoice", "Status", "Total", "Paid", "Balance", "Cur"]):
        pdf.cell(widths[i], 7, h, border=1)
    pdf.ln()
    pdf.set_font("helvetica", "", 9)
    for r in rows:
        vals = [r["date"], r["invoice_no"], r["status"], f'{r["total"]:,.0f}', f'{r["paid"]:,.0f}', f'{r["balance"]:,.0f}', r["currency"]]
        for i, v in enumerate(vals):
            pdf.cell(widths[i], 6.5, _pdf_safe(v), border=1)
        pdf.ln()
    pdf.ln(2)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(0, 7, _pdf_safe(f"Total billed: {float(total_billed):,.0f}   Paid: {float(total_paid):,.0f}   BALANCE DUE: {float(balance):,.0f}"),
             new_x="LMARGIN", new_y="NEXT")
    buf = BytesIO(pdf.output())
    return StreamingResponse(buf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="statement_{cust.name.replace(" ", "_")}.pdf"'})


# ================================================================== POS counter sales (ERP completion)

def _pos_amounts(sale: PosSale) -> dict:
    subtotal = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_price)) for l in sale.lines), Decimal("0"))
    tax = (subtotal * Decimal(str(sale.tax_rate or 0)) / Decimal("100")).quantize(Decimal("0.01"))
    cogs = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_cost or 0)) for l in sale.lines), Decimal("0"))
    return {"subtotal": subtotal, "tax": tax, "total": subtotal + tax, "cogs": cogs}


def serialize_pos_sale(db: Session, sale: PosSale) -> dict:
    a = _pos_amounts(sale)
    return {
        "id": sale.id, "business_unit_id": sale.business_unit_id,
        "bu_name": sale.business_unit.name if sale.business_unit else None,
        "customer_id": sale.customer_id,
        "customer_name": sale.customer.name if sale.customer else sale.customer_name,
        "payment_method": sale.payment_method, "tax_rate": float(sale.tax_rate or 0),
        "status": sale.status,
        "sale_date": sale.sale_date.isoformat() if sale.sale_date else None,
        "subtotal": float(a["subtotal"]), "tax_amount": float(a["tax"]),
        "total": float(a["total"]), "cogs": float(a["cogs"]),
        "gross_profit": float(a["subtotal"] - a["cogs"]),
        "lines": [{"id": l.id, "inventory_item_id": l.inventory_item_id,
                   "description": l.description, "quantity": float(l.quantity),
                   "unit_price": float(l.unit_price), "unit_cost": float(l.unit_cost or 0),
                   "serial_nos": _serial_list(l.serial_nos)} for l in sale.lines],
        "created_at": sale.created_at.isoformat() if sale.created_at else None,
    }


class PosLineIn(BaseModel):
    inventory_item_id: int
    quantity: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    serial_nos: Optional[list[str]] = None


class PosSaleIn(BaseModel):
    business_unit_id: int
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    payment_method: str = "cash"  # cash/bank
    tax_rate: float = 0
    sale_date: Optional[date] = None
    lines: list[PosLineIn] = Field(min_length=1)


def _post_pos_journals(db: Session, sale: PosSale, user_id: int) -> None:
    """Sale: Dr 1110/1120 (by payment method) / Cr 4100 net / Cr 2130 tax.
    COGS: Dr 5100 / Cr 1140 at moving-average cost. Best-effort, like the
    PO/invoice journal helpers — a missing CoA never blocks a sale."""
    try:
        from src.routers.accounting import account_by_code, create_entry
        a = _pos_amounts(sale)
        bu = sale.business_unit_id
        when = sale.sale_date or date.today()
        if a["total"] > 0:
            cash = account_by_code(db, bu, "1120" if sale.payment_method == "bank" else "1110")
            sales_acc = account_by_code(db, bu, "4100")
            tax_acc = account_by_code(db, bu, "2130")
            if cash and sales_acc:
                lines = [{"account_id": cash.id, "debit": a["total"], "credit": 0},
                         {"account_id": sales_acc.id, "debit": 0, "credit": a["subtotal"]}]
                if a["tax"] > 0 and tax_acc:
                    lines.append({"account_id": tax_acc.id, "debit": 0, "credit": a["tax"]})
                create_entry(db, bu, when, f"POS sale #{sale.id}", "pos_sale",
                             user_id, lines, source_id=sale.id, post=True)
        if a["cogs"] > 0:
            inv_acc = account_by_code(db, bu, "1140")
            cogs_acc = account_by_code(db, bu, "5100")
            if inv_acc and cogs_acc:
                create_entry(db, bu, when, f"POS sale #{sale.id} — COGS", "pos_sale",
                             user_id,
                             [{"account_id": cogs_acc.id, "debit": a["cogs"], "credit": 0},
                              {"account_id": inv_acc.id, "debit": 0, "credit": a["cogs"]}],
                             source_id=sale.id, post=True)
        db.commit()
    except Exception:
        db.rollback()


@router.post("/pos-sales")
def create_pos_sale(payload: PosSaleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if payload.payment_method not in ("cash", "bank"):
        raise HTTPException(400, "payment_method must be cash or bank")
    if payload.customer_id is not None:
        cust = db.query(Customer).filter(Customer.id == payload.customer_id).first()
        if not cust or cust.business_unit_id != bu_id:
            raise HTTPException(404, "Customer not found in this BU")
    import json as _json
    sale = PosSale(
        business_unit_id=bu_id, customer_id=payload.customer_id,
        customer_name=payload.customer_name, payment_method=payload.payment_method,
        tax_rate=Decimal(str(payload.tax_rate)), status="completed",
        sale_date=payload.sale_date or date.today(), sold_by_id=user.id,
    )
    db.add(sale)
    db.flush()
    # Validate stock for every line before moving anything.
    items = {}
    for ln in payload.lines:
        item = db.query(InventoryItem).filter(
            InventoryItem.id == ln.inventory_item_id,
            InventoryItem.business_unit_id == bu_id,
            InventoryItem.is_active == True).first()  # noqa: E712
        if not item:
            raise HTTPException(404, f"Inventory item {ln.inventory_item_id} not found in this BU")
        qty = Decimal(str(ln.quantity))
        if Decimal(str(item.current_stock or 0)) < qty:
            raise HTTPException(400, f"Not enough stock for {item.name}: {float(item.current_stock or 0):g} available")
        items[ln.inventory_item_id] = item
    for ln in payload.lines:
        item = items[ln.inventory_item_id]
        qty = Decimal(str(ln.quantity))
        db.add(PosSaleLine(
            sale_id=sale.id, inventory_item_id=item.id, description=item.name,
            quantity=qty, unit_price=Decimal(str(ln.unit_price)),
            unit_cost=Decimal(str(item.unit_cost or 0)),
            serial_nos=_json.dumps(ln.serial_nos) if ln.serial_nos else None,
        ))
        item.current_stock = Decimal(str(item.current_stock or 0)) - qty
        item.updated_at = datetime.utcnow()
        db.add(StockMovement(item_id=item.id, movement_type="out", quantity=qty,
                             reason=f"POS sale #{sale.id}", recorded_by_id=user.id))
        if getattr(item, "requires_expiry", False):
            from src.routers.modules import consume_batches_fefo
            consume_batches_fefo(db, item, qty)
    db.flush()
    _mark_line_serials_sold(db, bu_id, sale.lines, pos_sale_id=sale.id)
    db.commit()
    _post_pos_journals(db, sale, user.id)
    db.refresh(sale)
    return serialize_pos_sale(db, sale)


@router.get("/pos-sales")
def list_pos_sales(business_unit_id: Optional[int] = None, sale_date: Optional[date] = None,
                   status: Optional[str] = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(PosSale)
    if bu_id is not None:
        q = q.filter(PosSale.business_unit_id == bu_id)
    if sale_date:
        q = q.filter(PosSale.sale_date == sale_date)
    if status:
        q = q.filter(PosSale.status == status)
    rows = q.order_by(PosSale.id.desc()).limit(200).all()
    return {"items": [serialize_pos_sale(db, s) for s in rows]}


@router.get("/pos-sales/daily-summary")
def pos_daily_summary(business_unit_id: Optional[int] = None,
                      on: Optional[date] = Query(None, alias="date"),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Today's (or `on` date's) POS totals: by payment method, COGS and
    gross profit. Voided sales are excluded."""
    bu_id = bu_scope(user, business_unit_id)
    day = on or date.today()
    q = db.query(PosSale).filter(PosSale.sale_date == day, PosSale.status == "completed")
    if bu_id is not None:
        q = q.filter(PosSale.business_unit_id == bu_id)
    by_method: dict = {}
    subtotal = tax = total = cogs = Decimal("0")
    count = 0
    for s in q.all():
        a = _pos_amounts(s)
        count += 1
        subtotal += a["subtotal"]
        tax += a["tax"]
        total += a["total"]
        cogs += a["cogs"]
        m = by_method.setdefault(s.payment_method, {"count": 0, "total": Decimal("0")})
        m["count"] += 1
        m["total"] += a["total"]
    return {
        "date": day.isoformat(), "business_unit_id": bu_id, "sale_count": count,
        "subtotal": float(subtotal), "tax": float(tax), "total": float(total),
        "cogs": float(cogs), "gross_profit": float(subtotal - cogs),
        "by_payment_method": {k: {"count": v["count"], "total": float(v["total"])}
                              for k, v in by_method.items()},
    }


@router.post("/pos-sales/{sale_id}/void")
def void_pos_sale(sale_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sale = db.query(PosSale).filter(PosSale.id == sale_id).first()
    if not sale:
        raise HTTPException(404, "POS sale not found")
    bu_scope(user, sale.business_unit_id)
    if sale.status != "completed":
        raise HTTPException(400, f"Cannot void a sale in status {sale.status}")
    for ln in sale.lines:
        item = db.query(InventoryItem).filter(InventoryItem.id == ln.inventory_item_id).first()
        if item:
            qty = Decimal(str(ln.quantity))
            item.current_stock = Decimal(str(item.current_stock or 0)) + qty
            item.updated_at = datetime.utcnow()
            db.add(StockMovement(item_id=item.id, movement_type="in", quantity=qty,
                                 reason=f"POS sale #{sale.id} voided", recorded_by_id=user.id))
            if getattr(item, "requires_expiry", False):
                restock_batches(db, item, qty, batch_no="POS-VOID")
    for s in db.query(ItemSerial).filter(ItemSerial.pos_sale_id == sale.id,
                                         ItemSerial.status == "sold").all():
        s.status = "in_stock"
        s.pos_sale_id = None
        s.sold_at = None
    # Reverse both POS journals (sale + COGS) with proper reversal entries.
    from src.routers.accounting import _reverse_entry
    entries = db.query(JournalEntry).filter(
        JournalEntry.source == "pos_sale", JournalEntry.source_id == sale.id,
        JournalEntry.status == "posted", JournalEntry.reversed_by_id == None).all()  # noqa: E711
    for e in entries:
        _reverse_entry(db, e, user.id)
    sale.status = "void"
    sale.voided_at = datetime.utcnow()
    db.commit()
    db.refresh(sale)
    return serialize_pos_sale(db, sale)


# ================================================================== quotations (ERP completion)

def quotation_amounts(q: Quotation) -> dict:
    subtotal = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_price)) for l in q.lines), Decimal("0"))
    tax = (subtotal * Decimal(str(q.tax_rate or 0)) / Decimal("100")).quantize(Decimal("0.01"))
    return {"subtotal": subtotal, "tax": tax, "total": subtotal + tax}


def serialize_quotation(db: Session, q: Quotation) -> dict:
    a = quotation_amounts(q)
    return {
        "id": q.id, "quote_no": q.quote_no, "business_unit_id": q.business_unit_id,
        "bu_name": q.business_unit.name if q.business_unit else None,
        "customer_id": q.customer_id,
        "customer_name": q.customer.name if q.customer else None,
        "status": q.status,
        "quote_date": q.quote_date.isoformat() if q.quote_date else None,
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
        "tax_rate": float(q.tax_rate or 0), "notes": q.notes,
        "currency": q.currency or "MMK", "exchange_rate": float(q.exchange_rate or 1),
        "project_id": q.project_id,
        "converted_invoice_id": q.converted_invoice_id,
        "subtotal": float(a["subtotal"]), "tax_amount": float(a["tax"]), "total": float(a["total"]),
        "lines": [{"id": l.id, "description": l.description, "quantity": float(l.quantity),
                   "unit_price": float(l.unit_price),
                   "inventory_item_id": l.inventory_item_id,
                   "line_total": float(Decimal(str(l.quantity)) * Decimal(str(l.unit_price)))} for l in q.lines],
    }


class QuotationIn(BaseModel):
    business_unit_id: int
    customer_id: int
    quote_date: date
    valid_until: Optional[date] = None
    tax_rate: float = 5
    notes: Optional[str] = None
    currency: str = "MMK"
    exchange_rate: Optional[float] = None
    project_id: Optional[int] = None
    lines: list[InvoiceLineIn] = Field(min_length=1)


def _fill_quotation_lines(db: Session, q: Quotation, lines) -> None:
    for ln in lines:
        db.add(QuotationLine(quotation_id=q.id, description=ln.description,
                             quantity=Decimal(str(ln.quantity)),
                             unit_price=Decimal(str(ln.unit_price)),
                             inventory_item_id=ln.inventory_item_id))


@router.get("/quotations")
def list_quotations(business_unit_id: Optional[int] = None, status: Optional[str] = None,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "CEO":
        business_unit_id = user.business_unit_id
    q = db.query(Quotation)
    if business_unit_id:
        q = q.filter(Quotation.business_unit_id == business_unit_id)
    if status:
        q = q.filter(Quotation.status == status)
    rows = q.order_by(Quotation.quote_date.desc(), Quotation.id.desc()).limit(200).all()
    return {"items": [serialize_quotation(db, r) for r in rows]}


@router.post("/quotations")
def create_quotation(payload: QuotationIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_scope(user, payload.business_unit_id)
    cust = db.query(Customer).filter(Customer.id == payload.customer_id, Customer.is_active == True).first()  # noqa: E712
    if not cust or cust.business_unit_id != payload.business_unit_id:
        raise HTTPException(404, "Customer not found in this BU")
    count = db.query(Quotation).filter(Quotation.business_unit_id == payload.business_unit_id).count()
    from src.routers.accounting import latest_rate
    q = Quotation(
        business_unit_id=payload.business_unit_id, customer_id=payload.customer_id,
        quote_no=f"QT-{payload.business_unit_id}-{count + 1:04d}", status="draft",
        quote_date=payload.quote_date, valid_until=payload.valid_until,
        tax_rate=Decimal(str(payload.tax_rate)), notes=payload.notes, created_by_id=user.id,
        currency=(payload.currency or "MMK").upper(),
        exchange_rate=(Decimal(str(payload.exchange_rate)) if payload.exchange_rate
                       else latest_rate(db, payload.currency)),
        project_id=payload.project_id,
    )
    db.add(q)
    db.flush()
    _fill_quotation_lines(db, q, payload.lines)
    db.commit()
    db.refresh(q)
    return serialize_quotation(db, q)


class QuotationUpdate(BaseModel):
    quote_date: Optional[date] = None
    valid_until: Optional[date] = None
    tax_rate: Optional[float] = None
    notes: Optional[str] = None
    project_id: Optional[int] = None
    lines: Optional[list[InvoiceLineIn]] = None


@router.patch("/quotations/{quotation_id}")
def update_quotation(quotation_id: int, payload: QuotationUpdate,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Quotation).filter(Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(404, "Quotation not found")
    bu_scope(user, q.business_unit_id)
    if q.status != "draft":
        raise HTTPException(400, "Only draft quotations can be edited")
    if payload.quote_date is not None:
        q.quote_date = payload.quote_date
    if payload.valid_until is not None:
        q.valid_until = payload.valid_until
    if payload.tax_rate is not None:
        q.tax_rate = Decimal(str(payload.tax_rate))
    if payload.notes is not None:
        q.notes = payload.notes
    if payload.project_id is not None:
        q.project_id = payload.project_id
    if payload.lines is not None:
        if not payload.lines:
            raise HTTPException(400, "A quotation needs at least one line")
        for old in list(q.lines):
            db.delete(old)
        db.flush()
        _fill_quotation_lines(db, q, payload.lines)
    db.commit()
    db.refresh(q)
    return serialize_quotation(db, q)


class QuotationStatusIn(BaseModel):
    status: str = Field(pattern="^(sent|accepted|rejected)$")


@router.post("/quotations/{quotation_id}/status")
def set_quotation_status(quotation_id: int, payload: QuotationStatusIn,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Quotation).filter(Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(404, "Quotation not found")
    bu_scope(user, q.business_unit_id)
    allowed = {"draft": {"sent"}, "sent": {"accepted", "rejected"},
               "accepted": set(), "rejected": set(), "converted": set()}
    if payload.status not in allowed.get(q.status, set()):
        raise HTTPException(400, f"Cannot move a {q.status} quotation to {payload.status}")
    q.status = payload.status
    db.commit()
    db.refresh(q)
    return serialize_quotation(db, q)


@router.post("/quotations/{quotation_id}/convert")
def convert_quotation(quotation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a draft SalesInvoice from this quotation. No journal is
    posted — that happens when the invoice is sent, as usual."""
    q = db.query(Quotation).filter(Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(404, "Quotation not found")
    bu_scope(user, q.business_unit_id)
    if q.status in ("rejected", "converted"):
        raise HTTPException(400, f"Cannot convert a {q.status} quotation")
    count = db.query(SalesInvoice).filter(SalesInvoice.business_unit_id == q.business_unit_id).count()
    inv = SalesInvoice(
        business_unit_id=q.business_unit_id, customer_id=q.customer_id,
        invoice_no=f"INV-{q.business_unit_id}-{count + 1:04d}", status="draft",
        invoice_date=date.today(), due_date=q.valid_until,
        tax_rate=q.tax_rate, notes=q.notes, created_by_id=user.id,
        currency=q.currency, exchange_rate=q.exchange_rate, project_id=q.project_id,
    )
    db.add(inv)
    db.flush()
    for ln in q.lines:
        db.add(SalesInvoiceLine(invoice_id=inv.id, description=ln.description,
                                quantity=ln.quantity, unit_price=ln.unit_price,
                                inventory_item_id=ln.inventory_item_id))
    q.status = "converted"
    q.converted_invoice_id = inv.id
    db.commit()
    db.refresh(inv)
    return serialize_invoice(db, inv)


# ================================================================== returns / credit & debit notes (ERP completion)

def _returned_qty_for_invoice_line(db: Session, invoice_line_id: int) -> Decimal:
    total = (db.query(func.coalesce(func.sum(SalesReturnLine.quantity), 0))
             .join(SalesReturn, SalesReturnLine.return_id == SalesReturn.id)
             .filter(SalesReturnLine.invoice_line_id == invoice_line_id,
                     SalesReturn.status == "posted").scalar())
    return Decimal(str(total or 0))


def serialize_sales_return(db: Session, r: SalesReturn) -> dict:
    net = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_price)) for l in r.lines), Decimal("0"))
    rate = Decimal(str(r.invoice.tax_rate or 0)) if r.invoice else Decimal("0")
    tax = (net * rate / Decimal("100")).quantize(Decimal("0.01"))
    return {
        "id": r.id, "return_no": r.return_no, "business_unit_id": r.business_unit_id,
        "invoice_id": r.invoice_id,
        "invoice_no": r.invoice.invoice_no if r.invoice else None,
        "status": r.status,
        "return_date": r.return_date.isoformat() if r.return_date else None,
        "reason": r.reason, "subtotal": float(net), "tax_amount": float(tax),
        "total": float(net + tax),
        "lines": [{"id": l.id, "invoice_line_id": l.invoice_line_id,
                   "quantity": float(l.quantity), "unit_price": float(l.unit_price)} for l in r.lines],
    }


class SalesReturnLineIn(BaseModel):
    invoice_line_id: int
    quantity: float = Field(gt=0)
    serial_nos: Optional[list[str]] = None


class SalesReturnIn(BaseModel):
    invoice_id: int
    reason: Optional[str] = None
    return_date: Optional[date] = None
    lines: list[SalesReturnLineIn] = Field(min_length=1)


def _post_sales_return_journal(db: Session, r: SalesReturn, user_id: int) -> None:
    """Dr 4100 net / Dr 2130 tax / Cr 1130 gross (MMK at the invoice rate)."""
    try:
        from src.routers.accounting import account_by_code, create_entry
        inv = r.invoice
        fx = Decimal(str(getattr(inv, "exchange_rate", 1) or 1))
        net = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_price)) for l in r.lines), Decimal("0"))
        tax = (net * Decimal(str(inv.tax_rate or 0)) / Decimal("100")).quantize(Decimal("0.01"))
        sales_acc = account_by_code(db, r.business_unit_id, "4100")
        tax_acc = account_by_code(db, r.business_unit_id, "2130")
        ar = account_by_code(db, r.business_unit_id, "1130")
        if not (sales_acc and ar):
            return
        lines = [{"account_id": sales_acc.id, "debit": net * fx, "credit": 0}]
        if tax > 0 and tax_acc:
            lines.append({"account_id": tax_acc.id, "debit": tax * fx, "credit": 0})
        lines.append({"account_id": ar.id, "debit": 0, "credit": (net + tax) * fx})
        create_entry(db, r.business_unit_id, r.return_date or date.today(),
                     f"Sales return {r.return_no} — {inv.invoice_no}", "sales_return",
                     user_id, lines, source_id=r.id, post=True)
        db.commit()
    except Exception:
        db.rollback()


@router.post("/sales-returns")
def create_sales_return(payload: SalesReturnIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inv = _invoice_or_404(db, payload.invoice_id)
    bu_scope(user, inv.business_unit_id)
    if inv.status not in ("sent", "partial", "paid"):
        raise HTTPException(400, f"Cannot return items against a {inv.status} invoice")
    line_by_id = {l.id: l for l in inv.lines}
    for ln in payload.lines:
        il = line_by_id.get(ln.invoice_line_id)
        if not il:
            raise HTTPException(400, f"Invoice line {ln.invoice_line_id} does not belong to this invoice")
        already = _returned_qty_for_invoice_line(db, il.id)
        if Decimal(str(ln.quantity)) > Decimal(str(il.quantity)) - already:
            raise HTTPException(400, f"Return quantity for '{il.description}' exceeds the invoiced "
                                     f"quantity minus previous returns")
    count = db.query(SalesReturn).filter(SalesReturn.business_unit_id == inv.business_unit_id).count()
    r = SalesReturn(business_unit_id=inv.business_unit_id, invoice_id=inv.id,
                    return_no=f"SR-{inv.business_unit_id}-{count + 1:04d}", status="posted",
                    return_date=payload.return_date or date.today(),
                    reason=payload.reason, created_by_id=user.id)
    db.add(r)
    db.flush()
    for ln in payload.lines:
        il = line_by_id[ln.invoice_line_id]
        qty = Decimal(str(ln.quantity))
        db.add(SalesReturnLine(return_id=r.id, invoice_line_id=il.id,
                               quantity=qty, unit_price=il.unit_price))
        if il.inventory_item_id:
            item = db.query(InventoryItem).filter(InventoryItem.id == il.inventory_item_id).first()
            if item:
                item.current_stock = Decimal(str(item.current_stock or 0)) + qty
                item.updated_at = datetime.utcnow()
                db.add(StockMovement(item_id=item.id, movement_type="in", quantity=qty,
                                     reason=f"Sales return {r.return_no} ({inv.invoice_no})",
                                     recorded_by_id=user.id))
        for sn in (ln.serial_nos or []):
            s = db.query(ItemSerial).filter(
                ItemSerial.business_unit_id == inv.business_unit_id,
                ItemSerial.serial_no == sn).first()
            if s and s.sales_invoice_id == inv.id:
                s.status = "returned"
    db.commit()
    _post_sales_return_journal(db, r, user.id)
    # Recompute the invoice status from the reduced balance.
    a = invoice_amounts(db, inv)
    if inv.status in ("sent", "partial", "paid"):
        if a["balance"] <= 0:
            inv.status = "paid"
        elif a["paid"] > 0:
            inv.status = "partial"
        db.commit()
    db.refresh(r)
    return serialize_sales_return(db, r)


@router.get("/sales-returns")
def list_sales_returns(business_unit_id: Optional[int] = None, invoice_id: Optional[int] = None,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(SalesReturn)
    if bu_id is not None:
        q = q.filter(SalesReturn.business_unit_id == bu_id)
    if invoice_id:
        q = q.filter(SalesReturn.invoice_id == invoice_id)
    rows = q.order_by(SalesReturn.id.desc()).limit(200).all()
    return {"items": [serialize_sales_return(db, r) for r in rows]}


def _returned_qty_for_po_line(db: Session, po_line_id: int) -> Decimal:
    total = (db.query(func.coalesce(func.sum(PurchaseReturnLine.quantity), 0))
             .join(PurchaseReturn, PurchaseReturnLine.return_id == PurchaseReturn.id)
             .filter(PurchaseReturnLine.po_line_id == po_line_id,
                     PurchaseReturn.status == "posted").scalar())
    return Decimal(str(total or 0))


def serialize_purchase_return(db: Session, r: PurchaseReturn) -> dict:
    total = sum((Decimal(str(l.quantity)) * Decimal(str(l.unit_cost)) for l in r.lines), Decimal("0"))
    return {
        "id": r.id, "return_no": r.return_no, "business_unit_id": r.business_unit_id,
        "po_id": r.po_id, "po_no": r.po.po_no if r.po else None,
        "status": r.status,
        "return_date": r.return_date.isoformat() if r.return_date else None,
        "reason": r.reason, "total": float(total),
        "lines": [{"id": l.id, "po_line_id": l.po_line_id,
                   "quantity": float(l.quantity), "unit_cost": float(l.unit_cost)} for l in r.lines],
    }


class PurchaseReturnLineIn(BaseModel):
    po_line_id: int
    quantity: float = Field(gt=0)


class PurchaseReturnIn(BaseModel):
    po_id: int
    reason: Optional[str] = None
    return_date: Optional[date] = None
    lines: list[PurchaseReturnLineIn] = Field(min_length=1)


@router.post("/purchase-returns")
def create_purchase_return(payload: PurchaseReturnIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    po = _po_or_404(db, payload.po_id)
    bu_scope(user, po.business_unit_id)
    if po.status not in ("received", "paid"):
        raise HTTPException(400, f"Cannot return items against a PO in status {po.status}")
    line_by_id = {l.id: l for l in po.lines}
    items = {}
    for ln in payload.lines:
        pl = line_by_id.get(ln.po_line_id)
        if not pl:
            raise HTTPException(400, f"PO line {ln.po_line_id} does not belong to this PO")
        already = _returned_qty_for_po_line(db, pl.id)
        qty = Decimal(str(ln.quantity))
        if qty > Decimal(str(pl.quantity)) - already:
            raise HTTPException(400, f"Return quantity for '{pl.item_name}' exceeds the received "
                                     f"quantity minus previous returns")
        if pl.inventory_item_id:
            item = db.query(InventoryItem).filter(InventoryItem.id == pl.inventory_item_id).first()
            if item is not None:
                if Decimal(str(item.current_stock or 0)) < qty:
                    raise HTTPException(400, f"Not enough stock of {item.name} to return")
                items[pl.id] = item
    count = db.query(PurchaseReturn).filter(PurchaseReturn.business_unit_id == po.business_unit_id).count()
    r = PurchaseReturn(business_unit_id=po.business_unit_id, po_id=po.id,
                       return_no=f"PR-{po.business_unit_id}-{count + 1:04d}", status="posted",
                       return_date=payload.return_date or date.today(),
                       reason=payload.reason, created_by_id=user.id)
    db.add(r)
    db.flush()
    total_cost = Decimal("0")
    fx = Decimal(str(getattr(po, "exchange_rate", 1) or 1))
    for ln in payload.lines:
        pl = line_by_id[ln.po_line_id]
        qty = Decimal(str(ln.quantity))
        db.add(PurchaseReturnLine(return_id=r.id, po_line_id=pl.id,
                                  quantity=qty, unit_cost=pl.unit_cost))
        total_cost += qty * Decimal(str(pl.unit_cost)) * fx
        item = items.get(pl.id)
        if item is not None:
            item.current_stock = Decimal(str(item.current_stock or 0)) - qty
            item.updated_at = datetime.utcnow()
            db.add(StockMovement(item_id=item.id, movement_type="out", quantity=qty,
                                 reason=f"Purchase return {r.return_no} ({po.po_no})",
                                 recorded_by_id=user.id))
    db.commit()
    try:
        from src.routers.accounting import account_by_code, create_entry
        ap = account_by_code(db, po.business_unit_id, "2110")
        inv_acc = account_by_code(db, po.business_unit_id, "1140")
        if ap and inv_acc and total_cost > 0:
            create_entry(db, po.business_unit_id, r.return_date or date.today(),
                         f"Purchase return {r.return_no} — {po.po_no}", "purchase_return",
                         user.id,
                         [{"account_id": ap.id, "debit": total_cost, "credit": 0},
                          {"account_id": inv_acc.id, "debit": 0, "credit": total_cost}],
                         source_id=r.id, post=True)
            db.commit()
    except Exception:
        db.rollback()
    db.refresh(r)
    return serialize_purchase_return(db, r)


@router.get("/purchase-returns")
def list_purchase_returns(business_unit_id: Optional[int] = None, po_id: Optional[int] = None,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(PurchaseReturn)
    if bu_id is not None:
        q = q.filter(PurchaseReturn.business_unit_id == bu_id)
    if po_id:
        q = q.filter(PurchaseReturn.po_id == po_id)
    rows = q.order_by(PurchaseReturn.id.desc()).limit(200).all()
    return {"items": [serialize_purchase_return(db, r) for r in rows]}


# ================================================================== invoice PDF + delivery note (ERP completion)

@router.get("/sales-invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from fpdf import FPDF

    inv = _invoice_or_404(db, invoice_id)
    bu_scope(user, inv.business_unit_id)
    bu = inv.business_unit
    cust = inv.customer
    a = invoice_amounts(db, inv)
    cur = getattr(inv, "currency", "MMK") or "MMK"
    fx = Decimal(str(getattr(inv, "exchange_rate", 1) or 1))
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    W = pdf.w - 20
    pdf.set_font("helvetica", "B", 16)
    pdf.cell(W, 9, _pdf_safe(bu.name if bu else ""), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "TAX INVOICE", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.cell(W, 6, _pdf_safe(f"Invoice No: {inv.invoice_no}    Date: {inv.invoice_date.isoformat() if inv.invoice_date else ''}"
                             + (f"    Due: {inv.due_date.isoformat()}" if inv.due_date else "")),
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 6, _pdf_safe(f"Bill To: {cust.name if cust else ''}"), new_x="LMARGIN", new_y="NEXT")
    if cust and cust.address:
        pdf.cell(W, 6, _pdf_safe(cust.address), new_x="LMARGIN", new_y="NEXT")
    if cust and (cust.phone or cust.contact_person):
        pdf.cell(W, 6, _pdf_safe("  ".join(x for x in [cust.contact_person or "", cust.phone or ""] if x)),
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    widths = [92, 20, 36, 42]
    pdf.set_font("helvetica", "B", 9)
    for i, h in enumerate(["Description", "Qty", "Unit Price", "Amount"]):
        pdf.cell(widths[i], 7, h, border=1)
    pdf.ln()
    pdf.set_font("helvetica", "", 9)
    for l in inv.lines:
        amt = Decimal(str(l.quantity)) * Decimal(str(l.unit_price))
        vals = [_pdf_safe(l.description[:48]), f"{float(l.quantity):g}",
                f"{float(l.unit_price):,.2f}", f"{float(amt):,.2f}"]
        for i, v in enumerate(vals):
            pdf.cell(widths[i], 6.5, v, border=1)
        pdf.ln()
    pdf.ln(2)
    pdf.set_font("helvetica", "", 10)
    rows = [("Subtotal", a["subtotal"]), (f"Commercial tax ({float(inv.tax_rate or 0):g}%)", a["tax"]),
            ("TOTAL", a["total"]), ("Paid", a["paid"])]
    if a.get("returns"):
        rows.append(("Less: returns (credit notes)", -a["returns"]))
    rows.append(("BALANCE DUE", a["balance"]))
    for label, v in rows:
        pdf.set_font("helvetica", "B" if label in ("TOTAL", "BALANCE DUE") else "", 10)
        pdf.cell(130, 6.5, _pdf_safe(label))
        pdf.cell(0, 6.5, f"{cur} {float(v):,.2f}", new_x="LMARGIN", new_y="NEXT")
    if cur != "MMK":
        pdf.set_font("helvetica", "", 9)
        pdf.cell(W, 6, _pdf_safe(f"Foreign currency invoice: amounts in {cur} at Ks {float(fx):,.2f} per 1 {cur} "
                                 f"(total = Ks {float(a['total'] * fx):,.0f})."),
                 new_x="LMARGIN", new_y="NEXT")
    if inv.notes:
        pdf.ln(2)
        pdf.multi_cell(W, 6, _pdf_safe(f"Notes: {inv.notes}"))
    buf = BytesIO(pdf.output())
    return StreamingResponse(buf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{inv.invoice_no}.pdf"'})


@router.get("/sales-invoices/{invoice_id}/delivery-note")
def delivery_note_pdf(invoice_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delivery note for an invoice: items + quantities only, no prices."""
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from fpdf import FPDF

    inv = _invoice_or_404(db, invoice_id)
    bu_scope(user, inv.business_unit_id)
    bu = inv.business_unit
    cust = inv.customer
    dn_no = inv.invoice_no.replace("INV", "DN", 1) if inv.invoice_no else f"DN-{inv.id}"
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    W = pdf.w - 20
    pdf.set_font("helvetica", "B", 16)
    pdf.cell(W, 9, _pdf_safe(bu.name if bu else ""), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "DELIVERY NOTE", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.cell(W, 6, _pdf_safe(f"DN No: {dn_no}    Invoice: {inv.invoice_no}    Date: {date.today().isoformat()}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 6, _pdf_safe(f"Deliver To: {cust.name if cust else ''}"), new_x="LMARGIN", new_y="NEXT")
    if cust and cust.address:
        pdf.cell(W, 6, _pdf_safe(cust.address), new_x="LMARGIN", new_y="NEXT")
    if cust and cust.phone:
        pdf.cell(W, 6, _pdf_safe(f"Phone: {cust.phone}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    widths = [120, 30, 40]
    pdf.set_font("helvetica", "B", 9)
    for i, h in enumerate(["Description", "Qty", "Unit"]):
        pdf.cell(widths[i], 7, h, border=1)
    pdf.ln()
    pdf.set_font("helvetica", "", 9)
    for l in inv.lines:
        unit = l.inventory_item.unit if l.inventory_item else "pcs"
        vals = [_pdf_safe(l.description[:60]), f"{float(l.quantity):g}", _pdf_safe(unit or "pcs")]
        for i, v in enumerate(vals):
            pdf.cell(widths[i], 6.5, v, border=1)
        pdf.ln()
    pdf.ln(14)
    pdf.set_font("helvetica", "", 10)
    pdf.cell(95, 7, "Delivered by: ______________________")
    pdf.cell(0, 7, "Received by: ______________________", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(95, 7, "Date: ______________________")
    pdf.cell(0, 7, "Signature: ______________________", new_x="LMARGIN", new_y="NEXT")
    buf = BytesIO(pdf.output())
    return StreamingResponse(buf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{dn_no}.pdf"'})


# ================================================================== serial / IMEI tracking (ERP completion)

def serialize_serial(db: Session, s: ItemSerial) -> dict:
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == s.business_unit_id).first()
    return {
        "id": s.id, "business_unit_id": s.business_unit_id,
        "bu_name": bu.name if bu else None,
        "inventory_item_id": s.inventory_item_id,
        "item_name": s.inventory_item.name if s.inventory_item else None,
        "serial_no": s.serial_no, "status": s.status,
        "purchase_order_id": s.purchase_order_id,
        "sales_invoice_id": s.sales_invoice_id, "pos_sale_id": s.pos_sale_id,
        "warranty_until": s.warranty_until.isoformat() if s.warranty_until else None,
        "sold_at": s.sold_at.isoformat() if s.sold_at else None,
    }


class SerialAddIn(BaseModel):
    serial_nos: list[str] = Field(min_length=1)
    warranty_until: Optional[date] = None
    purchase_order_id: Optional[int] = None


@router.post("/inventory/{item_id}/serials")
def add_serials(item_id: int, payload: SerialAddIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Inventory item not found")
    bu_scope(user, item.business_unit_id)
    cleaned = [str(s).strip() for s in payload.serial_nos if str(s).strip()]
    if not cleaned:
        raise HTTPException(400, "No serial numbers given")
    if len(set(cleaned)) != len(cleaned):
        raise HTTPException(400, "Duplicate serial numbers in the request")
    existing = {s.serial_no for s in db.query(ItemSerial).filter(
        ItemSerial.business_unit_id == item.business_unit_id,
        ItemSerial.inventory_item_id == item.id,
        ItemSerial.serial_no.in_(cleaned)).all()}
    if existing:
        raise HTTPException(400, f"Serial numbers already registered for this item: {', '.join(sorted(existing))}")
    rows = []
    for sn in cleaned:
        row = ItemSerial(business_unit_id=item.business_unit_id, inventory_item_id=item.id,
                         serial_no=sn, status="in_stock",
                         warranty_until=payload.warranty_until,
                         purchase_order_id=payload.purchase_order_id)
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return {"items": [serialize_serial(db, r) for r in rows], "count": len(rows)}


@router.get("/inventory/{item_id}/serials")
def list_serials(item_id: int, status: Optional[str] = None,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Inventory item not found")
    bu_scope(user, item.business_unit_id)
    q = db.query(ItemSerial).filter(ItemSerial.inventory_item_id == item_id)
    if status:
        q = q.filter(ItemSerial.status == status)
    rows = q.order_by(ItemSerial.serial_no).all()
    return {"items": [serialize_serial(db, s) for s in rows]}


@router.get("/serials/lookup")
def lookup_serial(serial_no: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Find a serial / IMEI across all items and BUs."""
    q = db.query(ItemSerial).filter(ItemSerial.serial_no == serial_no.strip())
    if user.role == "CEO":
        q = q.filter(ItemSerial.business_unit_id == user.business_unit_id)
    rows = q.all()
    return {"items": [serialize_serial(db, s) for s in rows], "count": len(rows)}


class SerialStatusIn(BaseModel):
    status: str = Field(pattern="^(in_stock|sold|returned|defective)$")


@router.post("/serials/{serial_id}/status")
def set_serial_status(serial_id: int, payload: SerialStatusIn,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.query(ItemSerial).filter(ItemSerial.id == serial_id).first()
    if not s:
        raise HTTPException(404, "Serial not found")
    bu_scope(user, s.business_unit_id)
    s.status = payload.status
    if payload.status == "in_stock":
        s.sales_invoice_id = None
        s.pos_sale_id = None
        s.sold_at = None
    db.commit()
    db.refresh(s)
    return serialize_serial(db, s)


# ================================================================== staff advances & loans (ERP completion)

def serialize_advance(db: Session, adv: StaffAdvance) -> dict:
    return {
        "id": adv.id, "employee_id": adv.employee_id,
        "employee_name": adv.employee.name if adv.employee else None,
        "business_unit_id": adv.business_unit_id,
        "bu_name": adv.business_unit.name if adv.business_unit else None,
        "amount": float(adv.amount or 0),
        "remaining_amount": float(adv.remaining_amount or 0),
        "monthly_deduction": float(adv.monthly_deduction or 0),
        "advance_date": adv.advance_date.isoformat() if adv.advance_date else None,
        "reason": adv.reason, "status": adv.status,
        "journal_entry_id": adv.journal_entry_id,
        "created_at": adv.created_at.isoformat() if adv.created_at else None,
    }


class AdvanceIn(BaseModel):
    employee_id: int
    amount: float = Field(gt=0)
    monthly_deduction: float = Field(gt=0)
    advance_date: Optional[date] = None
    reason: Optional[str] = None


@router.get("/staff-advances")
def list_advances(business_unit_id: Optional[int] = None, status: Optional[str] = None,
                  employee_id: Optional[int] = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, business_unit_id)
    q = db.query(StaffAdvance)
    if bu_id is not None:
        q = q.filter(StaffAdvance.business_unit_id == bu_id)
    if status:
        q = q.filter(StaffAdvance.status == status)
    if employee_id:
        q = q.filter(StaffAdvance.employee_id == employee_id)
    rows = q.order_by(StaffAdvance.id.desc()).limit(200).all()
    return {"items": [serialize_advance(db, a) for a in rows]}


@router.post("/staff-advances")
def create_advance(payload: AdvanceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_active == True).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    bu_scope(user, emp.business_unit_id)
    adv = StaffAdvance(
        employee_id=emp.id, business_unit_id=emp.business_unit_id,
        amount=Decimal(str(payload.amount)), remaining_amount=Decimal(str(payload.amount)),
        monthly_deduction=Decimal(str(payload.monthly_deduction)),
        advance_date=payload.advance_date or date.today(), reason=payload.reason,
        status="pending", requested_by_id=user.id,
    )
    db.add(adv)
    db.commit()
    db.refresh(adv)
    return serialize_advance(db, adv)


@router.post("/staff-advances/{advance_id}/approve")
def approve_advance(advance_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Approve + disburse: Dr 1240 Staff Advances / Cr 1110 Cash in the
    employee's BU (account 1240 is created on demand for older BUs)."""
    adv = db.query(StaffAdvance).filter(StaffAdvance.id == advance_id).first()
    if not adv:
        raise HTTPException(404, "Staff advance not found")
    if adv.status != "pending":
        raise HTTPException(400, f"Advance is {adv.status}")
    adv.status = "approved"
    adv.approved_by_id = user.id
    adv.approved_at = datetime.utcnow()
    try:
        from src.routers.accounting import (
            _ensure_advance_account, account_by_code, create_entry,
        )
        _ensure_advance_account(db, adv.business_unit_id)
        adv_acc = account_by_code(db, adv.business_unit_id, "1240")
        cash = account_by_code(db, adv.business_unit_id, "1110")
        if adv_acc and cash:
            e = create_entry(db, adv.business_unit_id, date.today(),
                             f"Staff advance — {adv.employee.name if adv.employee else ''}",
                             "staff_advance", user.id,
                             [{"account_id": adv_acc.id, "debit": adv.amount, "credit": 0},
                              {"account_id": cash.id, "debit": 0, "credit": adv.amount}],
                             source_id=adv.id, post=True)
            db.flush()
            adv.journal_entry_id = e.id
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()  # no CoA yet — approval stands, journal skipped (payroll pattern)
        adv = db.query(StaffAdvance).filter(StaffAdvance.id == advance_id).first()
        adv.status = "approved"
        adv.approved_by_id = user.id
        adv.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(adv)
    return serialize_advance(db, adv)


@router.post("/staff-advances/{advance_id}/reject")
def reject_advance(advance_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    adv = db.query(StaffAdvance).filter(StaffAdvance.id == advance_id).first()
    if not adv:
        raise HTTPException(404, "Staff advance not found")
    if adv.status != "pending":
        raise HTTPException(400, f"Advance is {adv.status}")
    adv.status = "rejected"
    adv.approved_by_id = user.id
    adv.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(adv)
    return serialize_advance(db, adv)


# ================================================================== attendance + leave balances (ERP completion)

ATTENDANCE_STATUSES = ("present", "absent", "late", "leave", "half_day")
# Yearly leave entitlements by type (days). "annual" is the earned-leave
# type and "sick" the medical type in the existing LeaveRequest values.
LEAVE_ENTITLEMENTS = {"annual": 10, "earned": 10, "casual": 10, "sick": 30, "medical": 30, "unpaid": 0}


def serialize_attendance(db: Session, r: AttendanceRecord) -> dict:
    return {
        "id": r.id, "business_unit_id": r.business_unit_id,
        "employee_id": r.employee_id,
        "employee_name": r.employee.name if r.employee else None,
        "date": r.record_date.isoformat() if r.record_date else None,
        "status": r.status, "note": r.note,
    }


class AttendanceRecordIn(BaseModel):
    employee_id: int
    record_date: Optional[date] = None
    date_: Optional[date] = Field(None, alias="date")
    status: str
    note: Optional[str] = None

    model_config = {"populate_by_name": True}


class AttendanceBulkIn(BaseModel):
    records: list[AttendanceRecordIn] = Field(min_length=1)


@router.post("/attendance/bulk")
def upsert_attendance(payload: AttendanceBulkIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Upsert one attendance record per (employee, date)."""
    saved = []
    for rec in payload.records:
        day = rec.record_date or rec.date_
        if day is None:
            raise HTTPException(400, "Each record needs a date")
        if rec.status not in ATTENDANCE_STATUSES:
            raise HTTPException(400, f"status must be one of {ATTENDANCE_STATUSES}")
        emp = db.query(Employee).filter(Employee.id == rec.employee_id).first()
        if not emp:
            raise HTTPException(404, f"Employee {rec.employee_id} not found")
        bu_scope(user, emp.business_unit_id)
        row = db.query(AttendanceRecord).filter(
            AttendanceRecord.employee_id == emp.id,
            AttendanceRecord.record_date == day).first()
        if row is None:
            row = AttendanceRecord(employee_id=emp.id, business_unit_id=emp.business_unit_id,
                                   record_date=day)
            db.add(row)
        row.status = rec.status
        row.note = rec.note
        row.business_unit_id = emp.business_unit_id
        row.recorded_by_id = user.id
        saved.append(row)
    db.commit()
    for row in saved:
        db.refresh(row)
    return {"saved": len(saved), "items": [serialize_attendance(db, r) for r in saved]}


@router.get("/attendance")
def list_attendance(business_unit_id: Optional[int] = None, month: Optional[str] = None,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One month of attendance records + per-employee status counts."""
    bu_id = bu_scope(user, business_unit_id)
    month = validate_period(month)
    if month:
        y, m = (int(x) for x in month.split("-"))
    else:
        today = date.today()
        y, m = today.year, today.month
    import calendar as _cal
    start = date(y, m, 1)
    end = date(y, m, _cal.monthrange(y, m)[1])
    q = db.query(AttendanceRecord).filter(AttendanceRecord.record_date >= start,
                                          AttendanceRecord.record_date <= end)
    if bu_id is not None:
        q = q.filter(AttendanceRecord.business_unit_id == bu_id)
    rows = q.order_by(AttendanceRecord.record_date, AttendanceRecord.employee_id).all()
    summary: dict = {}
    for r in rows:
        s = summary.setdefault(r.employee_id, {
            "employee_id": r.employee_id,
            "employee_name": r.employee.name if r.employee else None,
            **{st: 0 for st in ATTENDANCE_STATUSES},
        })
        s[r.status] = s.get(r.status, 0) + 1
    return {"month": f"{y:04d}-{m:02d}",
            "items": [serialize_attendance(db, r) for r in rows],
            "summary": list(summary.values())}


@router.get("/leave-balances")
def leave_balances(business_unit_id: Optional[int] = None, year: Optional[int] = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Computed leave balances: entitlement − approved days used in the
    year (requests overlapping the year count their overlapping days)."""
    bu_id = bu_scope(user, business_unit_id)
    yr = year or date.today().year
    jan1, dec31 = date(yr, 1, 1), date(yr, 12, 31)
    q = db.query(Employee).filter(Employee.is_active == True)  # noqa: E712
    if bu_id is not None:
        q = q.filter(Employee.business_unit_id == bu_id)
    out = []
    for emp in q.order_by(Employee.name).all():
        approved = db.query(LeaveRequest).filter(
            LeaveRequest.employee_id == emp.id, LeaveRequest.status == "approved",
            LeaveRequest.start_date <= dec31, LeaveRequest.end_date >= jan1).all()
        used: dict = {}
        for lv in approved:
            s = max(lv.start_date, jan1)
            e = min(lv.end_date, dec31)
            days = (e - s).days + 1
            if days > 0:
                used[lv.leave_type] = used.get(lv.leave_type, 0) + days
        types = {}
        for t, ent in LEAVE_ENTITLEMENTS.items():
            if t in ("earned", "medical"):
                continue  # aliases of annual/sick — reported under the stored type names
            u = used.get(t, 0)
            types[t] = {"entitlement": ent, "used": u,
                        "remaining": (ent - u) if t != "unpaid" else None}
        out.append({"employee_id": emp.id, "employee_name": emp.name,
                    "business_unit_id": emp.business_unit_id, "year": yr,
                    "balances": types})
    return {"year": yr, "items": out}
