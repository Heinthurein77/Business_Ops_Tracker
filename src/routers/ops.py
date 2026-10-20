"""Operational extensions: action items, notifications, inventory, expense
approvals, staff records, customer metrics, and forecasting.

Access model mirrors the rest of the app:
- CEO: scoped to their own business unit
- Chairman / HO ("admin"): all business units
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import get_current_user, require_roles
from src.database import get_db
from src.models import (
    ActionItem,
    BusinessUnit,
    CustomerMetric,
    DailyFinancial,
    ExpenseRequest,
    InventoryItem,
    Notification,
    StaffRecord,
    StockMovement,
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
    return {"notified": sent}


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
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return serialize_inventory_item(item)


class MovementIn(BaseModel):
    movement_type: str  # in/out/adjustment
    quantity: float
    reason: Optional[str] = None


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
    elif payload.movement_type == "adjustment":
        item.current_stock = qty
    else:
        raise HTTPException(400, "movement_type must be in/out/adjustment")
    item.updated_at = datetime.utcnow()
    db.add(StockMovement(item_id=item.id, movement_type=payload.movement_type,
                         quantity=qty, reason=payload.reason, recorded_by_id=user.id))
    db.commit()
    db.refresh(item)
    return serialize_inventory_item(item)


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


@router.post("/expenses")
def submit_expense(payload: ExpenseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bu_id = bu_scope(user, payload.business_unit_id)
    if bu_id is None:
        raise HTTPException(400, "business_unit_id is required")
    e = ExpenseRequest(
        business_unit_id=bu_id,
        submitted_by_id=user.id,
        title=payload.title.strip(),
        amount=Decimal(str(payload.amount)),
        category=payload.category,
        description=payload.description,
        receipt_url=payload.receipt_url,
        status="pending",
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    # Notify Chairman/HO for approval
    for approver in db.query(User).filter(User.role.in_(["Chairman", "HO"]), User.is_active == True).all():  # noqa: E712
        db.add(Notification(
            user_id=approver.id, type="expense_pending",
            title="Expense awaiting approval",
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
