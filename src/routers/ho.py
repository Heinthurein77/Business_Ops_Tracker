import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import assert_token_current, require_roles
from src.constants import Role
from src.config import settings
from src.database import get_db
from src.models import (
    HeadOfficeActivityLog,
    HeadOfficeDailyFinancial,
    HeadOfficeHealthStatus,
    HeadOfficeMonthlyTarget,
    HeadOfficeSocialLog,
    User,
)
from src.r2_storage import delete_object, extract_r2_key, upload_attachment_or_503
from src.schemas import MonthlyTargetRequest
from src.upload_validation import content_matches_extension
from src.utils import (
    BP_DIASTOLIC_RANGE,
    BP_SYSTOLIC_RANGE,
    HEART_RATE_RANGE,
    MAX_NARRATIVE_TEXT_LEN,
    SLEEP_HOURS_RANGE,
    STEPS_RANGE,
    build_activity_rows,
    build_financial_rows,
    build_health_rows,
    build_resubmit_audit_detail,
    build_social_rows,
    compute_field_changes,
    compute_ho_quadrant_summary,
    compute_monthly_social_counts,
    evaluate_ho_cumulative_health,
    form_decimal,
    form_int,
    form_money,
    form_percent,
    form_text,
    format_local_datetime,
    log_audit,
    monthly_target_window_error,
    parse_bool,
    parse_month_or_default,
    parse_monthly_target_amounts,
    SubmitRateLimiter,
    today_local,
    todays_submission_events,
    validate_blood_pressure,
)

router = APIRouter(prefix="/api/ho", tags=["ho"])

# Same shape as /api/ceo/submit-report's guard — a real daily report is
# submitted at most a couple of times a day.
REPORT_SUBMIT_RATE_LIMIT = 5
REPORT_SUBMIT_RATE_WINDOW_SECONDS = 60
# M-12: the guard itself lives in src/utils.py (SubmitRateLimiter); this
# router owns one instance so CEO and HO keep separate budgets.
_rate_limiter = SubmitRateLimiter(REPORT_SUBMIT_RATE_LIMIT, REPORT_SUBMIT_RATE_WINDOW_SECONDS)
_is_rate_limited = _rate_limiter.is_limited

MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "docx", "xlsx", "csv", "txt"}

logger = logging.getLogger(__name__)

@router.get("/dashboard")
def ho_dashboard(user: User = Depends(require_roles(Role.HO)), db: Session = Depends(get_db)):
    report_date = today_local()

    financial = db.query(HeadOfficeDailyFinancial).filter(HeadOfficeDailyFinancial.report_date == report_date).first()
    activity = db.query(HeadOfficeActivityLog).filter(HeadOfficeActivityLog.report_date == report_date).first()
    health = db.query(HeadOfficeHealthStatus).filter(HeadOfficeHealthStatus.report_date == report_date).first()
    social = db.query(HeadOfficeSocialLog).filter(HeadOfficeSocialLog.report_date == report_date).first()

    # m-31: HO rows are shared by report_date — any HO user can overwrite
    # another's submission. The models already carry submitted_by (set on
    # every submit), so no migration is needed; the dashboard records and
    # displays the last submitter instead of fragmenting the single HO
    # report per user. The most recently updated of today's rows wins.
    last_submitted_by = None
    today_rows = [r for r in (financial, activity, health, social)
                  if r is not None and getattr(r, "submitted_by", None) is not None]
    if today_rows:
        latest_row = max(today_rows, key=lambda r: r.updated_at or r.created_at)
        submitter = db.query(User).filter(User.id == latest_row.submitted_by).first()
        if submitter is not None:
            last_submitted_by = {
                "user_id": submitter.id,
                "username": submitter.username,
                "full_name": submitter.full_name,
            }

    latest_financial = db.query(HeadOfficeDailyFinancial).order_by(HeadOfficeDailyFinancial.report_date.desc()).first()
    latest_activity = db.query(HeadOfficeActivityLog).order_by(HeadOfficeActivityLog.report_date.desc()).first()
    latest_health = db.query(HeadOfficeHealthStatus).order_by(HeadOfficeHealthStatus.report_date.desc()).first()
    latest_social = db.query(HeadOfficeSocialLog).order_by(HeadOfficeSocialLog.report_date.desc()).first()

    current_month_start = date(report_date.year, report_date.month, 1)
    monthly_target = (
        db.query(HeadOfficeMonthlyTarget)
        .filter(HeadOfficeMonthlyTarget.target_month == current_month_start)
        .first()
    )
    cumulative_performance = evaluate_ho_cumulative_health(db, monthly_target, current_month_start, report_date)
    budget_status = cumulative_performance["status"].lower() if cumulative_performance else "grey"

    quadrant = compute_ho_quadrant_summary(latest_financial, financial, latest_activity, latest_social, latest_health, budget_status=budget_status)
    monthly_social = compute_monthly_social_counts(db, HeadOfficeSocialLog, [], current_month_start, report_date)

    financial_history = db.query(HeadOfficeDailyFinancial).order_by(HeadOfficeDailyFinancial.report_date.desc()).limit(62).all()
    activity_history = db.query(HeadOfficeActivityLog).order_by(HeadOfficeActivityLog.report_date.desc()).limit(30).all()
    health_history = db.query(HeadOfficeHealthStatus).order_by(HeadOfficeHealthStatus.report_date.desc()).limit(30).all()
    social_history = db.query(HeadOfficeSocialLog).order_by(HeadOfficeSocialLog.report_date.desc()).limit(30).all()

    # Day-by-Day Review's per-row Cumulative Net Revenue needs every day of a
    # row's own calendar month present, so targets are looked up per distinct
    # (year, month) present in financial_history — same approach as
    # /api/admin/business-unit/{id} and /api/ceo/dashboard.
    target_months = {date(f.report_date.year, f.report_date.month, 1) for f in financial_history}
    targets_by_month = {}
    if target_months:
        for t in db.query(HeadOfficeMonthlyTarget).filter(HeadOfficeMonthlyTarget.target_month.in_(target_months)).all():
            targets_by_month[t.target_month] = t

    return {
        "report_date": report_date.isoformat(),
        "report_date_display": report_date.strftime("%A, %d %B %Y"),
        "last_submitted_by": last_submitted_by,
        "submission_events_today": todays_submission_events(db, user.id, "HO_DAILY_REPORT_SUBMITTED", report_date),
        "financial": {
            "revenue": float(financial.revenue) if financial else None,
            "cash_flow": float(financial.cash_flow) if financial and financial.cash_flow is not None else None,
            "receivable": float(financial.receivable) if financial and financial.receivable is not None else None,
            "payable": float(financial.payable) if financial and financial.payable is not None else None,
            "other_amount": float(financial.other_amount) if financial and financial.other_amount is not None else None,
            "other_notes": financial.other_notes if financial else None,
        } if financial else None,
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
        } if activity else None,
        "health": {
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
        "quadrant": {
            **quadrant,
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
        },
    }


@router.get("/monthly-target")
def get_monthly_target(month: str = "", user: User = Depends(require_roles(Role.HO)), db: Session = Depends(get_db)):
    """Fetches the target already set for the given month (default: current
    month) — mirrors /api/ceo/monthly-target so the "Set Monthly Target" form
    can pre-fill the same way."""
    today = today_local()
    year, month_num = parse_month_or_default(month, today)
    target_month = date(year, month_num, 1)

    target = db.query(HeadOfficeMonthlyTarget).filter(HeadOfficeMonthlyTarget.target_month == target_month).first()
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
    user: User = Depends(require_roles(Role.HO)),
    db: Session = Depends(get_db),
):
    """Sets Head Office's monthly target for the given month — but only once,
    same lock CEOs are held to (see /api/ceo/monthly-target): only Chairman/HO
    (via the admin override endpoint) can revise it after that."""
    try:
        target_month = date.fromisoformat(payload.target_month)
        if target_month.day != 1:
            target_month = target_month.replace(day=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="target_month must be a valid ISO date")

    # M-03: same edit window as the CEO targets — a past month is only
    # settable within the window after that month ends.
    window_error = monthly_target_window_error(target_month, today_local(), settings.REPORT_EDIT_WINDOW_DAYS)
    if window_error:
        raise HTTPException(status_code=400, detail=window_error)

    # Rejects NaN / Infinity / abs >= 1e13 / negative revenue-receivable-payable
    # targets with a 400 (pydantic's float lets Infinity and huge values through).
    amounts = parse_monthly_target_amounts(payload)

    existing = db.query(HeadOfficeMonthlyTarget).filter(HeadOfficeMonthlyTarget.target_month == target_month).first()
    if existing is not None:
        raise HTTPException(
            status_code=403,
            detail="This month's target has already been set and is locked — contact Head Office if it needs to change.",
        )

    target = HeadOfficeMonthlyTarget(target_month=target_month)
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

    log_audit(db, user, "HO_MONTHLY_TARGET_SET", f"month={target_month.isoformat()}", request)

    return {"ok": True, "target_month": target_month.isoformat()}


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
    user: User = Depends(require_roles(Role.HO)),
    db: Session = Depends(get_db),
):
    # Deliberately a plain `def`, not `async def`: everything below is blocking
    # (SQLAlchemy queries with row locks, a synchronous boto3 upload of up to
    # 10 MB), so FastAPI must run it in its threadpool instead of on the event
    # loop, where it would stall every other request while locks are held.
    # UploadFile.file is a regular SpooledTemporaryFile, safe to use from here.
    if _is_rate_limited(user.id):
        log_audit(db, user, "HO_REPORT_SUBMIT_RATE_LIMITED", "", request)
        raise HTTPException(status_code=429, detail="Too many submissions in a short time. Please wait a moment and try again.")

    report_date = today_local()

    # --- Validate every input up front, before any DB work, row lock or
    # upload. Invalid values are a clear HTTP 400 - never a 500, and never
    # stored (NaN/Infinity/huge money would poison the dashboards' SUMs). The
    # ranges mirror the database CHECK constraints. Blank stays "not given". ---
    revenue_val = form_money(revenue, "Revenue")
    cash_flow_val = form_money(cash_flow, "Cash Flow")
    receivable_val = form_money(receivable, "Receivable")
    payable_val = form_money(payable, "Payable")
    # Optional - mirrors /api/ceo/submit-report's Other Amount, defaults to 0
    # rather than blocking submission when left blank.
    other_amount_val = form_money(other_amount, "Other Amount") or Decimal("0")

    # Financial fields are mandatory every day, same as a CEO's daily report.
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

    # m-13: the attachment is validated and uploaded BEFORE any row is
    # locked or written, and with NO database session held — same shape
    # as /api/ceo/submit-report. get_db checks a pooled connection out
    # (SELECT 1) before the handler runs, and the old code then took
    # this day's row locks before the R2 upload, so a slow upload (up
    # to ~90 s) held a pooled connection AND row locks. The pre-upload
    # locks are not needed for orphan-safety: the write phase below
    # re-takes the same row locks, a first-submission race still
    # surfaces as the 409 at commit time (whose handler deletes the
    # loser's upload), and a resubmission's post-commit cleanup deletes
    # the previous attachment in every interleaving. So the upload now
    # runs between validation and the writes, after db.close() releases
    # the pooled connection; the write phase re-uses the same session
    # object, which checks a fresh connection out on its next query.
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

        # Release the pooled connection checked out by get_db before the
        # (potentially slow) R2 upload; the session is reused below.
        db.close()
        # D-4: an attachment that cannot be stored fails the submit (503)
        # instead of saving the report as if the evidence were attached.
        document_url = upload_attachment_or_503(attachment.file, attachment.filename)

    # M-14: no_autoflush for the four lookup-or-create blocks below (and
    # the assert_token_current check just before the commit, which queries
    # too) — same guard as /api/ceo/submit-report. Each block adds a new
    # per-day row and the NEXT block's lookup would autoflush that pending
    # INSERT — on a concurrent double submit the flush hits the per-day
    # unique constraint OUTSIDE the try/except around db.commit() below
    # and 500s instead of 409ing. With autoflush off, nothing is sent to
    # the DB until the commit, whose IntegrityError handler is the 409
    # guard. The lookups only ever read already-committed rows (each block
    # queries a different table), so deferring the flush changes no result.
    with db.no_autoflush:
        financial = (
            db.query(HeadOfficeDailyFinancial)
            .filter(HeadOfficeDailyFinancial.report_date == report_date)
            .with_for_update()
            .first()
        )
        # Captured before being overwritten below, so a same-day resubmission's
        # audit log entry can show exactly what changed rather than just that a
        # submission happened — mirrors /api/ceo/submit-report's same tamper-evidence.
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
            financial = HeadOfficeDailyFinancial(report_date=report_date)
            db.add(financial)
        financial.revenue = revenue_val
        financial.cash_flow = cash_flow_val
        financial.receivable = receivable_val
        financial.payable = payable_val
        financial.other_amount = other_amount_val
        financial.other_notes = financial_other_notes or None
        financial.submitted_by = user.id

        activity = (
            db.query(HeadOfficeActivityLog)
            .filter(HeadOfficeActivityLog.report_date == report_date)
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
            activity = HeadOfficeActivityLog(report_date=report_date)
            db.add(activity)
        # SM-7: captured before being overwritten below, so a resubmission that
        # replaces the attachment can delete the previous R2 object afterwards.
        previous_document_url = activity.document_url
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
            db.query(HeadOfficeHealthStatus)
            .filter(HeadOfficeHealthStatus.report_date == report_date)
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
            health = HeadOfficeHealthStatus(report_date=report_date)
            db.add(health)
        health.daily_steps = daily_steps_val
        health.heart_rate = heart_rate_val
        health.sleep_hours = sleep_hours_val
        health.bp_systolic = bp_systolic_val
        health.bp_diastolic = bp_diastolic_val
        health.other_notes = health_other_notes or None
        health.submitted_by = user.id

        social = (
            db.query(HeadOfficeSocialLog)
            .filter(HeadOfficeSocialLog.report_date == report_date)
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
            social = HeadOfficeSocialLog(report_date=report_date)
            db.add(social)
        social.event_flag = parse_bool(event_flag)
        social.meeting_flag = parse_bool(meeting_flag)
        social.golf_flag = parse_bool(golf_flag)
        social.other_notes = social_other_notes or None
        social.submitted_by = user.id

        # m-05 (SM-1): same mid-request revocation re-check as the CEO
        # submit path — immediately before the commit.
        assert_token_current(db, user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if document_url:
            # SM-7: the loser's upload is already in R2 with no DB row
            # pointing at it — delete it instead of orphaning it (and its
            # upload-budget slot) on every double-click.
            delete_object(extract_r2_key(document_url))
        log_audit(db, user, "HO_REPORT_SUBMIT_CONFLICT", f"date={report_date.isoformat()}", request)
        raise HTTPException(
            status_code=409,
            detail="Another submission for today was saved at the same moment — please try again.",
        )

    if document_url and previous_document_url and previous_document_url != document_url:
        # SM-7: this resubmission replaced the previous attachment — delete
        # the orphaned object instead of leaving it in R2 forever.
        delete_object(extract_r2_key(previous_document_url))

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
    log_audit(db, user, "HO_DAILY_REPORT_SUBMITTED", audit_detail, request)

    return {"ok": True}
