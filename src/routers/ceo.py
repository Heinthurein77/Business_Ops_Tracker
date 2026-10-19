import threading
import time
from collections import defaultdict, deque
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import require_roles, resolve_redirect_path
from src.database import get_db
from src.models import ActivityLog, BusinessUnit, CeoProfile, DailyFinancial, FinancialNote, HealthStatus, MonthlyTarget, SocialLog, User
from src.r2_storage import is_configured as r2_is_configured
from src.r2_storage import upload_fileobj
from src.schemas import CeoProfileRequest, FinancialNoteRequest, MonthlyTargetRequest
from src.upload_validation import content_matches_extension
from src.utils import (
    BP_DIASTOLIC_RANGE,
    BP_SYSTOLIC_RANGE,
    HEART_RATE_RANGE,
    MAX_NARRATIVE_TEXT_LEN,
    MAX_SHORT_TEXT_LEN,
    SLEEP_HOURS_RANGE,
    STEPS_RANGE,
    build_activity_rows,
    build_ceo_activity_calendar,
    build_financial_rows,
    build_health_rows,
    build_resubmit_audit_detail,
    build_social_rows,
    calculate_age,
    compute_bu_quadrant_summary,
    compute_field_changes,
    compute_monthly_social_counts,
    evaluate_cumulative_health,
    form_decimal,
    form_int,
    form_money,
    form_percent,
    form_text,
    format_local_datetime,
    log_audit,
    parse_bounded_decimal,
    parse_monthly_target_amounts,
    serialize_ceo_profile,
    shift_month,
    today_local,
    todays_submission_events,
    validate_blood_pressure,
)

router = APIRouter(prefix="/api/ceo", tags=["ceo"])

# Sliding-window guard on /api/ceo/submit-report: a real daily report is
# submitted at most a couple of times a day, so this only ever engages
# against double submits or scripted retries — but it protects the DB write
# and the R2 upload from being hammered.
REPORT_SUBMIT_RATE_LIMIT = 5
REPORT_SUBMIT_RATE_WINDOW_SECONDS = 60
_report_submit_timestamps: dict[int, deque] = defaultdict(deque)
# submit_report runs in FastAPI's threadpool (it's a plain `def`), so the
# shared deque bookkeeping below must be serialized.
_report_submit_lock = threading.Lock()

MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "doc", "docx", "xls", "xlsx", "csv", "txt"}

MAX_PROFILE_PHOTO_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_PROFILE_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def _is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    with _report_submit_lock:
        timestamps = _report_submit_timestamps[user_id]
        while timestamps and now - timestamps[0] > REPORT_SUBMIT_RATE_WINDOW_SECONDS:
            timestamps.popleft()
        if len(timestamps) >= REPORT_SUBMIT_RATE_LIMIT:
            return True
        timestamps.append(now)
        return False


def _parse_decimal(raw: Optional[str]) -> Optional[Decimal]:
    """Blank -> None. NaN / Infinity / abs(value) >= 1e13 (the money columns
    are Numeric(16, 2)) / unparsable input -> None too, never a Decimal that
    Postgres would store as NaN (poisoning every SUM) or overflow on. Rounded
    to 2 decimals, the column's precision. See utils.parse_bounded_decimal."""
    return parse_bounded_decimal(raw)


def _parse_bool(raw: Optional[str]) -> bool:
    return (raw or "").strip().lower() in ("true", "1", "yes", "on")


@router.get("/dashboard")
def ceo_dashboard(
    month: str = "",
    user: User = Depends(require_roles("CEO")),
    db: Session = Depends(get_db),
):
    report_date = today_local()

    try:
        calendar_year, calendar_month = (int(part) for part in month.split("-", 1))
        date(calendar_year, calendar_month, 1)  # validates the month is real
        if not (2000 <= calendar_year <= 2100):
            # The calendar grid pads to whole weeks, so a month at the very
            # edge of the supported date range (e.g. 9999-12) would overflow
            # date arithmetic and 500 - clamp to a sane window instead.
            raise ValueError("month out of supported range")
    except (ValueError, TypeError):
        calendar_year, calendar_month = report_date.year, report_date.month

    # Activity / health / social rows are the submitting CEO's own personal
    # data: every read of them below is scoped to `submitted_by == user.id`
    # (not just the business unit), so a newly appointed CEO of this unit
    # never sees their predecessor's history. Financial rows, targets and
    # notes belong to the business and stay unit-scoped.
    calendar_weeks = build_ceo_activity_calendar(
        db, user.business_unit_id, calendar_year, calendar_month, report_date, user.id
    )
    prev_year, prev_month = shift_month(calendar_year, calendar_month, -1)
    next_year, next_month = shift_month(calendar_year, calendar_month, 1)

    financial = (
        db.query(DailyFinancial)
        .filter(DailyFinancial.business_unit_id == user.business_unit_id, DailyFinancial.report_date == report_date)
        .first()
    )
    # 14-day trend for the CEO's own BU — powers the sparklines on their dashboard.
    trend_days = [report_date - timedelta(days=offset) for offset in range(13, -1, -1)]
    trend_rows = (
        db.query(
            DailyFinancial.report_date,
            DailyFinancial.revenue,
            DailyFinancial.cash_flow,
            DailyFinancial.receivable,
            DailyFinancial.payable,
        )
        .filter(
            DailyFinancial.business_unit_id == user.business_unit_id,
            DailyFinancial.report_date >= trend_days[0],
            DailyFinancial.report_date <= trend_days[-1],
        )
        .all()
    )
    trend_by_date = {
        r: {
            "revenue": float(rv) if rv is not None else 0.0,
            "cash_flow": float(cf) if cf is not None else 0.0,
            "receivable": float(rc) if rc is not None else 0.0,
            "payable": float(py) if py is not None else 0.0,
        }
        for r, rv, cf, rc, py in trend_rows
    }
    financial_trend = {
        "labels": [d.strftime("%d %b") for d in trend_days],
        "revenue": [round(trend_by_date.get(d, {}).get("revenue", 0.0), 2) for d in trend_days],
        "cash_flow": [round(trend_by_date.get(d, {}).get("cash_flow", 0.0), 2) for d in trend_days],
        "receivable": [round(trend_by_date.get(d, {}).get("receivable", 0.0), 2) for d in trend_days],
        "payable": [round(trend_by_date.get(d, {}).get("payable", 0.0), 2) for d in trend_days],
    }
    activity = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.business_unit_id == user.business_unit_id,
            ActivityLog.report_date == report_date,
            ActivityLog.submitted_by == user.id,
        )
        .first()
    )
    health = (
        db.query(HealthStatus)
        .filter(
            HealthStatus.business_unit_id == user.business_unit_id,
            HealthStatus.report_date == report_date,
            HealthStatus.submitted_by == user.id,
        )
        .first()
    )
    social = (
        db.query(SocialLog)
        .filter(
            SocialLog.business_unit_id == user.business_unit_id,
            SocialLog.report_date == report_date,
            SocialLog.submitted_by == user.id,
        )
        .first()
    )

    # "Latest ever" (not just today's) versions, needed for the quadrant status
    # computation — same shared helper the Chairman/HO's Business Unit Detail
    # page uses, so a CEO sees exactly the same status for their own unit.
    latest_financial = (
        db.query(DailyFinancial)
        .filter(DailyFinancial.business_unit_id == user.business_unit_id)
        .order_by(DailyFinancial.report_date.desc())
        .first()
    )
    latest_activity = (
        db.query(ActivityLog)
        .filter(ActivityLog.business_unit_id == user.business_unit_id, ActivityLog.submitted_by == user.id)
        .order_by(ActivityLog.report_date.desc())
        .first()
    )
    latest_health = (
        db.query(HealthStatus)
        .filter(HealthStatus.business_unit_id == user.business_unit_id, HealthStatus.submitted_by == user.id)
        .order_by(HealthStatus.report_date.desc())
        .first()
    )
    latest_social = (
        db.query(SocialLog)
        .filter(SocialLog.business_unit_id == user.business_unit_id, SocialLog.submitted_by == user.id)
        .order_by(SocialLog.report_date.desc())
        .first()
    )
    current_month_start = date(report_date.year, report_date.month, 1)
    monthly_target = (
        db.query(MonthlyTarget)
        .filter(MonthlyTarget.business_unit_id == user.business_unit_id, MonthlyTarget.target_month == current_month_start)
        .first()
    )
    # Single-step Cumulative Financial Health: Month-to-Date Cumulative Net
    # Revenue (Sum(Revenue) + Sum(Other Amount), day 1 of the month through
    # today) vs the CEO's monthly revenue target, pro-rated to today's
    # day-of-month. See evaluate_cumulative_financial_health for the full rules.
    cumulative_performance = evaluate_cumulative_health(db, user.business_unit_id, current_month_start, report_date)
    budget_status = cumulative_performance["status"].lower() if cumulative_performance else "grey"

    mtd_totals_row = (
        db.query(
            func.coalesce(func.sum(DailyFinancial.revenue), 0),
            func.coalesce(func.sum(DailyFinancial.other_amount), 0),
        )
        .filter(
            DailyFinancial.business_unit_id == user.business_unit_id,
            DailyFinancial.report_date >= current_month_start,
            DailyFinancial.report_date <= report_date,
        )
        .first()
    )
    mtd_revenue = float(mtd_totals_row[0] or 0)
    mtd_other_amount = float(mtd_totals_row[1] or 0)

    quadrant_summary = compute_bu_quadrant_summary(
        latest_financial, financial, latest_activity, latest_social, latest_health, budget_status=budget_status
    )

    business_unit = db.query(BusinessUnit).filter(BusinessUnit.id == user.business_unit_id).first()
    ceo_profile_row = db.query(CeoProfile).filter(CeoProfile.user_id == user.id).first()

    recent_financials = (
        db.query(DailyFinancial)
        .filter(DailyFinancial.business_unit_id == user.business_unit_id)
        .order_by(DailyFinancial.report_date.desc())
        .limit(30)
        .all()
    )
    recent_activity_logs = (
        db.query(ActivityLog)
        .filter(ActivityLog.business_unit_id == user.business_unit_id, ActivityLog.submitted_by == user.id)
        .order_by(ActivityLog.report_date.desc())
        .limit(90)
        .all()
    )

    # Day-by-Day Review — lets the CEO step back through their own past
    # submissions across all 4 areas, same as the Chairman/HO's Business Unit
    # Detail page (built from the same shared row builders, so the figures
    # and statuses a CEO sees for their own unit never drift from what an
    # executive would see).
    # Limit is 62 (2 months' worth) rather than 30 — build_financial_rows'
    # per-row Cumulative Net Revenue needs every day of a row's own calendar
    # month present in this list to sum correctly.
    financial_history = (
        db.query(DailyFinancial)
        .filter(DailyFinancial.business_unit_id == user.business_unit_id)
        .order_by(DailyFinancial.report_date.desc())
        .limit(62)
        .all()
    )
    activity_history = (
        db.query(ActivityLog)
        .filter(ActivityLog.business_unit_id == user.business_unit_id, ActivityLog.submitted_by == user.id)
        .order_by(ActivityLog.report_date.desc())
        .limit(30)
        .all()
    )
    health_history = (
        db.query(HealthStatus)
        .filter(HealthStatus.business_unit_id == user.business_unit_id, HealthStatus.submitted_by == user.id)
        .order_by(HealthStatus.report_date.desc())
        .limit(30)
        .all()
    )
    social_history = (
        db.query(SocialLog)
        .filter(SocialLog.business_unit_id == user.business_unit_id, SocialLog.submitted_by == user.id)
        .order_by(SocialLog.report_date.desc())
        .limit(30)
        .all()
    )
    target_months = {date(f.report_date.year, f.report_date.month, 1) for f in financial_history}
    targets_by_month = {}
    if target_months:
        for t in (
            db.query(MonthlyTarget)
            .filter(MonthlyTarget.business_unit_id == user.business_unit_id, MonthlyTarget.target_month.in_(target_months))
            .all()
        ):
            targets_by_month[t.target_month] = t

    financial_notes = (
        db.query(FinancialNote)
        .filter(FinancialNote.business_unit_id == user.business_unit_id, FinancialNote.note_date >= current_month_start)
        .order_by(FinancialNote.note_date.desc(), FinancialNote.created_at.desc())
        .all()
    )

    submission_events_today = todays_submission_events(db, user.id, "DAILY_REPORT_SUBMITTED", report_date)
    monthly_social = compute_monthly_social_counts(
        db,
        SocialLog,
        [SocialLog.business_unit_id == user.business_unit_id, SocialLog.submitted_by == user.id],
        current_month_start,
        report_date,
    )

    return {
        "business_unit": {"id": business_unit.id, "code": business_unit.code, "name": business_unit.name} if business_unit else None,
        "submission_events_today": submission_events_today,
        # Same shape as the Executive Dashboard/Operations grid, Business Unit
        # Detail, and the standalone CEO Profiles list (serialize_ceo_profile)
        # so the CEO's own dashboard can show the identical compact profile
        # summary table without a separate page.
        "ceo_profile": serialize_ceo_profile(user, ceo_profile_row, report_date),
        "report_date": report_date.isoformat(),
        "report_date_display": report_date.strftime("%A, %d %B %Y"),
        "financial": {
            "revenue": float(financial.revenue) if financial else None,
            "expenses": float(financial.expenses) if financial else None,
            "net_profit": float(financial.net_profit) if financial else None,
            "cash_in_hand": float(financial.cash_in_hand) if financial else None,
            "cash_flow": float(financial.cash_flow) if financial and financial.cash_flow is not None else None,
            "receivable": float(financial.receivable) if financial and financial.receivable is not None else None,
            "payable": float(financial.payable) if financial and financial.payable is not None else None,
            "other_amount": float(financial.other_amount) if financial and financial.other_amount is not None else None,
            "other_notes": financial.other_notes if financial else None,
        } if financial else None,
        "financial_trend": financial_trend,
        "activity": {
            "meetings_attended": activity.meetings_attended,
            "site_visits": activity.site_visits,
            "key_decisions": activity.key_decisions,
            "next_day_schedule": activity.next_day_schedule,
            "document_url": activity.document_url,
            "gym_percent": activity.gym_percent,
            "swimming_percent": activity.swimming_percent,
            "cycle_percent": activity.cycle_percent,
            "other_notes": activity.other_notes,
            "updated_at": format_local_datetime(activity.updated_at),
        } if activity else None,
        "health": {
            "physical_health": health.physical_health,
            "stress_level": health.stress_level,
            "energy_mood": health.energy_mood,
            "sleep_quality": health.sleep_quality,
            "daily_steps": health.daily_steps,
            "heart_rate": health.heart_rate,
            "sleep_hours": float(health.sleep_hours) if health.sleep_hours is not None else None,
            "bp_systolic": health.bp_systolic,
            "bp_diastolic": health.bp_diastolic,
            "other_notes": health.other_notes,
        } if health else None,
        "social": {
            "event_flag": social.event_flag,
            "meeting_flag": social.meeting_flag,
            "golf_flag": social.golf_flag,
            "other_notes": social.other_notes,
        } if social else None,
        "submitted_at_today": format_local_datetime(activity.updated_at) if activity else None,
        "already_submitted_today": activity is not None,
        "recent_financials": [
            {
                "report_date": f.report_date.strftime("%d %b %Y"),
                "revenue": float(f.revenue) if f.revenue is not None else None,
                "cash_flow": float(f.cash_flow) if f.cash_flow is not None else None,
                "receivable": float(f.receivable) if f.receivable is not None else None,
                "payable": float(f.payable) if f.payable is not None else None,
                "other_amount": float(f.other_amount) if f.other_amount is not None else None,
            }
            for f in recent_financials
        ],
        "report_logs": [
            {
                "report_date": log.report_date.strftime("%d %b %Y"),
                "submitted_at": format_local_datetime(log.updated_at),
                "meetings_attended": log.meetings_attended,
            }
            for log in recent_activity_logs
        ],
        "financial_rows": build_financial_rows(financial_history, targets_by_month),
        "activity_rows": build_activity_rows(activity_history),
        "health_rows": build_health_rows(health_history),
        "social_rows": build_social_rows(social_history),
        "calendar_weeks": calendar_weeks,
        "calendar_month_label": date(calendar_year, calendar_month, 1).strftime("%B %Y"),
        "calendar_is_current_month": (calendar_year, calendar_month) == (report_date.year, report_date.month),
        "prev_month": f"{prev_year:04d}-{prev_month:02d}",
        "next_month": f"{next_year:04d}-{next_month:02d}",
        "current_month": f"{report_date.year:04d}-{report_date.month:02d}",
        "monthly_target": {
            "target_month": monthly_target.target_month.isoformat(),
            "monthly_revenue_target": float(monthly_target.monthly_revenue_target),
            "monthly_cash_flow_target": float(monthly_target.monthly_cash_flow_target),
            "monthly_max_receivable": float(monthly_target.monthly_max_receivable),
            "monthly_max_payable": float(monthly_target.monthly_max_payable),
        } if monthly_target else None,
        "cumulative_performance": cumulative_performance,
        "financial_notes": [
            {"note_date": n.note_date.isoformat(), "note_text": n.note_text, "created_at": format_local_datetime(n.created_at)}
            for n in financial_notes
        ],
        "quadrant": {
            **quadrant_summary,
            # Real actual-vs-CEO-target status. financial_status above already
            # promotes to green when this is green (see compute_bu_quadrant_summary);
            # exposed here too so the UI can show the underlying verdict directly.
            "budget_status": budget_status,
            "mtd_revenue": mtd_revenue,
            "mtd_other_amount": mtd_other_amount,
            "cumulative_net_revenue": mtd_revenue + mtd_other_amount,
            "achievement_pct": cumulative_performance["achievement_pct"] if cumulative_performance else None,
            "variance_amount": cumulative_performance["variance_amount"] if cumulative_performance else None,
            "expected_mtd_target": cumulative_performance["expected_mtd_target"] if cumulative_performance else None,
            "full_monthly_target": cumulative_performance["full_monthly_target"] if cumulative_performance else None,
            "revenue": float(latest_financial.revenue) if latest_financial else None,
            "cash_flow": float(latest_financial.cash_flow) if latest_financial and latest_financial.cash_flow is not None else None,
            "receivable": float(latest_financial.receivable) if latest_financial and latest_financial.receivable is not None else None,
            "payable": float(latest_financial.payable) if latest_financial and latest_financial.payable is not None else None,
            "financial_other_notes": latest_financial.other_notes if latest_financial else None,
            "event_flag": latest_social.event_flag if latest_social else None,
            "meeting_flag": latest_social.meeting_flag if latest_social else None,
            "golf_flag": latest_social.golf_flag if latest_social else None,
            "social_other_notes": latest_social.other_notes if latest_social else None,
            "monthly_event_count": monthly_social["event_count"],
            "monthly_meeting_count": monthly_social["meeting_count"],
            "monthly_golf_count": monthly_social["golf_count"],
            "monthly_days_submitted": monthly_social["days_submitted"],
            "gym_percent": latest_activity.gym_percent if latest_activity else None,
            "swimming_percent": latest_activity.swimming_percent if latest_activity else None,
            "cycle_percent": latest_activity.cycle_percent if latest_activity else None,
            "activity_other_notes": latest_activity.other_notes if latest_activity else None,
            "daily_steps": latest_health.daily_steps if latest_health else None,
            "heart_rate": latest_health.heart_rate if latest_health else None,
            "sleep_hours": float(latest_health.sleep_hours) if latest_health and latest_health.sleep_hours is not None else None,
            "bp_systolic": latest_health.bp_systolic if latest_health else None,
            "bp_diastolic": latest_health.bp_diastolic if latest_health else None,
            "health_other_notes": latest_health.other_notes if latest_health else None,
        },
    }


@router.get("/monthly-target")
def get_monthly_target(
    month: str = "",
    user: User = Depends(require_roles("CEO")),
    db: Session = Depends(get_db),
):
    """Fetches the target already set for the given month (default: current
    month) so the "Set Monthly Target" form can pre-fill with existing values
    instead of always starting blank."""
    today = today_local()
    try:
        year, month_num = (int(part) for part in month.split("-", 1))
        target_month = date(year, month_num, 1)
    except (ValueError, TypeError):
        target_month = date(today.year, today.month, 1)

    target = (
        db.query(MonthlyTarget)
        .filter(MonthlyTarget.business_unit_id == user.business_unit_id, MonthlyTarget.target_month == target_month)
        .first()
    )
    if target is None:
        return {"target": None, "target_month": target_month.isoformat()}
    return {
        "target": {
            "target_month": target.target_month.isoformat(),
            "monthly_revenue_target": float(target.monthly_revenue_target),
            "monthly_cash_flow_target": float(target.monthly_cash_flow_target),
            "monthly_max_receivable": float(target.monthly_max_receivable),
            "monthly_max_payable": float(target.monthly_max_payable),
        },
        "target_month": target_month.isoformat(),
    }


@router.post("/monthly-target")
def set_monthly_target(
    payload: MonthlyTargetRequest,
    request: Request,
    user: User = Depends(require_roles("CEO")),
    db: Session = Depends(get_db),
):
    """Sets this business unit's monthly target for the given month — but only
    once. A CEO who could freely revise the target mid-month could lower it
    after a bad day to dodge a RED/YELLOW status, defeating the whole point
    of a benchmark, so once set it's locked for the rest of that month; only
    Chairman/HO (via the admin override endpoint) can revise it after that."""
    try:
        target_month = date.fromisoformat(payload.target_month)
        if target_month.day != 1:
            target_month = target_month.replace(day=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="target_month must be a valid ISO date")

    # Rejects NaN / Infinity / abs >= 1e13 / negative revenue-receivable-payable
    # targets with a 400 (pydantic's float lets Infinity and huge values through).
    amounts = parse_monthly_target_amounts(payload)

    existing = (
        db.query(MonthlyTarget)
        .filter(MonthlyTarget.business_unit_id == user.business_unit_id, MonthlyTarget.target_month == target_month)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=403,
            detail="This month's target has already been set and is locked — contact Head Office if it needs to change.",
        )

    target = MonthlyTarget(business_unit_id=user.business_unit_id, target_month=target_month)
    target.monthly_revenue_target = amounts["monthly_revenue_target"]
    target.monthly_cash_flow_target = amounts["monthly_cash_flow_target"]
    target.monthly_max_receivable = amounts["monthly_max_receivable"]
    target.monthly_max_payable = amounts["monthly_max_payable"]
    target.set_by = user.id
    db.add(target)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=403, detail="This month's target has already been set and is locked — contact Head Office if it needs to change.")

    log_audit(db, user, "MONTHLY_TARGET_SET", f"month={target_month.isoformat()}", request)

    return {"ok": True, "target_month": target_month.isoformat()}


@router.get("/profile")
def get_profile(user: User = Depends(require_roles("CEO")), db: Session = Depends(get_db)):
    """Fetches the CEO's own mandatory profile — pre-fills the Profile Setup
    form with whatever's already on file (blank fields on first visit)."""
    profile = db.query(CeoProfile).filter(CeoProfile.user_id == user.id).first()
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == user.business_unit_id).first()

    return {
        "is_profile_completed": user.is_profile_completed,
        "full_name": user.full_name,
        "executive_title": profile.executive_title if profile else "Chief Executive Officer",
        "phone_number": profile.phone_number if profile else None,
        "email": profile.email if profile else None,
        "date_of_birth": profile.date_of_birth.isoformat() if profile and profile.date_of_birth else None,
        "age": calculate_age(profile.date_of_birth) if profile and profile.date_of_birth else None,
        "address": profile.address if profile else None,
        "business_details": profile.business_details if profile else None,
        "profile_pic_url": profile.profile_pic_url if profile else None,
        # This app is strictly one CEO per business unit, so this is always a
        # single-element array — kept as an array (rather than one bu_id
        # field) to match how the Admin Dashboard wants to render/filter it.
        "managed_business_ids": [bu.code] if bu else [],
    }


@router.post("/profile")
def set_profile(
    request: Request,
    full_name: str = Form(...),
    executive_title: str = Form("Chief Executive Officer"),
    phone_number: str = Form(...),
    email: Optional[str] = Form(None),
    date_of_birth: str = Form(...),
    address: str = Form(...),
    business_details: Optional[str] = Form(None),
    profile_photo: Optional[UploadFile] = File(None),
    user: User = Depends(require_roles("CEO")),
    db: Session = Depends(get_db),
):
    """Creates or updates the CEO's mandatory profile. Individual Form(...)
    fields (rather than a single embedded Pydantic form model — that combo
    didn't bind correctly here alongside the separate File field) are
    re-assembled into a CeoProfileRequest so Pydantic still does the real
    validation (required fields, date-of-birth bounds) in one place; a
    ValidationError there means the "all required fields filled"
    precondition wasn't met, and is_profile_completed never flips to True.
    The photo itself is a real file upload (multipart), not a client-
    supplied URL — optional, and only replaces the existing photo (if any)
    when a new file is actually sent, so re-saving the rest of the profile
    doesn't clear a previously uploaded picture."""
    # Length / character checks first, on every free-text field, so an
    # oversized or NUL-containing value is a clear 400 (never silently
    # truncated, never a DB-level 500). Limits match CeoProfileRequest's own
    # max_length values, plus Address which the schema leaves unbounded.
    full_name = form_text(full_name, "Full name", 150)
    executive_title = form_text(executive_title, "Executive title", 120)
    phone_number = form_text(phone_number, "Phone number", 30)
    email = form_text(email, "Email", 255)
    date_of_birth = form_text(date_of_birth, "Date of birth", 30)
    address = form_text(address, "Address", MAX_SHORT_TEXT_LEN)
    business_details = form_text(business_details, "Business details", MAX_NARRATIVE_TEXT_LEN)

    try:
        payload = CeoProfileRequest(
            full_name=full_name,
            executive_title=executive_title,
            phone_number=phone_number,
            email=email or None,
            date_of_birth=date_of_birth,
            address=address,
            business_details=business_details or None,
        )
    except PydanticValidationError as exc:
        # Pydantic's exc.errors() returns a list of dicts — every other error
        # in this app returns a plain string `detail`, and the frontend's
        # ApiError just does new Error(detail), which silently stringifies a
        # list/dict to "[object Object]" instead of showing anything useful.
        # Flatten to one readable string to match that convention.
        messages = []
        for err in exc.errors(include_url=False, include_context=False):
            msg = err["msg"]
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            field = err["loc"][-1] if err["loc"] else None
            messages.append(f"{field}: {msg}" if field else msg)
        raise HTTPException(status_code=400, detail="; ".join(messages))

    was_already_completed = user.is_profile_completed

    profile = db.query(CeoProfile).filter(CeoProfile.user_id == user.id).with_for_update().first()
    if profile is None:
        profile = CeoProfile(user_id=user.id)
        db.add(profile)

    photo_warning = None
    if profile_photo is not None and profile_photo.filename:
        extension = profile_photo.filename.rsplit(".", 1)[-1].lower() if "." in profile_photo.filename else ""
        if extension not in ALLOWED_PROFILE_PHOTO_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported photo type. Allowed: {', '.join(sorted(ALLOWED_PROFILE_PHOTO_EXTENSIONS))}",
            )
        profile_photo.file.seek(0, 2)
        photo_size = profile_photo.file.tell()
        profile_photo.file.seek(0)
        if photo_size > MAX_PROFILE_PHOTO_SIZE_BYTES:
            raise HTTPException(status_code=400, detail="Profile photo must be 5 MB or smaller")
        if not content_matches_extension(extension, profile_photo.file):
            raise HTTPException(status_code=400, detail="File content does not match its file type")

        uploaded_url = upload_fileobj(profile_photo.file, profile_photo.filename, folder="profile-photos")
        if uploaded_url:
            profile.profile_pic_url = uploaded_url
        else:
            # upload_fileobj() returns None both when file storage isn't
            # configured and when the upload itself failed — either way, the
            # rest of the profile is still valid and gets saved below, but
            # silently dropping the photo here left CEOs with no idea their
            # picture never actually saved ("why is my photo not showing").
            photo_warning = (
                "File storage isn't configured on this server, so the photo wasn't saved — the rest of your profile was."
                if not r2_is_configured()
                else "The photo couldn't be uploaded right now — the rest of your profile was saved. Please try again."
            )

    profile.executive_title = payload.executive_title
    profile.phone_number = payload.phone_number
    profile.email = payload.email
    profile.date_of_birth = payload.date_of_birth
    profile.address = payload.address
    profile.business_details = payload.business_details
    user.full_name = payload.full_name
    user.is_profile_completed = True
    db.commit()

    log_audit(db, user, "CEO_PROFILE_COMPLETED" if not was_already_completed else "CEO_PROFILE_UPDATED", "", request)

    return {"ok": True, "redirect_to": resolve_redirect_path(user), "photo_warning": photo_warning}


@router.get("/financial-notes")
def list_financial_notes(user: User = Depends(require_roles("CEO")), db: Session = Depends(get_db)):
    """This month's Record Notes log for the CEO's own business unit."""
    month_start = date(today_local().year, today_local().month, 1)
    notes = (
        db.query(FinancialNote)
        .filter(FinancialNote.business_unit_id == user.business_unit_id, FinancialNote.note_date >= month_start)
        .order_by(FinancialNote.note_date.desc(), FinancialNote.created_at.desc())
        .all()
    )
    return {
        "financial_notes": [
            {"note_date": n.note_date.isoformat(), "note_text": n.note_text, "created_at": format_local_datetime(n.created_at)}
            for n in notes
        ]
    }


@router.post("/financial-notes")
def add_financial_note(
    payload: FinancialNoteRequest,
    request: Request,
    user: User = Depends(require_roles("CEO")),
    db: Session = Depends(get_db),
):
    """Appends one dated entry to this month's Record Notes log — an
    append-only qualitative log alongside the Cumulative Net Revenue figures,
    independent of the mandatory daily report (a CEO can add a note any time,
    more than once a day, without needing to resubmit their whole report)."""
    # FinancialNoteRequest already caps the raw text at 2,000 characters (a
    # 422 from pydantic, before this runs); this adds the checks it can't:
    # whitespace-only notes (pass min_length=1 but store as an empty note),
    # NUL characters and un-encodable text (both a DB-level 500 on Postgres).
    note_text = form_text(payload.note_text, "Note", 2000)
    if not note_text:
        raise HTTPException(status_code=400, detail="Note cannot be blank")
    note = FinancialNote(
        business_unit_id=user.business_unit_id,
        note_date=today_local(),
        note_text=note_text,
        created_by=user.id,
    )
    db.add(note)
    db.commit()
    log_audit(db, user, "FINANCIAL_NOTE_ADDED", "", request)
    return {"ok": True}


@router.post("/submit-report")
def submit_report(
    request: Request,
    revenue: str = Form(""),
    cash_flow: str = Form(""),
    receivable: str = Form(""),
    payable: str = Form(""),
    other_amount: str = Form(""),
    financial_other_notes: str = Form(""),
    meetings_attended: str = Form(""),
    site_visits: str = Form(""),
    key_decisions: str = Form(""),
    next_day_schedule: str = Form(""),
    gym_percent: str = Form(""),
    swimming_percent: str = Form(""),
    cycle_percent: str = Form(""),
    activity_other_notes: str = Form(""),
    daily_steps: str = Form(""),
    heart_rate: str = Form(""),
    sleep_hours: str = Form(""),
    bp_systolic: str = Form(""),
    bp_diastolic: str = Form(""),
    health_other_notes: str = Form(""),
    event_flag: str = Form("false"),
    meeting_flag: str = Form("false"),
    golf_flag: str = Form("false"),
    social_other_notes: str = Form(""),
    attachment: Optional[UploadFile] = File(None),
    user: User = Depends(require_roles("CEO")),
    db: Session = Depends(get_db),
):
    # Deliberately a plain `def`, not `async def`: everything below is blocking
    # (SQLAlchemy queries with row locks, a synchronous boto3 upload of up to
    # 10 MB), so FastAPI must run it in its threadpool instead of on the event
    # loop, where it would stall every other request while locks are held.
    # UploadFile.file is a regular SpooledTemporaryFile, safe to use from here.
    if _is_rate_limited(user.id):
        log_audit(db, user, "REPORT_SUBMIT_RATE_LIMITED", "", request)
        raise HTTPException(status_code=429, detail="Too many submissions in a short time. Please wait a moment and try again.")

    report_date = today_local()

    # --- Validate every input up front, before any DB work, row lock or
    # upload. Invalid values are a clear HTTP 400 — never a 500, and never
    # stored (NaN/Infinity/huge money would poison the dashboards' SUMs). The
    # ranges mirror the database CHECK constraints. Blank stays "not given". ---
    revenue_val = form_money(revenue, "Revenue")
    cash_flow_val = form_money(cash_flow, "Cash Flow")
    receivable_val = form_money(receivable, "Receivable")
    payable_val = form_money(payable, "Payable")
    # Optional — unlike the four mandatory fields below, Other Amount was
    # added after CEOs already had an established daily habit around
    # Revenue/Cash Flow/Receivable/Payable, so it defaults to 0 rather than
    # blocking submission when left blank.
    other_amount_val = form_money(other_amount, "Other Amount") or Decimal("0")

    # Financial fields are mandatory every day — no weekday/weekend exception.
    missing = [
        label
        for label, val in [("Revenue", revenue_val), ("Cash Flow", cash_flow_val), ("Receivable", receivable_val), ("Payable", payable_val)]
        if val is None
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Financial fields are mandatory. Missing: {', '.join(missing)}",
        )

    daily_steps_val = form_int(daily_steps, *STEPS_RANGE)
    heart_rate_val = form_int(heart_rate, *HEART_RATE_RANGE)
    sleep_hours_val = form_decimal(sleep_hours, *SLEEP_HOURS_RANGE, places=1)
    bp_systolic_val = form_int(bp_systolic, *BP_SYSTOLIC_RANGE)
    bp_diastolic_val = form_int(bp_diastolic, *BP_DIASTOLIC_RANGE)
    validate_blood_pressure(bp_systolic_val, bp_diastolic_val)

    gym_percent_val = form_percent(gym_percent, "Gym %")
    swimming_percent_val = form_percent(swimming_percent, "Swimming %")
    cycle_percent_val = form_percent(cycle_percent, "Cycle %")

    financial_other_notes = form_text(financial_other_notes, "Financial notes", MAX_NARRATIVE_TEXT_LEN)
    meetings_attended = form_text(meetings_attended, "Meetings attended", MAX_NARRATIVE_TEXT_LEN)
    site_visits = form_text(site_visits, "Site visits", MAX_NARRATIVE_TEXT_LEN)
    key_decisions = form_text(key_decisions, "Key decisions", MAX_NARRATIVE_TEXT_LEN)
    next_day_schedule = form_text(next_day_schedule, "Next day schedule", MAX_NARRATIVE_TEXT_LEN)
    activity_other_notes = form_text(activity_other_notes, "Activity notes", MAX_NARRATIVE_TEXT_LEN)
    health_other_notes = form_text(health_other_notes, "Health notes", MAX_NARRATIVE_TEXT_LEN)
    social_other_notes = form_text(social_other_notes, "Social notes", MAX_NARRATIVE_TEXT_LEN)

    # The attachment is checked and uploaded here, before any row lock is
    # taken below, so a slow upload never holds a lock on the day's rows.
    document_url = None
    if attachment is not None and attachment.filename:
        extension = attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else ""
        if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported attachment type. Allowed: {', '.join(sorted(ALLOWED_ATTACHMENT_EXTENSIONS))}",
            )
        attachment.file.seek(0, 2)
        attachment_size = attachment.file.tell()
        attachment.file.seek(0)
        if attachment_size > MAX_ATTACHMENT_SIZE_BYTES:
            raise HTTPException(status_code=400, detail="Attachment must be 10 MB or smaller")
        if not content_matches_extension(extension, attachment.file):
            raise HTTPException(status_code=400, detail="File content does not match its file type")

        document_url = upload_fileobj(attachment.file, attachment.filename)

    financial = (
        db.query(DailyFinancial)
        .filter(DailyFinancial.business_unit_id == user.business_unit_id, DailyFinancial.report_date == report_date)
        .with_for_update()
        .first()
    )
    # Captured before being overwritten below, so a same-day resubmission's
    # audit log entry can show exactly what changed rather than just that a
    # submission happened — the only tamper-evidence for financial corrections.
    previous_financial = (
        {
            "revenue": financial.revenue,
            "cash_flow": financial.cash_flow,
            "receivable": financial.receivable,
            "payable": financial.payable,
            "other_amount": financial.other_amount,
            "financial_other_notes": financial.other_notes,
        }
        if financial is not None
        else None
    )
    if financial is None:
        financial = DailyFinancial(business_unit_id=user.business_unit_id, report_date=report_date)
        db.add(financial)

    # expenses/net_profit/cash_in_hand are legacy columns no longer collected
    # from the form — zeroed out rather than left stale so historical views
    # (Comparison, Operations, Excel export) show $0 rather than old data.
    financial.revenue = revenue_val
    financial.other_amount = other_amount_val
    financial.expenses = Decimal("0")
    financial.net_profit = Decimal("0")
    financial.cash_in_hand = Decimal("0")
    financial.cash_flow = cash_flow_val
    financial.receivable = receivable_val
    financial.payable = payable_val
    financial.other_notes = financial_other_notes or None
    financial.submitted_by = user.id

    activity = (
        db.query(ActivityLog)
        .filter(ActivityLog.business_unit_id == user.business_unit_id, ActivityLog.report_date == report_date)
        .with_for_update()
        .first()
    )
    previous_activity = (
        {
            "meetings_attended": activity.meetings_attended,
            "site_visits": activity.site_visits,
            "key_decisions": activity.key_decisions,
            "next_day_schedule": activity.next_day_schedule,
            "gym_percent": activity.gym_percent,
            "swimming_percent": activity.swimming_percent,
            "cycle_percent": activity.cycle_percent,
            "activity_other_notes": activity.other_notes,
        }
        if activity is not None
        else None
    )
    if activity is None:
        activity = ActivityLog(business_unit_id=user.business_unit_id, report_date=report_date)
        db.add(activity)
    elif activity.submitted_by != user.id:
        # Today's row was left by a previous CEO of this unit: every field is
        # overwritten below, but the attachment link is only replaced when a
        # new file is sent — never carry their document over as this CEO's.
        activity.document_url = None
    activity.meetings_attended = meetings_attended
    activity.site_visits = site_visits
    activity.key_decisions = key_decisions
    activity.next_day_schedule = next_day_schedule
    if document_url:
        activity.document_url = document_url
    activity.gym_percent = gym_percent_val
    activity.swimming_percent = swimming_percent_val
    activity.cycle_percent = cycle_percent_val
    activity.other_notes = activity_other_notes or None
    activity.submitted_by = user.id

    health = (
        db.query(HealthStatus)
        .filter(HealthStatus.business_unit_id == user.business_unit_id, HealthStatus.report_date == report_date)
        .with_for_update()
        .first()
    )
    previous_health = (
        {
            "daily_steps": health.daily_steps,
            "heart_rate": health.heart_rate,
            "sleep_hours": health.sleep_hours,
            "bp_systolic": health.bp_systolic,
            "bp_diastolic": health.bp_diastolic,
            "health_other_notes": health.other_notes,
        }
        if health is not None
        else None
    )
    if health is None:
        health = HealthStatus(business_unit_id=user.business_unit_id, report_date=report_date)
        db.add(health)
    # physical_health/stress_level/energy_mood/sleep_quality are legacy fields no
    # longer collected from the form — left null; the health-risk calculation
    # already treats a null base as neutral and relies on steps + vitals instead.
    health.daily_steps = daily_steps_val
    health.heart_rate = heart_rate_val
    health.sleep_hours = sleep_hours_val
    health.bp_systolic = bp_systolic_val
    health.bp_diastolic = bp_diastolic_val
    health.other_notes = health_other_notes or None
    health.submitted_by = user.id

    social = (
        db.query(SocialLog)
        .filter(SocialLog.business_unit_id == user.business_unit_id, SocialLog.report_date == report_date)
        .with_for_update()
        .first()
    )
    previous_social = (
        {
            "event_flag": social.event_flag,
            "meeting_flag": social.meeting_flag,
            "golf_flag": social.golf_flag,
            "social_other_notes": social.other_notes,
        }
        if social is not None
        else None
    )
    if social is None:
        social = SocialLog(business_unit_id=user.business_unit_id, report_date=report_date)
        db.add(social)
    social.event_flag = _parse_bool(event_flag)
    social.meeting_flag = _parse_bool(meeting_flag)
    social.golf_flag = _parse_bool(golf_flag)
    social.other_notes = social_other_notes or None
    social.submitted_by = user.id

    try:
        db.commit()
    except IntegrityError:
        # Two concurrent submissions (double-click, retried request) can both
        # miss each other's row on the initial lookup above and then race on
        # the same per-day unique constraint — surface a clean "try again"
        # rather than a raw 500, since a genuine resubmit is otherwise allowed.
        db.rollback()
        log_audit(db, user, "REPORT_SUBMIT_CONFLICT", f"date={report_date.isoformat()}", request)
        raise HTTPException(
            status_code=409,
            detail="Another submission for today was saved at the same moment — please try again.",
        )

    changes = None
    if previous_financial is not None:
        current_financial = {
            "revenue": revenue_val,
            "cash_flow": cash_flow_val,
            "receivable": receivable_val,
            "payable": payable_val,
            "other_amount": other_amount_val,
            "financial_other_notes": financial.other_notes,
        }
        current_activity = {
            "meetings_attended": activity.meetings_attended,
            "site_visits": activity.site_visits,
            "key_decisions": activity.key_decisions,
            "next_day_schedule": activity.next_day_schedule,
            "gym_percent": gym_percent_val,
            "swimming_percent": swimming_percent_val,
            "cycle_percent": cycle_percent_val,
            "activity_other_notes": activity.other_notes,
        }
        current_health = {
            "daily_steps": daily_steps_val,
            "heart_rate": heart_rate_val,
            "sleep_hours": sleep_hours_val,
            "bp_systolic": bp_systolic_val,
            "bp_diastolic": bp_diastolic_val,
            "health_other_notes": health.other_notes,
        }
        current_social = {
            "event_flag": social.event_flag,
            "meeting_flag": social.meeting_flag,
            "golf_flag": social.golf_flag,
            "social_other_notes": social.other_notes,
        }
        # Health vitals and free-text values are recorded as "(hidden)" by
        # compute_field_changes (only THAT they changed is audited); money
        # keeps real values as the tamper trail for financial edits.
        changes = (
            compute_field_changes(previous_financial, current_financial)
            + compute_field_changes(previous_activity, current_activity)
            + compute_field_changes(previous_health, current_health)
            + compute_field_changes(previous_social, current_social)
        )

    audit_detail = build_resubmit_audit_detail(report_date, changes)
    log_audit(db, user, "DAILY_REPORT_SUBMITTED", audit_detail, request)

    return {"ok": True}
