import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import require_roles, resolve_redirect_path
from src.constants import NotificationType, Role
from src.config import settings
from src.database import get_db
from src.models import ActivityLog, BusinessUnit, CeoProfile, DailyFinancial, FinancialNote, HealthStatus, MonthlyTarget, SocialLog, User
from src.r2_storage import delete_object, extract_r2_key
from src.r2_storage import is_configured as r2_is_configured
from src.r2_storage import upload_fileobj
from src.report_forms import CeoReportForm, ceo_report_form
from src.schemas import CeoProfileRequest, FinancialNoteRequest, MonthlyTargetRequest
from src.services.daily_report import DailyReportRowSpec, upsert_daily_report
from src.upload_validation import content_matches_extension
from src.utils import (
    MAX_NARRATIVE_TEXT_LEN,
    MAX_SHORT_TEXT_LEN,
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
    form_text,
    format_local_datetime,
    log_audit,
    monthly_target_window_error,
    notify,
    parse_month_or_today,
    parse_monthly_target_amounts,
    serialize_ceo_profile,
    shift_month,
    SubmitRateLimiter,
    today_local,
    todays_submission_events,
)

router = APIRouter(prefix="/api/ceo", tags=["ceo"])

# Sliding-window guard on /api/ceo/submit-report: a real daily report is
# submitted at most a couple of times a day, so this only ever engages
# against double submits or scripted retries — but it protects the DB write
# and the R2 upload from being hammered.
REPORT_SUBMIT_RATE_LIMIT = 5
REPORT_SUBMIT_RATE_WINDOW_SECONDS = 60
# M-12: the guard itself lives in src/utils.py (SubmitRateLimiter); this
# router owns one instance so CEO and HO keep separate budgets.
_rate_limiter = SubmitRateLimiter(REPORT_SUBMIT_RATE_LIMIT, REPORT_SUBMIT_RATE_WINDOW_SECONDS)

logger = logging.getLogger(__name__)

MAX_PROFILE_PHOTO_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_PROFILE_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


@router.get("/dashboard")
def ceo_dashboard(
    month: str = "",
    for_date: str = Query("", alias="date"),
    user: User = Depends(require_roles(Role.CEO)),
    db: Session = Depends(get_db),
):
    report_date = today_local()

    calendar_year, calendar_month = parse_month_or_today(month, report_date)

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

    # Form date: the dashboard's report form normally edits today's rows.
    # The History page's month calendar links here with ?date=YYYY-MM-DD so
    # a CEO can fill or correct any day of the month; the form then
    # prefills from (and submits back to) that date's rows instead.
    # Everything else on this payload — targets, trends, quadrants — stays
    # anchored to today, exactly as before.
    try:
        form_date = date.fromisoformat(for_date) if for_date else report_date
    except ValueError:
        form_date = report_date
    form_financial = (
        db.query(DailyFinancial)
        .filter(DailyFinancial.business_unit_id == user.business_unit_id, DailyFinancial.report_date == form_date)
        .first()
    )
    form_activity = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.business_unit_id == user.business_unit_id,
            ActivityLog.report_date == form_date,
            ActivityLog.submitted_by == user.id,
        )
        .first()
    )
    form_health = (
        db.query(HealthStatus)
        .filter(
            HealthStatus.business_unit_id == user.business_unit_id,
            HealthStatus.report_date == form_date,
            HealthStatus.submitted_by == user.id,
        )
        .first()
    )
    form_social = (
        db.query(SocialLog)
        .filter(
            SocialLog.business_unit_id == user.business_unit_id,
            SocialLog.report_date == form_date,
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
    try:
        ceo_profile_row = db.query(CeoProfile).filter(CeoProfile.user_id == user.id).first()
    except RuntimeError:
        # D-6: a profile row encrypted under a lost/rotated-out key must not
        # 500 the dashboard — show no profile instead (GET /api/ceo/profile
        # reports the individual fields as "unavailable").
        db.rollback()
        ceo_profile_row = None

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

    def _financial_payload(f):
        return {
            "revenue": float(f.revenue),
            "revenue_anomaly": bool(getattr(f, "revenue_anomaly", False)),
            "revenue_anomaly_reviewed": bool(getattr(f, "revenue_anomaly_reviewed_at", None)),
            "expenses": float(f.expenses),
            "net_profit": float(f.net_profit),
            "cash_in_hand": float(f.cash_in_hand),
            "cash_flow": float(f.cash_flow) if f.cash_flow is not None else None,
            "receivable": float(f.receivable) if f.receivable is not None else None,
            "payable": float(f.payable) if f.payable is not None else None,
            "other_amount": float(f.other_amount) if f.other_amount is not None else None,
            "other_notes": f.other_notes,
        } if f else None

    def _activity_payload(a):
        resolver_username = None
        if a and a.help_resolved_by:
            resolver = db.query(User).filter(User.id == a.help_resolved_by).first()
            resolver_username = resolver.username if resolver else None
        return {
            "meetings_attended": a.meetings_attended,
            "meetings_count": a.meetings_count,
            "site_visits": a.site_visits,
            "key_decisions": a.key_decisions,
            "key_achievement": a.key_achievement,
            "next_day_schedule": a.next_day_schedule,
            "problems_today": a.problems_today,
            "help_needed": a.help_needed,
            "needs_help": bool(a.help_needed and a.help_needed.strip() and a.help_resolved_at is None),
            "help_resolved_at": format_local_datetime(a.help_resolved_at),
            "help_resolved_by_username": resolver_username,
            "help_response": a.help_response,
            "document_url": a.document_url,
            "gym_percent": a.gym_percent,
            "swimming_percent": a.swimming_percent,
            "cycle_percent": a.cycle_percent,
            "other_notes": a.other_notes,
            "updated_at": format_local_datetime(a.updated_at),
        } if a else None

    def _health_payload(h):
        return {
            "physical_health": h.physical_health,
            "stress_level": h.stress_level,
            "energy_mood": h.energy_mood,
            "sleep_quality": h.sleep_quality,
            "daily_steps": h.daily_steps,
            "heart_rate": h.heart_rate,
            "sleep_hours": float(h.sleep_hours) if h.sleep_hours is not None else None,
            "bp_systolic": h.bp_systolic,
            "bp_diastolic": h.bp_diastolic,
            "weight_kg": float(h.weight_kg) if h.weight_kg is not None else None,
            "is_sick": h.is_sick,
            "gym_percent": h.gym_percent,
            "swimming_percent": h.swimming_percent,
            "cycle_percent": h.cycle_percent,
            "other_notes": h.other_notes,
        } if h else None

    def _social_payload(s):
        return {
            "event_flag": s.event_flag,
            "meeting_flag": s.meeting_flag,
            "golf_flag": s.golf_flag,
            "other_notes": s.other_notes,
        } if s else None

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
        "financial": _financial_payload(financial),
        "financial_trend": financial_trend,
        "activity": _activity_payload(activity),
        "health": _health_payload(health),
        "social": _social_payload(social),
        # The report form's own rows (see form_date above): identical to
        # the financial/activity/health/social keys above when no ?date=
        # was given, so the form can always prefill from these.
        "form_date": form_date.isoformat(),
        "form_date_display": form_date.strftime("%A, %d %B %Y"),
        "form_is_today": form_date == report_date,
        "form_financial": _financial_payload(form_financial),
        "form_activity": _activity_payload(form_activity),
        "form_health": _health_payload(form_health),
        "form_social": _social_payload(form_social),
        "form_already_submitted": form_activity is not None,
        "form_submitted_at": format_local_datetime(form_activity.updated_at) if form_activity else None,
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
            # Exercise completion lives on the health row since the form
            # move (2026-10-22); fall back to the activity row's pre-move
            # percents so older days keep displaying theirs.
            "gym_percent": (
                latest_health.gym_percent if latest_health and latest_health.gym_percent is not None
                else (latest_activity.gym_percent if latest_activity else None)
            ),
            "swimming_percent": (
                latest_health.swimming_percent if latest_health and latest_health.swimming_percent is not None
                else (latest_activity.swimming_percent if latest_activity else None)
            ),
            "cycle_percent": (
                latest_health.cycle_percent if latest_health and latest_health.cycle_percent is not None
                else (latest_activity.cycle_percent if latest_activity else None)
            ),
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
    user: User = Depends(require_roles(Role.CEO)),
    db: Session = Depends(get_db),
):
    """Fetches the target already set for the given month (default: current
    month) so the "Set Monthly Target" form can pre-fill with existing values
    instead of always starting blank."""
    today = today_local()
    year, month_num = parse_month_or_today(month, today)
    target_month = date(year, month_num, 1)

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
    user: User = Depends(require_roles(Role.CEO)),
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

    # M-03: a target for a past month is only settable within the edit
    # window after that month ends; older months need the admin override.
    window_error = monthly_target_window_error(target_month, today_local(), settings.REPORT_EDIT_WINDOW_DAYS)
    if window_error:
        raise HTTPException(status_code=400, detail=window_error)

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
def get_profile(user: User = Depends(require_roles(Role.CEO)), db: Session = Depends(get_db)):
    """Fetches the CEO's own mandatory profile — pre-fills the Profile Setup
    form with whatever's already on file (blank fields on first visit)."""
    from sqlalchemy.orm import load_only

    decrypt_failed = False
    try:
        profile = db.query(CeoProfile).filter(CeoProfile.user_id == user.id).first()
    except RuntimeError:
        # D-6: a row encrypted under a lost/rotated-out key must not 500 the
        # whole page. The encrypted columns are unreadable, so re-read only
        # the plaintext columns and report the encrypted ones as unavailable.
        decrypt_failed = True
        db.rollback()
        profile = (
            db.query(CeoProfile)
            .options(load_only(CeoProfile.id, CeoProfile.user_id, CeoProfile.executive_title,
                               CeoProfile.business_details, CeoProfile.profile_pic_url))
            .filter(CeoProfile.user_id == user.id)
            .first()
        )
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == user.business_unit_id).first()

    unavailable = "unavailable"
    return {
        "is_profile_completed": user.is_profile_completed,
        "full_name": user.full_name,
        "executive_title": profile.executive_title if profile else "Chief Executive Officer",
        "phone_number": unavailable if decrypt_failed else (profile.phone_number if profile else None),
        "email": unavailable if decrypt_failed else (profile.email if profile else None),
        "date_of_birth": (
            None
            if decrypt_failed or not profile or not profile.date_of_birth
            else profile.date_of_birth.isoformat()
        ),
        "age": (
            None
            if decrypt_failed or not profile or not profile.date_of_birth
            else calculate_age(profile.date_of_birth)
        ),
        "address": unavailable if decrypt_failed else (profile.address if profile else None),
        "business_details": profile.business_details if profile else None,
        "profile_pic_url": profile.profile_pic_url if profile else None,
        "profile_fields_unavailable": decrypt_failed,
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
    user: User = Depends(require_roles(Role.CEO)),
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
    previous_photo_url = None
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
            # SM-7: remember the previous photo so it can be deleted once
            # the replacement is committed (below) instead of orphaned.
            previous_photo_url = profile.profile_pic_url
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

    if previous_photo_url and profile.profile_pic_url and previous_photo_url != profile.profile_pic_url:
        # SM-7: the photo was replaced — delete the previous R2 object
        # instead of leaving it orphaned forever.
        delete_object(extract_r2_key(previous_photo_url))

    log_audit(db, user, "CEO_PROFILE_COMPLETED" if not was_already_completed else "CEO_PROFILE_UPDATED", "", request)

    return {"ok": True, "redirect_to": resolve_redirect_path(user), "photo_warning": photo_warning}


@router.get("/financial-notes")
def list_financial_notes(user: User = Depends(require_roles(Role.CEO)), db: Session = Depends(get_db)):
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
    user: User = Depends(require_roles(Role.CEO)),
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


_parse_report_form = ceo_report_form(_rate_limiter)


def _apply_ceo_report(form: CeoReportForm, user: User):
    """Returns the `apply` callback for the shared upsert: assigns the
    parsed CEO form to the four per-day rows. Returns the previous
    attachment URL (or None) so a replaced attachment can be deleted
    after the commit. Pre-overwrite values (help request, attachment)
    are read before any assignment — the resubmit rules below depend on
    what was there before this submission."""

    def apply(rows, previous):
        financial = rows["financial"]
        activity = rows["activity"]
        health = rows["health"]
        social = rows["social"]

        # expenses/net_profit/cash_in_hand are legacy columns no longer collected
        # from the form — zeroed out rather than left stale so historical views
        # (Comparison, Operations, Excel export) show $0 rather than old data.
        financial.revenue = form.revenue
        financial.other_amount = form.other_amount
        financial.expenses = Decimal("0")
        financial.net_profit = Decimal("0")
        financial.cash_in_hand = Decimal("0")
        financial.cash_flow = form.cash_flow
        financial.receivable = form.receivable
        financial.payable = form.payable
        financial.other_notes = form.financial_other_notes or None
        financial.submitted_by = user.id

        # Captured for the help re-open rule below: if this day's request was
        # already resolved and the CEO now changes the ask, the resolution is
        # cleared (the old response no longer matches the request).
        previous_help_needed = activity.help_needed
        previous_help_resolved_at = activity.help_resolved_at
        # SM-7: captured before being overwritten below, so a resubmission that
        # replaces the attachment can delete the previous R2 object afterwards.
        previous_document_url = activity.document_url
        if previous["activity"] is not None and activity.submitted_by != user.id:
            # Today's row was left by a previous CEO of this unit: every field is
            # overwritten below, but the attachment link is only replaced when a
            # new file is sent — never carry their document over as this CEO's.
            activity.document_url = None
        activity.meetings_attended = form.meetings_attended
        activity.meetings_count = form.meetings_count
        activity.site_visits = form.site_visits
        activity.key_decisions = form.key_decisions
        activity.key_achievement = form.key_achievement or None
        activity.next_day_schedule = form.next_day_schedule
        activity.problems_today = form.problems_today or None
        activity.help_needed = form.help_needed or None
        if previous_help_resolved_at is not None and activity.help_needed != previous_help_needed:
            # Re-open: the ask changed after HO/Chairman answered it, so the
            # recorded response no longer applies. An unchanged resubmission
            # (e.g. fixing the day's revenue) keeps the resolution intact.
            activity.help_resolved_at = None
            activity.help_resolved_by = None
            activity.help_response = None
        if form.document_url:
            activity.document_url = form.document_url
        if not form.health_exercise_present:
            # Pre-move clients still send exercise % as activity fields. The
            # current form sends health_*_percent instead, and then the
            # activity row's legacy percents are left exactly as stored —
            # overwriting them with blanks would erase the fallback data
            # older days rely on for display.
            activity.gym_percent = form.gym_percent
            activity.swimming_percent = form.swimming_percent
            activity.cycle_percent = form.cycle_percent
        activity.other_notes = form.activity_other_notes or None
        activity.submitted_by = user.id

        # physical_health/sleep_quality remain legacy fields no longer
        # collected — left null. stress_level/energy_mood are collected again
        # (one-tap chips), alongside weight, the sick toggle, and the exercise
        # percents that moved here from the activity section.
        health.daily_steps = form.daily_steps
        health.heart_rate = form.heart_rate
        health.sleep_hours = form.sleep_hours
        health.bp_systolic = form.bp_systolic
        health.bp_diastolic = form.bp_diastolic
        health.stress_level = form.stress_level
        health.energy_mood = form.energy_mood
        health.weight_kg = form.weight_kg
        health.is_sick = form.is_sick
        if form.health_exercise_present:
            health.gym_percent = form.health_gym_percent
            health.swimming_percent = form.health_swimming_percent
            health.cycle_percent = form.health_cycle_percent
        health.other_notes = form.health_other_notes or None
        health.submitted_by = user.id

        social.event_flag = form.event_flag
        social.meeting_flag = form.meeting_flag
        social.golf_flag = form.golf_flag
        social.other_notes = form.social_other_notes or None
        social.submitted_by = user.id

        return previous_document_url

    return apply


@router.post("/submit-report")
def submit_report(
    request: Request,
    form: CeoReportForm = Depends(_parse_report_form),
    user: User = Depends(require_roles(Role.CEO)),
    db: Session = Depends(get_db),
):
    # Deliberately a plain `def`, not `async def`: everything below is blocking
    # (SQLAlchemy queries with row locks, a synchronous boto3 upload of up to
    # 10 MB), so FastAPI must run it in its threadpool instead of on the event
    # loop, where it would stall every other request while locks are held.
    # UploadFile.file is a regular SpooledTemporaryFile, safe to use from here.
    #
    # B-M1: parsing/validation/upload live in src/report_forms.py (the
    # `form` dependency) and the 4-table upsert in
    # src/services/daily_report.py — this handler only wires them together
    # and does the CEO-only post-submit work (anomaly + backdated alerts).
    report_date = form.report_date
    specs = [
        DailyReportRowSpec(
            key="financial", cls=DailyFinancial,
            filters={"business_unit_id": user.business_unit_id, "report_date": report_date},
            create_kwargs={"business_unit_id": user.business_unit_id, "report_date": report_date},
            snapshot_fields=(
                ("revenue", "revenue"), ("cash_flow", "cash_flow"),
                ("receivable", "receivable"), ("payable", "payable"),
                ("other_amount", "other_amount"), ("other_notes", "financial_other_notes"),
                # PM-6: the sticky-anomaly check below reads the previous
                # row's flag/review state from this snapshot (previous is
                # a dict, not the ORM row).
                ("revenue_anomaly", "revenue_anomaly"),
                ("revenue_anomaly_reviewed_at", "revenue_anomaly_reviewed_at"),
            ),
        ),
        DailyReportRowSpec(
            key="activity", cls=ActivityLog,
            filters={"business_unit_id": user.business_unit_id, "report_date": report_date},
            create_kwargs={"business_unit_id": user.business_unit_id, "report_date": report_date},
            snapshot_fields=(
                ("meetings_attended", "meetings_attended"), ("meetings_count", "meetings_count"),
                ("site_visits", "site_visits"), ("key_decisions", "key_decisions"),
                ("key_achievement", "key_achievement"), ("next_day_schedule", "next_day_schedule"),
                ("problems_today", "problems_today"), ("help_needed", "help_needed"),
                ("gym_percent", "gym_percent"), ("swimming_percent", "swimming_percent"),
                ("cycle_percent", "cycle_percent"), ("other_notes", "activity_other_notes"),
            ),
        ),
        DailyReportRowSpec(
            key="health", cls=HealthStatus,
            filters={"business_unit_id": user.business_unit_id, "report_date": report_date},
            create_kwargs={"business_unit_id": user.business_unit_id, "report_date": report_date},
            snapshot_fields=(
                ("daily_steps", "daily_steps"), ("heart_rate", "heart_rate"),
                ("sleep_hours", "sleep_hours"), ("bp_systolic", "bp_systolic"),
                ("bp_diastolic", "bp_diastolic"), ("stress_level", "stress_level"),
                ("energy_mood", "energy_mood"), ("weight_kg", "weight_kg"),
                ("is_sick", "is_sick"), ("gym_percent", "gym_percent"),
                ("swimming_percent", "swimming_percent"), ("cycle_percent", "cycle_percent"),
                ("other_notes", "health_other_notes"),
            ),
        ),
        DailyReportRowSpec(
            key="social", cls=SocialLog,
            filters={"business_unit_id": user.business_unit_id, "report_date": report_date},
            create_kwargs={"business_unit_id": user.business_unit_id, "report_date": report_date},
            snapshot_fields=(
                ("event_flag", "event_flag"), ("meeting_flag", "meeting_flag"),
                ("golf_flag", "golf_flag"), ("other_notes", "social_other_notes"),
            ),
        ),
    ]
    result = upsert_daily_report(
        db=db, request=request, user=user, report_date=report_date, specs=specs,
        document_url=form.document_url, apply=_apply_ceo_report(form, user),
        conflict_audit_action="REPORT_SUBMIT_CONFLICT",
    )
    financial = result.rows["financial"]
    activity = result.rows["activity"]
    health = result.rows["health"]
    previous_financial = result.previous["financial"]
    previous_activity = result.previous["activity"]
    previous_health = result.previous["health"]
    previous_social = result.previous["social"]

    changes = None
    if previous_financial is not None:
        current_financial = {
            "revenue": form.revenue,
            "cash_flow": form.cash_flow,
            "receivable": form.receivable,
            "payable": form.payable,
            "other_amount": form.other_amount,
            "financial_other_notes": financial.other_notes,
        }
        current_activity = {
            "meetings_attended": activity.meetings_attended,
            "meetings_count": activity.meetings_count,
            "site_visits": activity.site_visits,
            "key_decisions": activity.key_decisions,
            "key_achievement": activity.key_achievement,
            "next_day_schedule": activity.next_day_schedule,
            "problems_today": activity.problems_today,
            "help_needed": activity.help_needed,
            "gym_percent": activity.gym_percent,
            "swimming_percent": activity.swimming_percent,
            "cycle_percent": activity.cycle_percent,
            "activity_other_notes": activity.other_notes,
        }
        current_health = {
            "daily_steps": form.daily_steps,
            "heart_rate": form.heart_rate,
            "sleep_hours": form.sleep_hours,
            "bp_systolic": form.bp_systolic,
            "bp_diastolic": form.bp_diastolic,
            "stress_level": form.stress_level,
            "energy_mood": form.energy_mood,
            "weight_kg": form.weight_kg,
            "is_sick": form.is_sick,
            "gym_percent": health.gym_percent,
            "swimming_percent": health.swimming_percent,
            "cycle_percent": health.cycle_percent,
            "health_other_notes": health.other_notes,
        }
        current_social = {
            "event_flag": form.event_flag,
            "meeting_flag": form.meeting_flag,
            "golf_flag": form.golf_flag,
            "social_other_notes": form.social_other_notes,
        }
        # Health vitals and free-text values are recorded as "(hidden)" by
        # compute_field_changes (only THAT they changed is audited); money
        # keeps real values as the tamper trail for financial edits.
        # Each area is diffed only when its previous row exists — a date
        # can hold a financial row without health/social rows (e.g. a
        # partially backfilled day), and diffing against None would 500.
        changes = compute_field_changes(previous_financial, current_financial)
        if previous_activity is not None:
            changes += compute_field_changes(previous_activity, current_activity)
        if previous_health is not None:
            changes += compute_field_changes(previous_health, current_health)
        if previous_social is not None:
            changes += compute_field_changes(previous_social, current_social)

    # D-14(b): anomaly flag on the auto-posted figure — today's revenue vs
    # the BU's trailing 14-day average (today excluded). A >50% deviation
    # flags the row for HO review and notifies HO users; the flag is stored
    # on the row so it survives resubmissions and is visible to every
    # review view that reads the row.
    revenue_anomaly = False
    anomaly_avg = None
    try:
        window_start = report_date - timedelta(days=14)
        prior_amounts = [
            float(r[0] or 0)
            for r in db.query(DailyFinancial.revenue).filter(
                DailyFinancial.business_unit_id == user.business_unit_id,
                DailyFinancial.report_date >= window_start,
                DailyFinancial.report_date < report_date,
            ).all()
        ]
        if prior_amounts:
            anomaly_avg = sum(prior_amounts) / len(prior_amounts)
            if anomaly_avg > 0 and abs(float(form.revenue or 0) - anomaly_avg) / anomaly_avg > 0.5:
                revenue_anomaly = True
    except Exception as exc:
        # Never break a submission over the anomaly check itself.
        logger.warning("revenue anomaly check failed for BU %s: %s", user.business_unit_id, exc)
    # PM-6: sticky anomaly — a resubmit must NOT silently clear an
    # unreviewed anomaly. If the previous row for this date was flagged
    # and nobody has reviewed it yet, the flag stays set whatever today
    # recomputes; only a reviewed flag is replaced by the new computation.
    # A reviewed flag clears its review stamp when a NEW anomaly is set,
    # so the new flag waits for its own review. (previous_financial is
    # the upsert snapshot dict — see snapshot_fields above.)
    _prev_anomaly = (previous_financial or {}).get("revenue_anomaly", False)
    _prev_reviewed_at = (previous_financial or {}).get("revenue_anomaly_reviewed_at")
    previously_flagged_unreviewed = bool(_prev_anomaly) and _prev_reviewed_at is None
    if previously_flagged_unreviewed:
        revenue_anomaly = True
    financial.revenue_anomaly = revenue_anomaly
    if revenue_anomaly and not previously_flagged_unreviewed:
        financial.revenue_anomaly_reviewed_at = None
        financial.revenue_anomaly_reviewed_by = None
    if revenue_anomaly and not previously_flagged_unreviewed:
        bu = db.query(BusinessUnit).filter(BusinessUnit.id == user.business_unit_id).first()
        bu_label = bu.name if bu else f"BU {user.business_unit_id}"
        deviation = (float(form.revenue or 0) - anomaly_avg) / anomaly_avg * 100 if anomaly_avg else 0
        # PM-6: notify the Chairman too (was HO-only), so the flag's
        # reviewer actually sees it.
        for recipient in db.query(User).filter(User.role.in_([Role.CHAIRMAN, Role.HO]), User.is_active == True).all():  # noqa: E712
            notify(
                db, recipient.id, NotificationType.ALERT,
                f"Revenue anomaly: {bu_label}",
                f"Revenue Ks {float(form.revenue or 0):,.0f} on {report_date.isoformat()} "
                f"vs 14-day average Ks {anomaly_avg:,.0f} ({deviation:+.0f}%) — please review." if anomaly_avg else
                f"Revenue Ks {float(form.revenue or 0):,.0f} on {report_date.isoformat()} "
                f"flagged as a revenue anomaly — please review.",
                link=f"/admin/business-unit/{user.business_unit_id}",
            )

    # M-03: an accepted backdated resubmit (a day that already had a
    # report, re-filed inside the window) notifies Chairman/HO so a
    # restatement of reviewed history is visible, not silent.
    if previous_financial is not None and report_date < today_local():
        bu = db.query(BusinessUnit).filter(BusinessUnit.id == user.business_unit_id).first()
        bu_label = bu.name if bu else f"BU {user.business_unit_id}"
        for recipient in db.query(User).filter(
                User.role.in_([Role.CHAIRMAN, Role.HO]), User.is_active == True).all():  # noqa: E712
            notify(
                db, recipient.id, NotificationType.ALERT,
                f"Backdated report edit: {bu_label}",
                f"{user.username} resubmitted the {report_date.isoformat()} report for "
                f"{bu_label} — please review the change.",
                link=f"/admin/business-unit/{user.business_unit_id}",
            )

    # The anomaly flag above and the notifications in this section were
    # added after the main commit, and log_audit below writes on its own
    # session — so persist them here. A failure here must never undo the
    # already-committed report itself, so it is logged and swallowed.
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - report already committed
        db.rollback()
        logger.warning("post-submit notification commit failed: %s", exc)

    audit_detail = build_resubmit_audit_detail(report_date, changes)
    log_audit(db, user, "DAILY_REPORT_SUBMITTED", audit_detail, request)

    return {"ok": True}
