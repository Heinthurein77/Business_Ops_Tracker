import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import require_roles
from src.database import get_db
from src.models import (
    ActivityLog,
    AuditLog,
    BusinessUnit,
    CeoProfile,
    DailyFinancial,
    FinancialNote,
    HealthStatus,
    HeadOfficeActivityLog,
    HeadOfficeDailyFinancial,
    HeadOfficeHealthStatus,
    HeadOfficeMonthlyTarget,
    HeadOfficeSocialLog,
    MonthlyTarget,
    Notification,
    SocialLog,
    User,
)
from src.schemas import MonthlyTargetRequest, ResolveHelpRequest
from src.utils import (
    APP_TZ,
    activity_quadrant_status,
    build_activity_rows,
    build_ceo_activity_calendar,
    build_financial_rows,
    build_health_rows,
    build_social_rows,
    calculate_age,
    compute_bu_quadrant_summary,
    compute_health_risk,
    compute_ho_quadrant_summary,
    compute_monthly_social_counts,
    compute_mtd_cumulative_by_bu,
    evaluate_cumulative_financial_health,
    evaluate_cumulative_health,
    evaluate_financial_health,
    evaluate_ho_cumulative_health,
    format_local_datetime,
    format_local_time,
    health_risk_notes,
    log_audit,
    parse_monthly_target_amounts,
    parse_resubmit_changes,
    serialize_ceo_profile,
    shift_month,
    todays_submission_events,
    resolve_financial_status,
    social_quadrant_status,
    step_status,
    today_local,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
require_admin = require_roles("Chairman", "HO")
# Chairman-only: HO Staff is included in require_admin for its own oversight
# pages, but must not be able to reach the escape-hatch override for its own
# monthly target — that would let it bypass the once-a-month lock enforced by
# its own /api/ho/monthly-target. The frontend never wires the override form
# up for an HO viewer (see HeadOfficePage.jsx's isHo branch), but that's not
# real enforcement — a direct API call could still hit it, so the lock has to
# live here too.
require_chairman = require_roles("Chairman")


def compute_period_totals(db: Session, target_date):
    row = (
        db.query(
            func.coalesce(func.sum(DailyFinancial.revenue), 0),
            func.coalesce(func.sum(DailyFinancial.expenses), 0),
        )
        .filter(DailyFinancial.report_date == target_date)
        .first()
    )
    revenue = float(row[0] or 0)
    expenses = float(row[1] or 0)
    return revenue, expenses, revenue - expenses


def compute_period_cash(db: Session, target_date) -> float:
    total = (
        db.query(func.coalesce(func.sum(DailyFinancial.cash_in_hand), 0))
        .filter(DailyFinancial.report_date == target_date)
        .scalar()
    )
    return float(total or 0)


def compute_period_column_sum(db: Session, column, target_date) -> float:
    total = db.query(func.coalesce(func.sum(column), 0)).filter(DailyFinancial.report_date == target_date).scalar()
    return float(total or 0)


def pct_change(current: float, previous: float) -> Optional[float]:
    if previous == 0:
        return None
    return round(((current - previous) / abs(previous)) * 100, 1)


_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_excel_cell(value: Optional[str]) -> Optional[str]:
    """Neutralizes formula-injection payloads (CWE-1236) in free-text fields
    before they're written into an exported spreadsheet. These fields are
    submitted by CEO accounts and read by Chairman/HO — a value starting with
    "=" is written by openpyxl as an actual live formula (verified: it sets
    the cell's data_type to "f"), so a malicious or compromised CEO account
    could plant something like =HYPERLINK(...) that runs when a higher-privilege
    user opens the export. Prefixing a single quote forces spreadsheet
    applications to treat the value as literal text."""
    if not value or not value.startswith(_FORMULA_TRIGGER_CHARS):
        return value
    return "'" + value


def gather_admin_overview(db: Session) -> dict:
    """Single shared computation of all cross-BU data, reused by every
    admin-facing endpoint (landing dashboard, comparison, operations grid) so
    each route stays a thin view over one consistent dataset."""
    today = today_local()
    yesterday = today - timedelta(days=1)

    business_units = db.query(BusinessUnit).order_by(BusinessUnit.id).all()
    # Active CEOs only, oldest first: a deactivated predecessor must never
    # shadow the current CEO of a unit, and if two active accounts ever share
    # a unit the newest one (last write wins) is the one shown.
    ceo_by_bu = {
        u.business_unit_id: u
        for u in db.query(User).filter(User.role == "CEO", User.is_active == True).order_by(User.id).all()  # noqa: E712
    }
    ceo_profiles_by_user = {p.user_id: p for p in db.query(CeoProfile).all()}

    totals_row = db.query(
        func.coalesce(func.sum(DailyFinancial.revenue), 0),
        func.coalesce(func.sum(DailyFinancial.expenses), 0),
    ).first()
    total_revenue = float(totals_row[0] or 0)
    total_expenses = float(totals_row[1] or 0)
    group_net_profit = total_revenue - total_expenses

    latest_financial_by_bu = {}
    for bu in business_units:
        latest = (
            db.query(DailyFinancial)
            .filter(DailyFinancial.business_unit_id == bu.id)
            .order_by(DailyFinancial.report_date.desc())
            .first()
        )
        if latest:
            latest_financial_by_bu[bu.id] = latest

    # Activity logs are submitted every day (weekday and weekend alike), so they are
    # the reliable source for "when was this BU last reported" — financials alone
    # go quiet on weekends even though the CEO still submitted their daily report.
    latest_activity_by_bu = {}
    for bu in business_units:
        latest = (
            db.query(ActivityLog)
            .filter(ActivityLog.business_unit_id == bu.id)
            .order_by(ActivityLog.report_date.desc())
            .first()
        )
        if latest:
            latest_activity_by_bu[bu.id] = latest

    today_financial_by_bu = {
        row.business_unit_id: row
        for row in db.query(DailyFinancial).filter(DailyFinancial.report_date == today).all()
    }
    today_activity_by_bu = {
        row.business_unit_id: row
        for row in db.query(ActivityLog).filter(ActivityLog.report_date == today).all()
    }

    latest_social_by_bu = {}
    for bu in business_units:
        latest = (
            db.query(SocialLog)
            .filter(SocialLog.business_unit_id == bu.id)
            .order_by(SocialLog.report_date.desc())
            .first()
        )
        if latest:
            latest_social_by_bu[bu.id] = latest

    total_cash_in_hand = sum(float(f.cash_in_hand) for f in latest_financial_by_bu.values())
    total_cash_flow = sum(float(f.cash_flow) for f in latest_financial_by_bu.values() if f.cash_flow is not None)
    total_receivable = sum(float(f.receivable) for f in latest_financial_by_bu.values() if f.receivable is not None)
    total_payable = sum(float(f.payable) for f in latest_financial_by_bu.values() if f.payable is not None)

    today_revenue, today_expenses, today_net = compute_period_totals(db, today)
    yesterday_revenue, yesterday_expenses, yesterday_net = compute_period_totals(db, yesterday)
    today_cash = compute_period_cash(db, today)
    yesterday_cash = compute_period_cash(db, yesterday)
    today_cash_flow = compute_period_column_sum(db, DailyFinancial.cash_flow, today)
    yesterday_cash_flow = compute_period_column_sum(db, DailyFinancial.cash_flow, yesterday)
    today_receivable = compute_period_column_sum(db, DailyFinancial.receivable, today)
    yesterday_receivable = compute_period_column_sum(db, DailyFinancial.receivable, yesterday)
    today_payable = compute_period_column_sum(db, DailyFinancial.payable, today)
    yesterday_payable = compute_period_column_sum(db, DailyFinancial.payable, yesterday)

    kpi = {
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "group_net_profit": group_net_profit,
        "total_cash_in_hand": total_cash_in_hand,
        "total_cash_flow": total_cash_flow,
        "total_receivable": total_receivable,
        "total_payable": total_payable,
        "revenue_trend": pct_change(today_revenue, yesterday_revenue),
        "expenses_trend": pct_change(today_expenses, yesterday_expenses),
        "net_profit_trend": pct_change(today_net, yesterday_net),
        "cash_trend": pct_change(today_cash, yesterday_cash),
        "cash_flow_trend": pct_change(today_cash_flow, yesterday_cash_flow),
        "receivable_trend": pct_change(today_receivable, yesterday_receivable),
        "payable_trend": pct_change(today_payable, yesterday_payable),
    }

    # --- CEO well-being matrix (Health input) ---
    wellbeing = []
    latest_health_by_bu = {}
    for bu in business_units:
        latest_health = (
            db.query(HealthStatus)
            .filter(HealthStatus.business_unit_id == bu.id)
            .order_by(HealthStatus.report_date.desc())
            .first()
        )
        if latest_health:
            latest_health_by_bu[bu.id] = latest_health
        ceo_user = ceo_by_bu.get(bu.id)
        risk = compute_health_risk(latest_health)

        risk_notes = health_risk_notes(latest_health)
        wellbeing.append(
            {
                "bu_id": bu.id,
                "bu_code": bu.code,
                "bu_name": bu.name,
                "ceo_username": ceo_user.username if ceo_user else "-",
                "daily_steps": latest_health.daily_steps if latest_health else None,
                "heart_rate": latest_health.heart_rate if latest_health else None,
                "bp_systolic": latest_health.bp_systolic if latest_health else None,
                "bp_diastolic": latest_health.bp_diastolic if latest_health else None,
                "risk": risk,
                "risk_reason": ", ".join(risk_notes) if risk_notes else "Elevated risk factors",
                "step_status": step_status(latest_health.daily_steps) if latest_health else None,
                "report_date": latest_health.report_date.isoformat() if latest_health else None,
                "reported_display": format_local_datetime(latest_health.updated_at) if latest_health else None,
            }
        )

    critical_ceos = [w for w in wellbeing if w["risk"] == "red"]

    # --- Financial alerts: red = missing mandatory submission (every day, no
    # weekend exception), or the latest submitted figures are RED per
    # evaluate_financial_health() (cash flow negative) — the same
    # revenue/cash_flow/receivable/payable-driven check the Financial
    # quadrant circle uses (via resolve_financial_status below), so this
    # alert and that circle can never disagree. Used to check
    # latest_fin.net_profit instead, but submit_report() has zeroed that
    # legacy column on every write since the form dropped it, which made
    # this branch permanently unreachable. ---
    financial_alerts = []
    for bu in business_units:
        fin_today = today_financial_by_bu.get(bu.id)
        latest_fin = latest_financial_by_bu.get(bu.id)
        reason = None
        alert_type = None
        if fin_today is None:
            reason = "Missing mandatory financial submission"
            alert_type = "missing"
        elif latest_fin is not None and latest_fin.cash_flow is not None:
            status_code, _, warning_flags = evaluate_financial_health(
                latest_fin.revenue, latest_fin.cash_flow, latest_fin.receivable, latest_fin.payable
            )
            if status_code == "RED":
                reason = warning_flags[0] if warning_flags else "Latest submission shows critical financial risk"
                alert_type = "loss"
        if reason:
            financial_alerts.append(
                {
                    "bu_id": bu.id,
                    "bu_code": bu.code,
                    "bu_name": bu.name,
                    "ceo_username": ceo_by_bu[bu.id].username if bu.id in ceo_by_bu else "-",
                    "reason": reason,
                    "alert_type": alert_type,
                    "cash_flow": float(latest_fin.cash_flow) if latest_fin and latest_fin.cash_flow is not None else None,
                }
            )

    # --- Activity alerts: red = no activity/health report at all today ---
    activity_alerts = []
    for bu in business_units:
        if bu.id not in today_activity_by_bu:
            activity_alerts.append(
                {
                    "bu_id": bu.id,
                    "bu_code": bu.code,
                    "bu_name": bu.name,
                    "ceo_username": ceo_by_bu[bu.id].username if bu.id in ceo_by_bu else "-",
                    "reason": "No report submitted today",
                }
            )

    submission_compliance = {
        "submitted": len(today_activity_by_bu),
        "total": len(business_units),
        "pct": round((len(today_activity_by_bu) / len(business_units)) * 100, 1) if business_units else 0,
    }
    yesterday_activity_count = db.query(ActivityLog).filter(ActivityLog.report_date == yesterday).count()
    compliance_yesterday_pct = (
        round((yesterday_activity_count / len(business_units)) * 100, 1) if business_units else 0
    )
    submission_compliance["trend_pts"] = round(submission_compliance["pct"] - compliance_yesterday_pct, 1)

    # --- Average net margin (net profit as % of revenue), today vs yesterday ---
    margin_today = (today_net / today_revenue * 100) if today_revenue else None
    margin_yesterday = (yesterday_net / yesterday_revenue * 100) if yesterday_revenue else None
    kpi["avg_net_margin"] = round(margin_today, 1) if margin_today is not None else 0
    kpi["margin_trend_pts"] = (
        round(margin_today - margin_yesterday, 1)
        if margin_today is not None and margin_yesterday is not None
        else None
    )

    # --- Chart 1: last 14 days of cash flow, broken down per business unit so
    # the Comparison page can show one BU's own trend at a time rather than a
    # single group-wide line. ---
    chart_days = [today - timedelta(days=offset) for offset in range(13, -1, -1)]
    trend_labels = [d.strftime("%d %b") for d in chart_days]

    trend_rows = (
        db.query(
            DailyFinancial.business_unit_id,
            DailyFinancial.report_date,
            DailyFinancial.revenue,
            DailyFinancial.cash_flow,
            DailyFinancial.receivable,
            DailyFinancial.payable,
        )
        .filter(DailyFinancial.report_date >= chart_days[0], DailyFinancial.report_date <= chart_days[-1])
        .all()
    )
    vals_by_bu_date: dict = defaultdict(dict)
    for bu_id, report_date, revenue, cash_flow, receivable, payable in trend_rows:
        vals_by_bu_date[bu_id][report_date] = {
            "revenue": float(revenue) if revenue is not None else 0.0,
            "cash_flow": float(cash_flow) if cash_flow is not None else 0.0,
            "receivable": float(receivable) if receivable is not None else 0.0,
            "payable": float(payable) if payable is not None else 0.0,
        }

    def _series(bu_id, key):
        return [round(vals_by_bu_date.get(bu_id, {}).get(d, {}).get(key, 0.0), 2) for d in chart_days]

    trend_by_bu = [
        {
            "bu_id": bu.id,
            "bu_code": bu.code,
            "bu_name": bu.name,
            "revenue": _series(bu.id, "revenue"),
            "cash_flow": _series(bu.id, "cash_flow"),
            "receivable": _series(bu.id, "receivable"),
            "payable": _series(bu.id, "payable"),
        }
        for bu in business_units
    ]
    # Kept for backwards compatibility; new clients use trend_by_bu.
    trend_cash_flow_by_bu = [
        {"bu_id": b["bu_id"], "bu_code": b["bu_code"], "bu_name": b["bu_name"], "cash_flow": b["cash_flow"]}
        for b in trend_by_bu
    ]

    # --- Chart 2: grouped bar chart, latest receivable vs payable per BU ---
    bar_categories = [bu.code for bu in business_units]
    bar_receivable = [round(float(latest_financial_by_bu[bu.id].receivable), 2) if bu.id in latest_financial_by_bu and latest_financial_by_bu[bu.id].receivable is not None else 0 for bu in business_units]
    bar_payable = [round(float(latest_financial_by_bu[bu.id].payable), 2) if bu.id in latest_financial_by_bu and latest_financial_by_bu[bu.id].payable is not None else 0 for bu in business_units]

    # --- Actual-vs-CEO-target budget status, shared by every BU grid below
    # (Executive Dashboard, Comparison, Operations) so the same red/yellow/
    # green verdict against the monthly target is visible everywhere, not
    # just buried in Business Unit Detail's CEO-Set Benchmark card.
    #
    # Single-step Cumulative Financial Health: Month-to-Date Cumulative Net
    # Revenue (Sum(Revenue) + Sum(Other Amount) from day 1 through today) vs
    # the CEO's monthly revenue target, pro-rated to today's day-of-month —
    # see evaluate_cumulative_financial_health for the full rules. Computed
    # here as one bulk grouped query across all 23 BUs (compute_mtd_cumulative_by_bu)
    # rather than 23 separate per-BU sums. ---
    month_start = date(today.year, today.month, 1)
    current_month_targets = {
        t.business_unit_id: t
        for t in db.query(MonthlyTarget).filter(MonthlyTarget.target_month == month_start).all()
    }
    cumulative_by_bu = compute_mtd_cumulative_by_bu(db, [bu.id for bu in business_units], month_start, today)

    # Record Notes count this month, per BU — a single cheap grouped COUNT
    # rather than fetching every note's full text for all 23 BUs just to show
    # a "N notes" indicator on the overview; the full log is one click away
    # via "View Full History" (Business Unit Detail).
    notes_count_by_bu = {
        bu_id: count
        for bu_id, count in (
            db.query(FinancialNote.business_unit_id, func.count(FinancialNote.id))
            .filter(FinancialNote.note_date >= month_start)
            .group_by(FinancialNote.business_unit_id)
            .all()
        )
    }

    budget_by_bu = {}
    for bu in business_units:
        target = current_month_targets.get(bu.id)
        bu_totals = cumulative_by_bu.get(bu.id, {})
        perf = evaluate_cumulative_financial_health(bu_totals.get("total", 0.0), target, today) if target else None
        if perf:
            budget_by_bu[bu.id] = {
                "budget_status": perf["status"].lower(),
                "achievement_pct": perf["achievement_pct"],
                "mtd_revenue": bu_totals.get("revenue", 0.0),
                "mtd_other_amount": bu_totals.get("other_amount", 0.0),
                "cumulative_net_revenue": perf["cumulative_net_revenue"],
                "variance_amount": perf["variance_amount"],
            }
        else:
            budget_by_bu[bu.id] = {
                "budget_status": "grey",
                "achievement_pct": None,
                "mtd_revenue": bu_totals.get("revenue", 0.0),
                "mtd_other_amount": bu_totals.get("other_amount", 0.0),
                "cumulative_net_revenue": bu_totals.get("total", 0.0),
                "variance_amount": None,
            }
        budget_by_bu[bu.id]["financial_notes_count"] = notes_count_by_bu.get(bu.id, 0)

    # --- Business Unit Comparison dataset ---
    comparison_data = []
    for bu in business_units:
        fin = latest_financial_by_bu.get(bu.id)
        comparison_data.append(
            {
                "bu_id": bu.id,
                "code": bu.code,
                "name": bu.name,
                "revenue": round(float(fin.revenue), 2) if fin and fin.revenue is not None else 0,
                "cash_flow": round(float(fin.cash_flow), 2) if fin and fin.cash_flow is not None else 0,
                "receivable": round(float(fin.receivable), 2) if fin and fin.receivable is not None else 0,
                "payable": round(float(fin.payable), 2) if fin and fin.payable is not None else 0,
                "report_date": fin.report_date.isoformat() if fin else None,
                **budget_by_bu[bu.id],
            }
        )

    # --- Operations grid ---
    operations = []
    for bu in business_units:
        fin = latest_financial_by_bu.get(bu.id)
        today_fin = today_financial_by_bu.get(bu.id)
        latest_activity = latest_activity_by_bu.get(bu.id)
        latest_social = latest_social_by_bu.get(bu.id)
        latest_health = latest_health_by_bu.get(bu.id)
        activity_today = today_activity_by_bu.get(bu.id)
        wb_entry = next((w for w in wellbeing if w["bu_code"] == bu.code), None)
        health_status = wb_entry["risk"] if wb_entry else "unknown"
        operations.append(
            {
                "bu_id": bu.id,
                "bu_code": bu.code,
                "bu_name": bu.name,
                "ceo_username": wb_entry["ceo_username"] if wb_entry else "-",
                "ceo_profile": serialize_ceo_profile(
                    ceo_by_bu.get(bu.id),
                    ceo_profiles_by_user.get(ceo_by_bu[bu.id].id) if bu.id in ceo_by_bu else None,
                    today,
                ),
                "report_date": latest_activity.report_date.strftime("%d %b %Y") if latest_activity else None,
                "report_time": format_local_time(latest_activity.updated_at) if latest_activity else None,
                "revenue": float(fin.revenue) if fin else None,
                "expenses": float(fin.expenses) if fin else None,
                "net_profit": float(fin.net_profit) if fin else None,
                "cash_in_hand": float(fin.cash_in_hand) if fin else None,
                "cash_flow": float(fin.cash_flow) if fin and fin.cash_flow is not None else None,
                "receivable": float(fin.receivable) if fin and fin.receivable is not None else None,
                "payable": float(fin.payable) if fin and fin.payable is not None else None,
                "other_amount": float(fin.other_amount) if fin and fin.other_amount is not None else None,
                "daily_steps": latest_health.daily_steps if latest_health else None,
                "step_status": step_status(latest_health.daily_steps) if latest_health else None,
                "heart_rate": latest_health.heart_rate if latest_health else None,
                "sleep_hours": float(latest_health.sleep_hours) if latest_health and latest_health.sleep_hours is not None else None,
                "bp_systolic": latest_health.bp_systolic if latest_health else None,
                "bp_diastolic": latest_health.bp_diastolic if latest_health else None,
                "health_other_notes": latest_health.other_notes if latest_health else None,
                "event_flag": latest_social.event_flag if latest_social else None,
                "meeting_flag": latest_social.meeting_flag if latest_social else None,
                "golf_flag": latest_social.golf_flag if latest_social else None,
                "social_other_notes": latest_social.other_notes if latest_social else None,
                "gym_percent": latest_activity.gym_percent if latest_activity else None,
                "swimming_percent": latest_activity.swimming_percent if latest_activity else None,
                "cycle_percent": latest_activity.cycle_percent if latest_activity else None,
                "activity_other_notes": latest_activity.other_notes if latest_activity else None,
                "risk": health_status,
                "submitted_today": activity_today is not None,
                "submitted_today_time": format_local_time(activity_today.updated_at) if activity_today else None,
                # 4-quadrant BU health circle: Financial (top-left) / Social (top-right) /
                # Activity (bottom-left) / Health (bottom-right). "unknown" from compute_health_risk
                # is normalized to "grey" here so the frontend only ever sees the 4 quadrant colors.
                # Same resolve_financial_status() helper Business Unit Detail uses, so the two
                # can never disagree about the same BU on the same day.
                "financial_status": resolve_financial_status(fin, today_fin, budget_by_bu[bu.id]["budget_status"]),
                "social_status": social_quadrant_status(latest_social),
                "activity_status": activity_quadrant_status(latest_activity),
                "health_status": "grey" if health_status == "unknown" else health_status,
                # Real actual-vs-CEO-target status (red/yellow/green), separate from
                # financial_status above (which is cash-flow health, not budget adherence).
                **budget_by_bu[bu.id],
            }
        )

    # --- Missing submissions for the current operational week (Mon..today) ---
    week_start = today - timedelta(days=today.weekday())
    week_days = [week_start + timedelta(days=i) for i in range((today - week_start).days + 1)]

    missing_submissions = []
    for bu in business_units:
        submitted_dates = {
            row.report_date
            for row in db.query(ActivityLog.report_date)
            .filter(ActivityLog.business_unit_id == bu.id, ActivityLog.report_date >= week_start)
            .all()
        }
        missing_days = [d for d in week_days if d not in submitted_dates]
        if missing_days:
            missing_submissions.append(
                {
                    "bu_id": bu.id,
                    "bu_code": bu.code,
                    "bu_name": bu.name,
                    "missing_dates": [d.strftime("%a %d %b") for d in missing_days],
                    "missing_day_labels": [d.strftime("%a") for d in missing_days],
                    "missing_count": len(missing_days),
                    "days_in_week_so_far": len(week_days),
                }
            )

    return {
        "today": today.isoformat(),
        "today_display": today.strftime("%A, %d %B %Y"),
        "kpi": kpi,
        "wellbeing": wellbeing,
        "critical_ceos": critical_ceos,
        "financial_alerts": financial_alerts,
        "activity_alerts": activity_alerts,
        "submission_compliance": submission_compliance,
        "operations": operations,
        "missing_submissions": missing_submissions,
        "trend_labels": trend_labels,
        "trend_cash_flow_by_bu": trend_cash_flow_by_bu,
        "trend_by_bu": trend_by_bu,
        "bar_categories": bar_categories,
        "bar_receivable": bar_receivable,
        "bar_payable": bar_payable,
        "comparison_data": comparison_data,
    }


@router.get("/overview")
def admin_overview(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    ov = gather_admin_overview(db)
    local_hour = datetime.now(APP_TZ).hour
    if local_hour < 12:
        greeting = "Good morning"
    elif local_hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    ov["greeting"] = greeting
    ov["generated_at"] = format_local_datetime(datetime.utcnow(), "%I:%M %p")
    return ov


@router.get("/comparison")
def admin_comparison(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    ov = gather_admin_overview(db)
    return {
        "today_display": ov["today_display"],
        "trend_labels": ov["trend_labels"],
        "trend_cash_flow_by_bu": ov["trend_cash_flow_by_bu"],
        "trend_by_bu": ov["trend_by_bu"],
        "bar_categories": ov["bar_categories"],
        "bar_receivable": ov["bar_receivable"],
        "bar_payable": ov["bar_payable"],
        "comparison_data": ov["comparison_data"],
    }


@router.get("/operations")
def admin_operations(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    ov = gather_admin_overview(db)
    return {
        "today_display": ov["today_display"],
        "today_iso": ov["today"],
        "operations": ov["operations"],
    }


@router.get("/business-unit/{bu_id}")
def business_unit_detail(bu_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == bu_id).first()
    if not bu:
        raise HTTPException(status_code=404, detail="Business unit not found")

    # Newest ACTIVE CEO of the unit (same rule as ceo_by_bu elsewhere): a
    # deactivated predecessor must not shadow the current CEO.
    ceo_user = (
        db.query(User)
        .filter(User.business_unit_id == bu_id, User.role == "CEO", User.is_active == True)  # noqa: E712
        .order_by(User.id.desc())
        .first()
    )
    ceo_profile_row = db.query(CeoProfile).filter(CeoProfile.user_id == ceo_user.id).first() if ceo_user else None
    today = today_local()

    # Limit is 62 (2 months' worth) rather than 30 — Day-by-Day Review's
    # per-row Cumulative Net Revenue (build_financial_rows) needs every day of
    # a row's own calendar month present in this list to sum correctly, and a
    # 31-day month plus a few days of browsing into the previous month would
    # otherwise get truncated at 30.
    financial_history = (
        db.query(DailyFinancial).filter(DailyFinancial.business_unit_id == bu_id)
        .order_by(DailyFinancial.report_date.desc()).limit(62).all()
    )
    activity_history = (
        db.query(ActivityLog).filter(ActivityLog.business_unit_id == bu_id)
        .order_by(ActivityLog.report_date.desc()).limit(30).all()
    )
    health_history = (
        db.query(HealthStatus).filter(HealthStatus.business_unit_id == bu_id)
        .order_by(HealthStatus.report_date.desc()).limit(30).all()
    )
    social_history = (
        db.query(SocialLog).filter(SocialLog.business_unit_id == bu_id)
        .order_by(SocialLog.report_date.desc()).limit(30).all()
    )

    latest_financial = financial_history[0] if financial_history else None
    latest_activity = activity_history[0] if activity_history else None
    latest_health = health_history[0] if health_history else None
    latest_social = social_history[0] if social_history else None

    today_financial = next((f for f in financial_history if f.report_date == today), None)
    today_activity = next((a for a in activity_history if a.report_date == today), None)
    submitted_today = today_activity is not None

    monthly_target = (
        db.query(MonthlyTarget)
        .filter(MonthlyTarget.business_unit_id == bu_id, MonthlyTarget.target_month == date(today.year, today.month, 1))
        .first()
    )
    cumulative_performance = evaluate_cumulative_health(db, bu_id, date(today.year, today.month, 1), today)
    monthly_social = compute_monthly_social_counts(
        db, SocialLog, [SocialLog.business_unit_id == bu_id], date(today.year, today.month, 1), today
    )

    financial_notes = (
        db.query(FinancialNote)
        .filter(FinancialNote.business_unit_id == bu_id, FinancialNote.note_date >= date(today.year, today.month, 1))
        .order_by(FinancialNote.note_date.desc(), FinancialNote.created_at.desc())
        .all()
    )

    # The Day-by-Day Review lets the viewer step through past submissions, so
    # the "vs monthly target" evaluation needs to be recomputed per row against
    # THAT row's own month's target — not just re-show today's target/result —
    # otherwise the benchmark badge looks frozen while every other field in the
    # review changes as you step between days. History can span a month
    # boundary, so targets are looked up per distinct (year, month) present.
    target_months = {date(f.report_date.year, f.report_date.month, 1) for f in financial_history}
    targets_by_month = {}
    if target_months:
        for t in db.query(MonthlyTarget).filter(MonthlyTarget.business_unit_id == bu_id, MonthlyTarget.target_month.in_(target_months)).all():
            targets_by_month[t.target_month] = t

    # 4-quadrant statuses — one shared helper (also used by the CEO's own
    # dashboard) so this page never drifts from what a CEO sees for their own
    # unit, or from the Admin Dashboard / Operations grid for the same BU.
    budget_status = cumulative_performance["status"].lower() if cumulative_performance else "grey"
    quadrant_summary = compute_bu_quadrant_summary(
        latest_financial, today_financial, latest_activity, latest_social, latest_health, budget_status=budget_status
    )
    financial_status = quadrant_summary["financial_status"]
    financial_reason = quadrant_summary["financial_reason"]
    social_status = quadrant_summary["social_status"]
    social_reason = quadrant_summary["social_reason"]
    activity_status = quadrant_summary["activity_status"]
    activity_reason = quadrant_summary["activity_reason"]
    health_status = quadrant_summary["health_status"]
    health_reason = quadrant_summary["health_reason"]

    return {
        "bu": {"id": bu.id, "code": bu.code, "name": bu.name},
        "today": today.isoformat(),
        "ceo_username": ceo_user.username if ceo_user else "-",
        # Same shape as the Executive Dashboard/Operations grid and the
        # standalone CEO Profiles list (serialize_ceo_profile), so clicking
        # into a BU from any of them shows the CEO's profile right alongside
        # their operational dashboard instead of a separate page.
        "ceo_profile": serialize_ceo_profile(ceo_user, ceo_profile_row, today),
        "submitted_today": submitted_today,
        "submission_events_today": todays_submission_events(db, ceo_user.id if ceo_user else None, "DAILY_REPORT_SUBMITTED", today),
        "financial_status": financial_status,
        "financial_reason": financial_reason,
        "social_status": social_status,
        "social_reason": social_reason,
        "monthly_event_count": monthly_social["event_count"],
        "monthly_meeting_count": monthly_social["meeting_count"],
        "monthly_golf_count": monthly_social["golf_count"],
        "monthly_days_submitted": monthly_social["days_submitted"],
        "activity_status": activity_status,
        "activity_reason": activity_reason,
        "health_status": health_status,
        "health_reason": health_reason,
        "financial_rows": build_financial_rows(financial_history, targets_by_month),
        "activity_rows": build_activity_rows(activity_history),
        "health_rows": build_health_rows(health_history),
        "social_rows": build_social_rows(social_history),
        "monthly_target": {
            "target_month": monthly_target.target_month.isoformat(),
            "monthly_revenue_target": float(monthly_target.monthly_revenue_target),
            "monthly_cash_flow_target": float(monthly_target.monthly_cash_flow_target),
            "monthly_max_receivable": float(monthly_target.monthly_max_receivable),
            "monthly_max_payable": float(monthly_target.monthly_max_payable),
        } if monthly_target else None,
        "cumulative_performance": cumulative_performance,
        # Read-only here — CEOs add these from their own dashboard
        # (POST /api/ceo/financial-notes); Chairman/HO just review them.
        "financial_notes": [
            {"note_date": n.note_date.isoformat(), "note_text": n.note_text, "created_at": format_local_datetime(n.created_at)}
            for n in financial_notes
        ],
    }


@router.get("/head-office")
def head_office_report(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Chairman/HO view of HO Staff's own daily report — same 4 areas a CEO
    reports (Business Performance/Social/Activity/Health), including the
    monthly target and Cumulative Net Revenue vs Target tracking. Deliberately
    kept off the 23-BU pages (Operations/Comparison/Excel export) since it
    isn't one of the 23 business units — see HeadOfficeActivityLog's
    docstring for why this is its own table rather than a special
    BusinessUnit row."""
    today = today_local()

    today_financial = db.query(HeadOfficeDailyFinancial).filter(HeadOfficeDailyFinancial.report_date == today).first()
    today_activity = db.query(HeadOfficeActivityLog).filter(HeadOfficeActivityLog.report_date == today).first()

    latest_financial = db.query(HeadOfficeDailyFinancial).order_by(HeadOfficeDailyFinancial.report_date.desc()).first()
    latest_activity = db.query(HeadOfficeActivityLog).order_by(HeadOfficeActivityLog.report_date.desc()).first()
    latest_health = db.query(HeadOfficeHealthStatus).order_by(HeadOfficeHealthStatus.report_date.desc()).first()
    latest_social = db.query(HeadOfficeSocialLog).order_by(HeadOfficeSocialLog.report_date.desc()).first()

    current_month_start = date(today.year, today.month, 1)
    monthly_target = (
        db.query(HeadOfficeMonthlyTarget)
        .filter(HeadOfficeMonthlyTarget.target_month == current_month_start)
        .first()
    )
    cumulative_performance = evaluate_ho_cumulative_health(db, monthly_target, current_month_start, today)
    budget_status = cumulative_performance["status"].lower() if cumulative_performance else "grey"

    quadrant = compute_ho_quadrant_summary(latest_financial, today_financial, latest_activity, latest_social, latest_health, budget_status=budget_status)
    monthly_social = compute_monthly_social_counts(db, HeadOfficeSocialLog, [], current_month_start, today)

    financial_history = db.query(HeadOfficeDailyFinancial).order_by(HeadOfficeDailyFinancial.report_date.desc()).limit(62).all()
    activity_history = db.query(HeadOfficeActivityLog).order_by(HeadOfficeActivityLog.report_date.desc()).limit(30).all()
    health_history = db.query(HeadOfficeHealthStatus).order_by(HeadOfficeHealthStatus.report_date.desc()).limit(30).all()
    social_history = db.query(HeadOfficeSocialLog).order_by(HeadOfficeSocialLog.report_date.desc()).limit(30).all()

    target_months = {date(f.report_date.year, f.report_date.month, 1) for f in financial_history}
    targets_by_month = {}
    if target_months:
        for t in db.query(HeadOfficeMonthlyTarget).filter(HeadOfficeMonthlyTarget.target_month.in_(target_months)).all():
            targets_by_month[t.target_month] = t

    ho_staff = db.query(User).filter(User.role == "HO").order_by(User.id).first()

    return {
        "today": today.isoformat(),
        "ho_username": ho_staff.username if ho_staff else "-",
        "submitted_today": today_activity is not None,
        "submission_events_today": todays_submission_events(db, ho_staff.id if ho_staff else None, "HO_DAILY_REPORT_SUBMITTED", today),
        "financial_status": quadrant["financial_status"],
        "financial_reason": quadrant["financial_reason"],
        "social_status": quadrant["social_status"],
        "social_reason": quadrant["social_reason"],
        "activity_status": quadrant["activity_status"],
        "activity_reason": quadrant["activity_reason"],
        "health_status": quadrant["health_status"],
        "health_reason": quadrant["health_reason"],
        "budget_status": budget_status,
        "mtd_revenue": cumulative_performance["mtd_revenue"] if cumulative_performance else None,
        "mtd_other_amount": cumulative_performance["mtd_other_amount"] if cumulative_performance else None,
        "cumulative_net_revenue": cumulative_performance["cumulative_net_revenue"] if cumulative_performance else None,
        "achievement_pct": cumulative_performance["achievement_pct"] if cumulative_performance else None,
        "variance_amount": cumulative_performance["variance_amount"] if cumulative_performance else None,
        "revenue": float(latest_financial.revenue) if latest_financial else None,
        "cash_flow": float(latest_financial.cash_flow) if latest_financial and latest_financial.cash_flow is not None else None,
        "receivable": float(latest_financial.receivable) if latest_financial and latest_financial.receivable is not None else None,
        "payable": float(latest_financial.payable) if latest_financial and latest_financial.payable is not None else None,
        "other_amount": float(latest_financial.other_amount) if latest_financial and latest_financial.other_amount is not None else None,
        "financial_other_notes": latest_financial.other_notes if latest_financial else None,
        "gym_percent": latest_activity.gym_percent if latest_activity else None,
        "swimming_percent": latest_activity.swimming_percent if latest_activity else None,
        "cycle_percent": latest_activity.cycle_percent if latest_activity else None,
        "activity_other_notes": latest_activity.other_notes if latest_activity else None,
        "event_flag": latest_social.event_flag if latest_social else None,
        "meeting_flag": latest_social.meeting_flag if latest_social else None,
        "golf_flag": latest_social.golf_flag if latest_social else None,
        "social_other_notes": latest_social.other_notes if latest_social else None,
        "monthly_event_count": monthly_social["event_count"],
        "monthly_meeting_count": monthly_social["meeting_count"],
        "monthly_golf_count": monthly_social["golf_count"],
        "monthly_days_submitted": monthly_social["days_submitted"],
        "daily_steps": latest_health.daily_steps if latest_health else None,
        "heart_rate": latest_health.heart_rate if latest_health else None,
        "sleep_hours": float(latest_health.sleep_hours) if latest_health and latest_health.sleep_hours is not None else None,
        "bp_systolic": latest_health.bp_systolic if latest_health else None,
        "bp_diastolic": latest_health.bp_diastolic if latest_health else None,
        "health_other_notes": latest_health.other_notes if latest_health else None,
        "monthly_target": {
            "target_month": monthly_target.target_month.isoformat(),
            "monthly_revenue_target": float(monthly_target.monthly_revenue_target),
            "monthly_cash_flow_target": float(monthly_target.monthly_cash_flow_target),
            "monthly_max_receivable": float(monthly_target.monthly_max_receivable),
            "monthly_max_payable": float(monthly_target.monthly_max_payable),
        } if monthly_target else None,
        "cumulative_performance": cumulative_performance,
        "financial_rows": build_financial_rows(financial_history, targets_by_month),
        "activity_rows": build_activity_rows(activity_history),
        "health_rows": build_health_rows(health_history),
        "social_rows": build_social_rows(social_history),
    }


@router.post("/head-office/monthly-target")
def override_ho_monthly_target(
    payload: MonthlyTargetRequest,
    request: Request,
    user: User = Depends(require_chairman),
    db: Session = Depends(get_db),
):
    """Chairman/HO override for Head Office's monthly target — unlike HO's own
    /api/ho/monthly-target (which locks after the first save this month), this
    always upserts, mirroring the business-unit override endpoint above."""
    try:
        target_month = date.fromisoformat(payload.target_month)
        if target_month.day != 1:
            target_month = target_month.replace(day=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="target_month must be a valid ISO date")

    # 400 on NaN / Infinity / abs >= 1e13 / negative revenue-receivable-payable.
    amounts = parse_monthly_target_amounts(payload)

    target = (
        db.query(HeadOfficeMonthlyTarget)
        .filter(HeadOfficeMonthlyTarget.target_month == target_month)
        .with_for_update()
        .first()
    )
    if target is None:
        target = HeadOfficeMonthlyTarget(target_month=target_month)
        db.add(target)

    target.monthly_revenue_target = amounts["monthly_revenue_target"]
    target.monthly_cash_flow_target = amounts["monthly_cash_flow_target"]
    target.monthly_max_receivable = amounts["monthly_max_receivable"]
    target.monthly_max_payable = amounts["monthly_max_payable"]
    target.set_by = user.id

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This month's target was just updated elsewhere — please try again.")

    log_audit(db, user, "HO_MONTHLY_TARGET_OVERRIDE", f"month={target_month.isoformat()}", request)

    return {"ok": True, "target_month": target_month.isoformat()}


@router.post("/business-unit/{bu_id}/monthly-target")
def override_monthly_target(
    bu_id: int,
    payload: MonthlyTargetRequest,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Chairman/HO override for a business unit's monthly target — unlike the
    CEO's own /api/ceo/monthly-target (which locks after the first save this
    month), this always upserts, since correcting a mistaken or unfair target
    is exactly the escape hatch the lock is meant to require going through
    Head Office for."""
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == bu_id).first()
    if not bu:
        raise HTTPException(status_code=404, detail="Business unit not found")

    try:
        target_month = date.fromisoformat(payload.target_month)
        if target_month.day != 1:
            target_month = target_month.replace(day=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="target_month must be a valid ISO date")

    # 400 on NaN / Infinity / abs >= 1e13 / negative revenue-receivable-payable.
    amounts = parse_monthly_target_amounts(payload)

    target = (
        db.query(MonthlyTarget)
        .filter(MonthlyTarget.business_unit_id == bu_id, MonthlyTarget.target_month == target_month)
        .with_for_update()
        .first()
    )
    if target is None:
        target = MonthlyTarget(business_unit_id=bu_id, target_month=target_month)
        db.add(target)

    target.monthly_revenue_target = amounts["monthly_revenue_target"]
    target.monthly_cash_flow_target = amounts["monthly_cash_flow_target"]
    target.monthly_max_receivable = amounts["monthly_max_receivable"]
    target.monthly_max_payable = amounts["monthly_max_payable"]
    target.set_by = user.id

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This month's target was just updated elsewhere — please try again.")

    log_audit(db, user, "MONTHLY_TARGET_OVERRIDE", f"bu_id={bu_id} month={target_month.isoformat()}", request)

    return {"ok": True, "target_month": target_month.isoformat()}


@router.get("/report-logs")
def report_logs(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    # No cap here — every report-submission audit entry ever recorded is
    # returned, not just the most recent slice, since this page is the full
    # historical record of report submissions (with client-side date/BU
    # filters on top), not a rolling activity feed. Includes HO Staff's own
    # daily report (HO_DAILY_REPORT_SUBMITTED) alongside the 23 BUs'
    # (DAILY_REPORT_SUBMITTED) — one unified log rather than a separate page,
    # since it's the same kind of event either way. HO's submitter has no
    # business_unit_id (the outerjoin below leaves bu=None for it), so it's
    # given a synthetic "HO — Head Office" row instead of the "-" a genuinely
    # unlinked row would show — that also makes it filterable via the
    # existing Business Unit dropdown for free, no frontend change needed.
    # Outer join on users: an erased user's audit rows keep existing with
    # user_id = NULL (FK is ON DELETE SET NULL), and an inner join would make
    # those rows - the very history of what was submitted - vanish.
    entries = (
        db.query(AuditLog, User, BusinessUnit)
        .outerjoin(User, AuditLog.user_id == User.id)
        .outerjoin(BusinessUnit, User.business_unit_id == BusinessUnit.id)
        .filter(AuditLog.action.in_(["DAILY_REPORT_SUBMITTED", "HO_DAILY_REPORT_SUBMITTED"]))
        .order_by(AuditLog.timestamp.desc())
        .all()
    )

    logs = []
    for audit, submitter, bu in entries:
        details = audit.details or ""
        report_date_str = None
        if "date=" in details:
            report_date_str = details.split("date=", 1)[1].split(" ", 1)[0]
        is_ho = audit.action == "HO_DAILY_REPORT_SUBMITTED"
        logs.append(
            {
                "timestamp_display": format_local_datetime(audit.timestamp),
                "report_date": report_date_str,
                # AuditLog.username is the name captured at write time - the
                # fallback once the user row itself is gone.
                "username": submitter.username if submitter else (audit.username or "(deleted user)"),
                "bu_code": "HO" if is_ho else (bu.code if bu else "-"),
                "bu_name": "Head Office" if is_ho else (bu.name if bu else "-"),
                # Absent entirely on a day's first submission (nothing to
                # diff against yet, see submit_report in ceo.py/ho.py) —
                # distinct from a same-day resubmission that changed
                # nothing (changes=[] but is_initial=False), so the
                # frontend can say "Initial submission" vs "No changes".
                "is_initial": "resubmit_changes_json=" not in details and "resubmit_changes=" not in details,
                "changes": parse_resubmit_changes(details),
            }
        )

    return {"logs": logs, "total_logs": len(logs)}


# --- Security Log: sign-in/out, password, and account-management events.
# A separate slice of AuditLog from report_logs above (also sourced from the
# same table) since these are access/identity events rather than submission
# history — kept Chairman-only, unlike Report Logs which HO Staff can also
# see, since it includes who reset/activated/deactivated which accounts.
SECURITY_LOG_ACTIONS = [
    "LOGIN_SUCCESS",
    "LOGIN_FAILED",
    "LOGOUT",
    "PASSWORD_CHANGED",
    "PASSWORD_CHANGE_FAILED",
    "PASSWORD_RESET_AUTO_APPLIED",  # historical rows only - kept so they still show
    "PASSWORD_RESET_REQUESTED",
    "PASSWORD_RESET_APPROVED",
    "PASSWORD_RESET_REJECTED",
    "CHAIRMAN_RECOVERY_SCRIPT",
    "USER_CREATED",
    "USER_PASSWORD_RESET_BY_ADMIN",
    "USER_ACTIVATED",
    "USER_DEACTIVATED",
]
SECURITY_LOG_LABELS = {
    "LOGIN_SUCCESS": "Signed In",
    "LOGIN_FAILED": "Failed Sign-In",
    "LOGOUT": "Signed Out",
    "PASSWORD_CHANGED": "Password Changed",
    "PASSWORD_CHANGE_FAILED": "Password Change Failed",
    "PASSWORD_RESET_AUTO_APPLIED": "Self-Service Password Reset",
    "PASSWORD_RESET_REQUESTED": "Password Reset Requested",
    "PASSWORD_RESET_APPROVED": "Password Reset Approved",
    "PASSWORD_RESET_REJECTED": "Password Reset Rejected",
    "CHAIRMAN_RECOVERY_SCRIPT": "Chairman Recovery Script",
    "USER_CREATED": "Account Created",
    "USER_PASSWORD_RESET_BY_ADMIN": "Password Reset by Admin",
    "USER_ACTIVATED": "Account Reactivated",
    "USER_DEACTIVATED": "Account Deactivated",
}


def _parse_kv_details(details: str) -> dict:
    """This app's audit "details" convention is space-separated key=value
    tokens (see the log_audit call sites). Safe for every action here EXCEPT
    LOGIN_FAILED, which must not go through this parser — LoginRequest.username
    (schemas.py) has no format validator, unlike CreateUserRequest's, so an
    attempted username containing a space would split across tokens and get
    silently truncated/mangled here. _describe_security_event handles
    LOGIN_FAILED separately for that reason."""
    pairs = {}
    for token in (details or "").split():
        if "=" in token:
            key, _, value = token.partition("=")
            pairs[key] = value
    return pairs


def _describe_security_event(action: str, details: str, username: Optional[str] = None) -> str:
    """`username` is the audit row's own username column - for
    PASSWORD_RESET_REQUESTED that column holds the account the (possibly
    unauthenticated) request was made FOR, not an actor."""
    if action == "LOGIN_FAILED":
        # Recorded even when the username doesn't match any account (see
        # auth.py's login()), so this can't always be read off the actor
        # link below — the attempted username only ever lives in details, as
        # its single "username=..." field (never any other field alongside
        # it — see the log_audit call site), so a straight prefix-strip
        # keeps it intact even if it contains spaces, unlike _parse_kv_details.
        attempted = details[len("username="):] if details and details.startswith("username=") else "—"
        return f"Attempted username: {attempted}"

    kv = _parse_kv_details(details)
    if action == "PASSWORD_CHANGE_FAILED":
        return "Current password did not match"
    if action == "USER_CREATED":
        return f"Created {kv.get('username', '—')} ({kv.get('role', '—')})"
    if action in (
        "USER_PASSWORD_RESET_BY_ADMIN",
        "USER_ACTIVATED",
        "USER_DEACTIVATED",
        "PASSWORD_RESET_APPROVED",
        "PASSWORD_RESET_REJECTED",
    ):
        return f"Target account: {kv.get('target_username', '—')}"
    if action == "PASSWORD_RESET_REQUESTED":
        return f"Requested for: {username or '—'}"
    return "—"


@router.get("/security-log")
def security_log(user: User = Depends(require_chairman), db: Session = Depends(get_db)):
    """Chairman-only view over the identity/access slice of AuditLog —
    every action below was already being recorded, just never surfaced
    anywhere in the UI. Capped to the most recent 500 (unlike report_logs,
    which is naturally bounded to ~1 submission/BU/day, sign-in/out volume
    has no such ceiling)."""
    entries = (
        db.query(AuditLog)
        .filter(AuditLog.action.in_(SECURITY_LOG_ACTIONS))
        .order_by(AuditLog.timestamp.desc())
        .limit(500)
        .all()
    )
    logs = [
        {
            "timestamp_display": format_local_datetime(e.timestamp),
            "username": e.username or "—",
            "action": e.action,
            "action_label": SECURITY_LOG_LABELS.get(e.action, e.action),
            "detail": _describe_security_event(e.action, e.details, e.username),
            "ip_address": e.ip_address or "—",
        }
        for e in entries
    ]
    return {"logs": logs}


@router.get("/ceo-profiles")
def ceo_profiles(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """All CEOs' mandatory profile info, for Head Office/Chairman to review.

    Note: the original spec for this asked for GET /api/v1/admin/ceo-profile
    (singular, versioned). This app has no /v1 namespace anywhere else and
    every other admin route lives under /api/admin, so this was placed at
    /api/admin/ceo-profiles (plural) instead — matching the existing
    convention and returning every CEO in one call rather than requiring the
    Admin Dashboard to look one up at a time."""
    ceos = (
        db.query(User)
        .filter(User.role == "CEO", User.is_active == True)  # noqa: E712
        .order_by(User.business_unit_id, User.id)
        .all()
    )
    profiles_by_user = {p.user_id: p for p in db.query(CeoProfile).all()}
    business_units = {bu.id: bu for bu in db.query(BusinessUnit).all()}
    today = today_local()

    entries = []
    for ceo in ceos:
        profile = profiles_by_user.get(ceo.id)
        bu = business_units.get(ceo.business_unit_id)
        entries.append(
            {
                "user_id": ceo.id,
                "username": ceo.username,
                "managed_business_ids": [bu.code] if bu else [],
                "managed_business_names": [bu.name] if bu else [],
                **serialize_ceo_profile(ceo, profile, today),
            }
        )

    return {"ceo_profiles": entries, "total": len(entries)}


@router.get("/ceo-activity-calendar")
def ceo_activity_calendar(
    business_unit_id: int,
    month: str = "",
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One business unit's CEO activity month for the Chairman/HO calendar
    page — the same Monday-first month grid the CEO sees on their own
    dashboard, built by the shared helper with no submitter scoping
    (user_id=None) so executives see the unit's days exactly as recorded,
    whoever submitted them. Month parsing mirrors the CEO dashboard:
    malformed values fall back to the current month."""
    business_unit = db.query(BusinessUnit).filter(BusinessUnit.id == business_unit_id).first()
    if business_unit is None:
        raise HTTPException(status_code=404, detail="Business unit not found")

    today = today_local()
    try:
        calendar_year, calendar_month = (int(part) for part in month.split("-", 1))
        date(calendar_year, calendar_month, 1)  # validates the month is real
        if not (2000 <= calendar_year <= 2100):
            raise ValueError("month out of supported range")
    except (ValueError, TypeError):
        calendar_year, calendar_month = today.year, today.month

    calendar_weeks = build_ceo_activity_calendar(db, business_unit.id, calendar_year, calendar_month, today, None)
    prev_year, prev_month = shift_month(calendar_year, calendar_month, -1)
    next_year, next_month = shift_month(calendar_year, calendar_month, 1)

    ceo_user = (
        db.query(User)
        .filter(User.business_unit_id == business_unit.id, User.role == "CEO", User.is_active == True)  # noqa: E712
        .first()
    )
    return {
        "business_unit": {"id": business_unit.id, "code": business_unit.code, "name": business_unit.name},
        "ceo": (
            {"id": ceo_user.id, "username": ceo_user.username, "full_name": ceo_user.full_name}
            if ceo_user is not None
            else None
        ),
        "calendar_weeks": calendar_weeks,
        "calendar_month_label": date(calendar_year, calendar_month, 1).strftime("%B %Y"),
        "prev_month": f"{prev_year:04d}-{prev_month:02d}",
        "next_month": f"{next_year:04d}-{next_month:02d}",
        "current_month": f"{today.year:04d}-{today.month:02d}",
    }


def _needs_help(log: Optional[ActivityLog]) -> bool:
    """An OPEN help escalation: the CEO wrote a help request and no
    Chairman/HO response has been recorded yet. A resolved request stops
    flagging everywhere (Needs Attention, badges, header counts) even
    though the help text itself stays on the report."""
    return bool(
        log is not None
        and log.help_needed
        and log.help_needed.strip()
        and log.help_resolved_at is None
    )


def _help_fields(log: ActivityLog, resolver_username: Optional[str] = None) -> dict:
    """The help-request fields shared by every admin activity payload —
    the ask, its open/resolved state, and (once answered) the response."""
    return {
        "problems_today": log.problems_today or "",
        "help_needed": log.help_needed or "",
        "needs_help": _needs_help(log),
        "help_resolved_at": format_local_datetime(log.help_resolved_at),
        "help_resolved_by_username": resolver_username,
        "help_response": log.help_response or "",
    }


def _effective_percent(health_row, activity_row, field: str):
    """Exercise % display value: the health row's post-move value when
    present, else the activity row's pre-move legacy value."""
    value = getattr(health_row, field, None) if health_row is not None else None
    if value is not None:
        return value
    return getattr(activity_row, field, None) if activity_row is not None else None


def _activity_entry(log: ActivityLog, bu: Optional[BusinessUnit], ceo_username: str,
                    resolver_username: Optional[str] = None) -> dict:
    return {
        "id": log.id,
        "bu_code": bu.code if bu else "-",
        "bu_name": bu.name if bu else "-",
        "ceo_username": ceo_username,
        "report_date": log.report_date.strftime("%d %b %Y"),
        "submitted_at": format_local_datetime(log.updated_at),
        "meetings_attended": log.meetings_attended or "",
        "meetings_count": log.meetings_count,
        "site_visits": log.site_visits or "",
        "key_decisions": log.key_decisions or "",
        "key_achievement": log.key_achievement or "",
        "next_day_schedule": log.next_day_schedule or "",
        **_help_fields(log, resolver_username),
        "document_url": log.document_url,
        "gym_percent": log.gym_percent,
        "swimming_percent": log.swimming_percent,
        "cycle_percent": log.cycle_percent,
    }


@router.get("/activity-dashboard")
def activity_dashboard(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    today = today_local()
    business_units = db.query(BusinessUnit).order_by(BusinessUnit.id).all()
    ceo_by_bu = {
        u.business_unit_id: u.username
        for u in db.query(User).filter(User.role == "CEO", User.is_active == True).order_by(User.id).all()  # noqa: E712
    }
    bu_by_id = {bu.id: bu for bu in business_units}

    today_rows = (
        db.query(ActivityLog).filter(ActivityLog.report_date == today)
        .order_by(ActivityLog.updated_at.desc()).all()
    )
    history_rows = (
        db.query(ActivityLog).order_by(ActivityLog.report_date.desc(), ActivityLog.updated_at.desc()).limit(300).all()
    )
    latest_by_bu = {}
    latest_health_by_bu = {}
    for bu in business_units:
        latest_by_bu[bu.id] = (
            db.query(ActivityLog).filter(ActivityLog.business_unit_id == bu.id)
            .order_by(ActivityLog.report_date.desc()).first()
        )
        latest_health_by_bu[bu.id] = (
            db.query(HealthStatus).filter(HealthStatus.business_unit_id == bu.id)
            .order_by(HealthStatus.report_date.desc()).first()
        )
    today_health_by_bu = {
        h.business_unit_id: h
        for h in db.query(HealthStatus).filter(HealthStatus.report_date == today).all()
    }

    # Resolver usernames for every log this payload serializes, in one
    # query — the help fields name who answered each request.
    resolver_ids = {
        log.help_resolved_by
        for log in today_rows + history_rows + [l for l in latest_by_bu.values() if l is not None]
        if log.help_resolved_by is not None
    }
    resolver_usernames = {}
    if resolver_ids:
        resolver_usernames = {
            u.id: u.username for u in db.query(User).filter(User.id.in_(resolver_ids)).all()
        }

    def _resolver_name(log):
        return resolver_usernames.get(log.help_resolved_by) if log.help_resolved_by else None

    today_briefing = []
    for log in today_rows:
        entry = _activity_entry(log, bu_by_id.get(log.business_unit_id),
                                ceo_by_bu.get(log.business_unit_id, "-"), _resolver_name(log))
        # Briefing cards read exercise % from the health row first (the
        # form collects it there now), falling back to the activity row.
        health_today = today_health_by_bu.get(log.business_unit_id)
        for field in ("gym_percent", "swimming_percent", "cycle_percent"):
            entry[field] = _effective_percent(health_today, log, field)
        today_briefing.append(entry)

    directory = []
    for bu in business_units:
        latest = latest_by_bu[bu.id]
        latest_health = latest_health_by_bu[bu.id]
        directory.append(
            {
                "bu_id": bu.id,
                "bu_code": bu.code,
                "bu_name": bu.name,
                "ceo_username": ceo_by_bu.get(bu.id, "-"),
                "help_log_id": latest.id if latest else None,
                "meetings_attended": latest.meetings_attended if latest else None,
                "meetings_count": latest.meetings_count if latest else None,
                "site_visits": latest.site_visits if latest else None,
                "key_decisions": latest.key_decisions if latest else None,
                "key_achievement": latest.key_achievement if latest else None,
                "next_day_schedule": latest.next_day_schedule if latest else None,
                "problems_today": latest.problems_today if latest else None,
                "help_needed": latest.help_needed if latest else None,
                "needs_help": _needs_help(latest),
                "help_resolved_at": format_local_datetime(latest.help_resolved_at) if latest else None,
                "help_resolved_by_username": _resolver_name(latest) if latest else None,
                "help_response": latest.help_response if latest else None,
                "gym_percent": _effective_percent(latest_health, latest, "gym_percent"),
                "swimming_percent": _effective_percent(latest_health, latest, "swimming_percent"),
                "cycle_percent": _effective_percent(latest_health, latest, "cycle_percent"),
                "document_url": latest.document_url if latest else None,
                "status": activity_quadrant_status(latest),
                "reported_display": format_local_datetime(latest.updated_at) if latest else None,
            }
        )

    counts = {
        "active": sum(1 for d in directory if d["status"] == "green"),
        "moderate": sum(1 for d in directory if d["status"] == "yellow"),
        "low": sum(1 for d in directory if d["status"] == "red"),
        "no_data": sum(1 for d in directory if d["status"] == "grey"),
    }

    activity_history = [
        _activity_entry(log, bu_by_id.get(log.business_unit_id),
                        ceo_by_bu.get(log.business_unit_id, "-"), _resolver_name(log))
        for log in history_rows
    ]

    return {
        "today_display": today.strftime("%A, %d %B %Y"),
        "today_briefing": today_briefing,
        "directory": directory,
        "counts": counts,
        "activity_history": activity_history,
        "submitted_count": len(today_briefing),
        "total_business_units": len(business_units),
    }


@router.post("/activity-logs/{log_id}/resolve-help")
def resolve_help_request(
    log_id: int,
    payload: ResolveHelpRequest,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Answers one CEO help_needed escalation: records the Chairman/HO
    response on the activity log (which clears the Needs Attention flag
    everywhere, since needs_help requires an unresolved request) and
    notifies the CEO who asked. The response is deliberately NOT copied
    into the audit details — like other free text, the audit trail only
    records that the resolution happened, and for which report."""
    response_text = payload.response.strip()
    if not response_text:
        raise HTTPException(status_code=422, detail="Response must not be empty")

    log = db.query(ActivityLog).filter(ActivityLog.id == log_id).first()
    if log is None:
        raise HTTPException(status_code=404, detail="Activity log not found")
    if not (log.help_needed and log.help_needed.strip()):
        raise HTTPException(status_code=400, detail="This report has no help request to resolve")
    if log.help_resolved_at is not None:
        raise HTTPException(status_code=400, detail="This help request has already been resolved")

    business_unit = db.query(BusinessUnit).filter(BusinessUnit.id == log.business_unit_id).first()
    log.help_resolved_at = datetime.utcnow()
    log.help_resolved_by = user.id
    log.help_response = response_text

    if log.submitted_by:
        title = (
            f"Help request answered — {business_unit.name if business_unit else 'your unit'} "
            f"({log.report_date.strftime('%d %b %Y')})"
        )[:200]
        db.add(Notification(
            user_id=log.submitted_by,
            type="help_resolved",
            title=title,
            message=response_text,
            link=f"/ceo?date={log.report_date.isoformat()}",
        ))
    db.commit()
    log_audit(
        db, user, "HELP_REQUEST_RESOLVED",
        f"log_id={log.id} bu_id={log.business_unit_id} date={log.report_date.isoformat()}",
        request,
    )
    return {
        "ok": True,
        "help_resolved_at": format_local_datetime(log.help_resolved_at),
        "help_resolved_by_username": user.username,
        "help_response": log.help_response,
    }


@router.get("/ceo-monthly-summary")
def ceo_monthly_summary(
    business_unit_id: int,
    month: str = "",
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One business unit's CEO month as a printable PDF: reporting stats,
    the month's achievements, problems and help requests (with their
    resolution state), and a health snapshot. `month` is YYYY-MM;
    malformed values fall back to the current month, mirroring the CEO
    activity calendar endpoint."""
    import calendar as _cal
    from io import BytesIO

    from fpdf import FPDF

    business_unit = db.query(BusinessUnit).filter(BusinessUnit.id == business_unit_id).first()
    if business_unit is None:
        raise HTTPException(status_code=404, detail="Business unit not found")

    today = today_local()
    try:
        year, month_num = (int(part) for part in month.split("-", 1))
        date(year, month_num, 1)  # validates the month is real
        if not (2000 <= year <= 2100):
            raise ValueError("month out of supported range")
    except (ValueError, TypeError):
        year, month_num = today.year, today.month
    month_start = date(year, month_num, 1)
    days_in_month = _cal.monthrange(year, month_num)[1]
    month_end = date(year, month_num, days_in_month)
    month_label = month_start.strftime("%B %Y")

    ceo_user = (
        db.query(User)
        .filter(User.business_unit_id == business_unit.id, User.role == "CEO", User.is_active == True)  # noqa: E712
        .first()
    )

    logs = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.business_unit_id == business_unit.id,
            ActivityLog.report_date >= month_start,
            ActivityLog.report_date <= month_end,
        )
        .order_by(ActivityLog.report_date)
        .all()
    )
    health_rows = (
        db.query(HealthStatus)
        .filter(
            HealthStatus.business_unit_id == business_unit.id,
            HealthStatus.report_date >= month_start,
            HealthStatus.report_date <= month_end,
        )
        .order_by(HealthStatus.report_date)
        .all()
    )
    fin_rows = (
        db.query(DailyFinancial)
        .filter(
            DailyFinancial.business_unit_id == business_unit.id,
            DailyFinancial.report_date >= month_start,
            DailyFinancial.report_date <= month_end,
        )
        .all()
    )

    days_reported = len({log.report_date for log in logs})
    total_meetings = sum(log.meetings_count or 0 for log in logs)
    month_revenue = sum(float(f.revenue or 0) for f in fin_rows)

    achievements = [
        (log.report_date, log.key_achievement) for log in logs
        if log.key_achievement and log.key_achievement.strip()
    ]
    problems = [
        (log.report_date, log.problems_today) for log in logs
        if log.problems_today and log.problems_today.strip()
    ]
    help_requests = [
        log for log in logs if log.help_needed and log.help_needed.strip()
    ]
    resolver_usernames = {}
    resolver_ids = {log.help_resolved_by for log in help_requests if log.help_resolved_by is not None}
    if resolver_ids:
        resolver_usernames = {
            u.id: u.username for u in db.query(User).filter(User.id.in_(resolver_ids)).all()
        }

    # Health snapshot — averages over the days that recorded each metric.
    steps = [h.daily_steps for h in health_rows if h.daily_steps is not None]
    sleeps = [float(h.sleep_hours) for h in health_rows if h.sleep_hours is not None]
    weighted = [h for h in health_rows if h.weight_kg is not None]
    latest_weight = float(weighted[-1].weight_kg) if weighted else None
    health_by_date = {h.report_date: h for h in health_rows}
    logs_by_date = {log.report_date: log for log in logs}

    def _exercise_average(field: str):
        values = []
        for day in sorted(set(health_by_date) | set(logs_by_date)):
            value = _effective_percent(health_by_date.get(day), logs_by_date.get(day), field)
            if value is not None:
                values.append(value)
        return (sum(values) / len(values)) if values else None

    exercise_averages = {
        "Gym": _exercise_average("gym_percent"),
        "Swimming": _exercise_average("swimming_percent"),
        "Cycle": _exercise_average("cycle_percent"),
    }

    def t(text):
        """Strip characters the core latin-1 PDF font can't render."""
        return (text or "").encode("latin-1", errors="replace").decode("latin-1")

    def money(v):
        return f"Ks {v:,.0f}"

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    W = pdf.w - 20

    pdf.set_font("helvetica", "B", 18)
    pdf.cell(W, 10, "CEO Monthly Summary", ln=1)
    pdf.set_font("helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    ceo_label = (
        f"{ceo_user.full_name or ceo_user.username} ({ceo_user.username})" if ceo_user else "No active CEO assigned"
    )
    pdf.cell(W, 6, t(f"{business_unit.name} ({business_unit.code})  |  {month_label}  |  Generated {today.isoformat()}"), ln=1)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Overview", ln=1)
    pdf.set_font("helvetica", "", 11)
    for key, value in [
        ("CEO", ceo_label),
        ("Days reported", f"{days_reported} of {days_in_month}"),
        ("Total meetings", str(total_meetings)),
        ("Month revenue", money(month_revenue)),
    ]:
        pdf.cell(45, 6.5, key)
        pdf.cell(0, 6.5, t(value), ln=1)
    pdf.ln(3)

    def _section(title: str):
        pdf.set_font("helvetica", "B", 13)
        pdf.cell(W, 8, title, ln=1)
        pdf.set_font("helvetica", "", 11)

    def _dated_entries(entries, empty_text: str):
        if not entries:
            pdf.set_font("helvetica", "I", 11)
            pdf.cell(W, 6.5, empty_text, ln=1)
            pdf.set_font("helvetica", "", 11)
            return
        for entry_date, text in entries:
            pdf.set_font("helvetica", "B", 10.5)
            pdf.cell(W, 5.5, entry_date.strftime("%d %b %Y"), ln=1)
            pdf.set_font("helvetica", "", 11)
            pdf.multi_cell(W, 6, t(text.strip()))
            pdf.ln(1.5)

    _section("Key Achievements")
    _dated_entries(achievements, "No achievements recorded this month.")
    pdf.ln(2)

    _section("Problems Reported")
    _dated_entries(problems, "No problems recorded this month.")
    pdf.ln(2)

    _section("Help Requests")
    if not help_requests:
        pdf.set_font("helvetica", "I", 11)
        pdf.cell(W, 6.5, "No help requests this month.", ln=1)
        pdf.set_font("helvetica", "", 11)
    for log in help_requests:
        pdf.set_font("helvetica", "B", 10.5)
        pdf.cell(W, 5.5, log.report_date.strftime("%d %b %Y"), ln=1)
        pdf.set_font("helvetica", "", 11)
        pdf.multi_cell(W, 6, t(log.help_needed.strip()))
        if log.help_resolved_at is not None:
            resolver = resolver_usernames.get(log.help_resolved_by, "HO / Chairman")
            pdf.set_font("helvetica", "", 10.5)
            pdf.set_text_color(21, 128, 61)
            # multi_cell leaves the cursor at the right margin — reset x,
            # otherwise the status line renders off the page edge.
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(
                W, 5.5,
                t(f"Status: Resolved - {log.help_response or ''} "
                  f"(by {resolver}, {format_local_datetime(log.help_resolved_at)})"),
            )
            pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_font("helvetica", "", 10.5)
            pdf.set_text_color(185, 28, 28)
            pdf.set_x(pdf.l_margin)
            pdf.cell(W, 5.5, t("Status: Open - awaiting a response"), ln=1)
            pdf.set_text_color(0, 0, 0)
        pdf.set_font("helvetica", "", 11)
        pdf.ln(1.5)
    pdf.ln(2)

    _section("Health Snapshot")
    health_lines = []
    if steps:
        health_lines.append(("Average daily steps", f"{sum(steps) / len(steps):,.0f}"))
    if sleeps:
        health_lines.append(("Average sleep", f"{sum(sleeps) / len(sleeps):.1f} hrs"))
    if latest_weight is not None:
        health_lines.append(("Latest weight", f"{latest_weight:.1f} kg"))
    for label, average in exercise_averages.items():
        if average is not None:
            health_lines.append((f"Average {label.lower()} completion", f"{average:.0f}%"))
    if not health_lines:
        pdf.set_font("helvetica", "I", 11)
        pdf.cell(W, 6.5, "No health data recorded this month.", ln=1)
        pdf.set_font("helvetica", "", 11)
    for key, value in health_lines:
        pdf.cell(55, 6.5, key)
        pdf.cell(0, 6.5, t(value), ln=1)

    buf = BytesIO(pdf.output())
    filename = f"ceo_monthly_summary_{business_unit.code}_{month_start.strftime('%Y-%m')}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _health_entry(h: HealthStatus, bu: Optional[BusinessUnit], ceo_username: str) -> dict:
    return {
        "bu_id": bu.id if bu else None,
        "bu_code": bu.code if bu else "-",
        "bu_name": bu.name if bu else "-",
        "ceo_username": ceo_username,
        "report_date": h.report_date.strftime("%d %b %Y"),
        "submitted_at": format_local_datetime(h.updated_at),
        "physical_health": h.physical_health,
        "stress_level": h.stress_level,
        "energy_mood": h.energy_mood,
        "sleep_quality": h.sleep_quality,
        "daily_steps": h.daily_steps,
        "step_status": step_status(h.daily_steps),
        "heart_rate": h.heart_rate,
        "sleep_hours": float(h.sleep_hours) if h.sleep_hours is not None else None,
        "bp_systolic": h.bp_systolic,
        "bp_diastolic": h.bp_diastolic,
        "weight_kg": float(h.weight_kg) if h.weight_kg is not None else None,
        "is_sick": h.is_sick,
        "gym_percent": h.gym_percent,
        "swimming_percent": h.swimming_percent,
        "cycle_percent": h.cycle_percent,
        "risk": compute_health_risk(h),
    }


@router.get("/health-dashboard")
def health_dashboard(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    today = today_local()
    business_units = db.query(BusinessUnit).order_by(BusinessUnit.id).all()
    bu_by_id = {bu.id: bu for bu in business_units}
    ceo_by_bu = {
        u.business_unit_id: u.username
        for u in db.query(User).filter(User.role == "CEO", User.is_active == True).order_by(User.id).all()  # noqa: E712
    }

    today_rows = (
        db.query(HealthStatus).filter(HealthStatus.report_date == today)
        .order_by(HealthStatus.updated_at.desc()).all()
    )
    today_briefing = [
        _health_entry(h, bu_by_id.get(h.business_unit_id), ceo_by_bu.get(h.business_unit_id, "-"))
        for h in today_rows
    ]

    directory = []
    for bu in business_units:
        latest = (
            db.query(HealthStatus).filter(HealthStatus.business_unit_id == bu.id)
            .order_by(HealthStatus.report_date.desc()).first()
        )
        directory.append(
            {
                "bu_id": bu.id,
                "bu_code": bu.code,
                "bu_name": bu.name,
                "ceo_username": ceo_by_bu.get(bu.id, "-"),
                "physical_health": latest.physical_health if latest else None,
                "stress_level": latest.stress_level if latest else None,
                "energy_mood": latest.energy_mood if latest else None,
                "sleep_quality": latest.sleep_quality if latest else None,
                "daily_steps": latest.daily_steps if latest else None,
                "step_status": step_status(latest.daily_steps) if latest else None,
                "heart_rate": latest.heart_rate if latest else None,
                "sleep_hours": float(latest.sleep_hours) if latest and latest.sleep_hours is not None else None,
                "bp_systolic": latest.bp_systolic if latest else None,
                "bp_diastolic": latest.bp_diastolic if latest else None,
                "weight_kg": float(latest.weight_kg) if latest and latest.weight_kg is not None else None,
                "is_sick": latest.is_sick if latest else None,
                "gym_percent": latest.gym_percent if latest else None,
                "swimming_percent": latest.swimming_percent if latest else None,
                "cycle_percent": latest.cycle_percent if latest else None,
                "risk": compute_health_risk(latest),
                "reported_display": format_local_datetime(latest.updated_at) if latest else None,
            }
        )

    counts = {
        "healthy": sum(1 for d in directory if d["risk"] == "green"),
        "warning": sum(1 for d in directory if d["risk"] == "yellow"),
        "critical": sum(1 for d in directory if d["risk"] == "red"),
        "unknown": sum(1 for d in directory if d["risk"] == "unknown"),
    }

    history_rows = (
        db.query(HealthStatus).order_by(HealthStatus.report_date.desc(), HealthStatus.updated_at.desc()).limit(300).all()
    )
    health_history = [
        _health_entry(h, bu_by_id.get(h.business_unit_id), ceo_by_bu.get(h.business_unit_id, "-"))
        for h in history_rows
    ]

    return {
        "today_display": today.strftime("%A, %d %B %Y"),
        "today_briefing": today_briefing,
        "directory": directory,
        "counts": counts,
        "health_history": health_history,
        "submitted_count": len(today_briefing),
        "total_business_units": len(business_units),
    }


@router.get("/export-excel")
def export_excel(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    def _parse_date(raw: Optional[str]) -> Optional[date]:
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    range_start = _parse_date(start_date)
    range_end = _parse_date(end_date)
    if range_start and range_end and range_start > range_end:
        range_start, range_end = range_end, range_start

    business_units = {bu.id: bu for bu in db.query(BusinessUnit).all()}

    financial_query = db.query(DailyFinancial)
    activity_query = db.query(ActivityLog)
    health_query = db.query(HealthStatus)
    social_query = db.query(SocialLog)
    if range_start:
        financial_query = financial_query.filter(DailyFinancial.report_date >= range_start)
        activity_query = activity_query.filter(ActivityLog.report_date >= range_start)
        health_query = health_query.filter(HealthStatus.report_date >= range_start)
        social_query = social_query.filter(SocialLog.report_date >= range_start)
    if range_end:
        financial_query = financial_query.filter(DailyFinancial.report_date <= range_end)
        activity_query = activity_query.filter(ActivityLog.report_date <= range_end)
        health_query = health_query.filter(HealthStatus.report_date <= range_end)
        social_query = social_query.filter(SocialLog.report_date <= range_end)

    financials = financial_query.all()
    activities = activity_query.all()
    healths = health_query.all()
    socials = social_query.all()

    merged = defaultdict(dict)
    for f in financials:
        key = (f.business_unit_id, f.report_date)
        merged[key].update(
            {"revenue": f.revenue, "cash_flow": f.cash_flow, "receivable": f.receivable, "payable": f.payable}
        )
    for a in activities:
        key = (a.business_unit_id, a.report_date)
        merged[key].update(
            {
                "meetings_attended": a.meetings_attended,
                "site_visits": a.site_visits,
                "key_decisions": a.key_decisions,
                "next_day_schedule": a.next_day_schedule,
                "gym_percent": a.gym_percent,
                "swimming_percent": a.swimming_percent,
                "cycle_percent": a.cycle_percent,
            }
        )
    for h in healths:
        key = (h.business_unit_id, h.report_date)
        merged[key].update(
            {
                "daily_steps": h.daily_steps,
                "heart_rate": h.heart_rate,
                "sleep_hours": h.sleep_hours,
                "bp_systolic": h.bp_systolic,
                "bp_diastolic": h.bp_diastolic,
            }
        )
    for s in socials:
        key = (s.business_unit_id, s.report_date)
        merged[key].update(
            {"event_flag": s.event_flag, "meeting_flag": s.meeting_flag, "golf_flag": s.golf_flag}
        )

    workbook = Workbook()
    sheet = workbook.active
    if range_start and range_end and range_start == range_end:
        sheet.title = f"Daily {range_start.isoformat()}"
    elif range_start or range_end:
        sheet.title = "Filtered Export"
    else:
        sheet.title = "Master Dataset"

    headers = [
        "BU Code", "BU Name", "Report Date", "Revenue", "Cash Flow", "Receivable", "Payable",
        "Meetings Attended", "Site Visits / Travel", "Key Decisions Made", "Next Day Schedule",
        "Gym %", "Swimming %", "Cycle %", "Daily Steps", "Heart Rate", "Sleep Hours", "Blood Pressure",
        "Event", "Meeting", "Golf",
    ]
    sheet.append(headers)

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for col_idx in range(1, len(headers) + 1):
        cell = sheet.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    sorted_keys = sorted(merged.keys(), key=lambda k: (k[1], business_units[k[0]].code if k[0] in business_units else ""), reverse=True)

    for bu_id, report_date in sorted_keys:
        bu = business_units.get(bu_id)
        if bu is None:
            continue
        record = merged[(bu_id, report_date)]
        bp_systolic = record.get("bp_systolic")
        bp_diastolic = record.get("bp_diastolic")
        blood_pressure = f"{bp_systolic}/{bp_diastolic}" if bp_systolic is not None and bp_diastolic is not None else None
        sheet.append(
            [
                bu.code, bu.name, report_date.isoformat(),
                float(record.get("revenue")) if record.get("revenue") is not None else None,
                float(record.get("cash_flow")) if record.get("cash_flow") is not None else None,
                float(record.get("receivable")) if record.get("receivable") is not None else None,
                float(record.get("payable")) if record.get("payable") is not None else None,
                _sanitize_excel_cell(record.get("meetings_attended")),
                _sanitize_excel_cell(record.get("site_visits")),
                _sanitize_excel_cell(record.get("key_decisions")),
                _sanitize_excel_cell(record.get("next_day_schedule")),
                record.get("gym_percent"),
                record.get("swimming_percent"),
                record.get("cycle_percent"),
                record.get("daily_steps"),
                record.get("heart_rate"),
                float(record.get("sleep_hours")) if record.get("sleep_hours") is not None else None,
                blood_pressure,
                "Yes" if record.get("event_flag") else ("No" if "event_flag" in record else None),
                "Yes" if record.get("meeting_flag") else ("No" if "meeting_flag" in record else None),
                "Yes" if record.get("golf_flag") else ("No" if "golf_flag" in record else None),
            ]
        )

    for col_idx, header in enumerate(headers, start=1):
        width = max(16, min(40, len(header) + 4))
        sheet.column_dimensions[get_column_letter(col_idx)].width = width

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    if range_start and range_end and range_start == range_end:
        filename = f"business_ops_daily_{range_start.isoformat()}.xlsx"
        audit_detail = f"range={range_start.isoformat()}"
    elif range_start or range_end:
        label_start = range_start.isoformat() if range_start else "earliest"
        label_end = range_end.isoformat() if range_end else today_local().isoformat()
        filename = f"business_ops_export_{label_start}_to_{label_end}.xlsx"
        audit_detail = f"range={label_start}..{label_end}"
    else:
        filename = f"business_ops_export_{today_local().isoformat()}.xlsx"
        audit_detail = "range=all"

    log_audit(db, user, "EXCEL_EXPORT", audit_detail, request)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Personal health / financial data: never cached by the browser
            # or any intermediary.
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------------ business unit management

from pydantic import BaseModel as _BaseModel, Field as _Field  # noqa: E402

from src.accounting_templates import TEMPLATE_LABELS, TEMPLATE_TYPES  # noqa: E402
from src.models import Account  # noqa: E402


def _serialize_bu_admin(db: Session, bu: BusinessUnit) -> dict:
    account_count = db.query(Account).filter(Account.business_unit_id == bu.id).count()
    user_count = db.query(User).filter(User.business_unit_id == bu.id).count()
    ceo = db.query(User).filter(User.business_unit_id == bu.id, User.role == "CEO",
                                User.is_active == True).first()  # noqa: E712
    return {
        "id": bu.id, "code": bu.code, "name": bu.name,
        "nature": getattr(bu, "nature", None),
        "nature_label": TEMPLATE_LABELS.get(getattr(bu, "nature", None) or "", "—"),
        "user_count": user_count,
        "ceo_name": (ceo.full_name or ceo.username) if ceo else None,
        "account_count": account_count,
        "has_books": account_count > 0,
        "created_at": bu.created_at.isoformat() if bu.created_at else None,
    }


def _next_bu_code(db: Session) -> str:
    nums = []
    for (code,) in db.query(BusinessUnit.code).all():
        if code and code.startswith("BU") and code[2:].isdigit():
            nums.append(int(code[2:]))
    return f"BU{(max(nums) + 1) if nums else 1}"


class BUCreateIn(_BaseModel):
    name: str = _Field(min_length=1, max_length=120)
    nature: str = "retail"


class BUUpdateIn(_BaseModel):
    name: Optional[str] = None
    nature: Optional[str] = None


@router.get("/business-units")
def list_business_units(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    bus = db.query(BusinessUnit).order_by(BusinessUnit.id).all()
    return {"items": [_serialize_bu_admin(db, bu) for bu in bus],
            "natures": [{"key": k, "label": TEMPLATE_LABELS.get(k, k)} for k in TEMPLATE_TYPES]}


@router.post("/business-units")
def create_business_unit(payload: BUCreateIn, user: User = Depends(require_admin),
                         db: Session = Depends(get_db)):
    """Create a BU and provision its chart of accounts from the nature template."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if payload.nature not in TEMPLATE_TYPES:
        raise HTTPException(400, f"nature must be one of {TEMPLATE_TYPES}")
    if db.query(BusinessUnit).filter(func.lower(BusinessUnit.name) == name.lower()).first():
        raise HTTPException(400, f"A business unit named '{name}' already exists")
    bu = BusinessUnit(code=_next_bu_code(db), name=name, nature=payload.nature)
    db.add(bu)
    db.commit()
    db.refresh(bu)
    from src.routers.accounting import provision_bu
    provision = provision_bu(bu.id, template=payload.nature, user=user, db=db)
    out = _serialize_bu_admin(db, bu)
    out["provision"] = provision
    return out


@router.patch("/business-units/{bu_id}")
def update_business_unit(bu_id: int, payload: BUUpdateIn, user: User = Depends(require_admin),
                         db: Session = Depends(get_db)):
    """Rename a BU and/or set its nature. Setting a nature on a BU with no
    chart of accounts yet provisions it; a BU that already has books keeps
    its existing chart (only the label changes)."""
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == bu_id).first()
    if not bu:
        raise HTTPException(404, "Business unit not found")
    note = None
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        dup = db.query(BusinessUnit).filter(func.lower(BusinessUnit.name) == name.lower(),
                                            BusinessUnit.id != bu_id).first()
        if dup:
            raise HTTPException(400, f"A business unit named '{name}' already exists")
        bu.name = name
    if payload.nature is not None:
        if payload.nature not in TEMPLATE_TYPES:
            raise HTTPException(400, f"nature must be one of {TEMPLATE_TYPES}")
        bu.nature = payload.nature
        db.commit()
        has_accounts = db.query(Account).filter(Account.business_unit_id == bu.id).count() > 0
        if has_accounts:
            note = "Nature label updated — existing chart of accounts unchanged."
        else:
            from src.routers.accounting import provision_bu
            provision_bu(bu.id, template=payload.nature, user=user, db=db)
            note = f"Chart of accounts created from the {TEMPLATE_LABELS.get(payload.nature, payload.nature)} template."
    db.commit()
    db.refresh(bu)
    out = _serialize_bu_admin(db, bu)
    out["note"] = note
    return out
