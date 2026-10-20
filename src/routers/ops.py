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
    LicenseDocument,
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
    _maybe_notify_low_stock(db, item)
    db.commit()
    db.refresh(item)
    return serialize_inventory_item(item)


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
    if month:
        try:
            y, m = month.split("-")
            month_start = date(int(y), int(m), 1)
        except Exception:
            raise HTTPException(400, "month must be YYYY-MM")
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
