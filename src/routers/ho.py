import logging
from datetime import date, timedelta

# PM-m22: alias for datetime.date — ho_dashboard's `date` query parameter
# shadows the class in that function's scope.
_DATE = date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import require_roles
from src.constants import NotificationType, Role
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
from src.report_forms import HoReportForm, ho_report_form
from src.schemas import MonthlyTargetRequest
from src.services.daily_report import DailyReportRowSpec, upsert_daily_report
from src.utils import (
    build_activity_rows,
    build_financial_rows,
    build_health_rows,
    build_resubmit_audit_detail,
    build_social_rows,
    compute_field_changes,
    compute_ho_quadrant_summary,
    compute_monthly_social_counts,
    evaluate_ho_cumulative_health,
    format_local_datetime,
    log_audit,
    monthly_target_window_error,
    notify,
    parse_month_or_today,
    parse_monthly_target_amounts,
    report_date_window_error,
    SubmitRateLimiter,
    today_local,
    todays_submission_events,
)

router = APIRouter(prefix="/api/ho", tags=["ho"])

# Same shape as /api/ceo/submit-report's guard — a real daily report is
# submitted at most a couple of times a day.
REPORT_SUBMIT_RATE_LIMIT = 5
REPORT_SUBMIT_RATE_WINDOW_SECONDS = 60
# M-12: the guard itself lives in src/utils.py (SubmitRateLimiter); this
# router owns one instance so CEO and HO keep separate budgets.
_rate_limiter = SubmitRateLimiter(REPORT_SUBMIT_RATE_LIMIT, REPORT_SUBMIT_RATE_WINDOW_SECONDS)

logger = logging.getLogger(__name__)

@router.get("/dashboard")
def ho_dashboard(date: str = "", user: User = Depends(require_roles(Role.HO)), db: Session = Depends(get_db)):
    # PM-m22: same 7-day backfill affordance as CEOs (?date= editing).
    # No date = today, as before. A date outside the edit window 400s
    # with the shared window error. The `date` parameter shadows the
    # datetime.date class in this scope, so _DATE (module alias) is used
    # for date construction below.
    today = today_local()
    if date:
        try:
            report_date = _DATE.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be a valid ISO date")
        window_error = report_date_window_error(report_date, today, settings.REPORT_EDIT_WINDOW_DAYS)
        if window_error:
            raise HTTPException(status_code=400, detail=window_error)
    else:
        report_date = today

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

    current_month_start = _DATE(report_date.year, report_date.month, 1)
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
    target_months = {_DATE(f.report_date.year, f.report_date.month, 1) for f in financial_history}
    targets_by_month = {}
    if target_months:
        for t in db.query(HeadOfficeMonthlyTarget).filter(HeadOfficeMonthlyTarget.target_month.in_(target_months)).all():
            targets_by_month[t.target_month] = t

    return {
        "report_date": report_date.isoformat(),
        "report_date_display": report_date.strftime("%A, %d %B %Y"),
        # PM-m22: the backfill form keys (same names the CEO dashboard
        # uses), so the HO page can tell "editing a past day" from today.
        "form_date": report_date.isoformat(),
        "form_date_display": report_date.strftime("%A, %d %B %Y"),
        "form_is_today": report_date == today,
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
    year, month_num = parse_month_or_today(month, today)
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


_parse_report_form = ho_report_form(_rate_limiter)


def _apply_ho_report(form: HoReportForm, user: User):
    """Returns the `apply` callback for the shared upsert: assigns the
    parsed HO form to the four per-day rows. Returns the previous
    attachment URL (or None) so a replaced attachment can be deleted
    after the commit."""

    def apply(rows, previous):
        financial = rows["financial"]
        activity = rows["activity"]
        health = rows["health"]
        social = rows["social"]

        financial.revenue = form.revenue
        financial.cash_flow = form.cash_flow
        financial.receivable = form.receivable
        financial.payable = form.payable
        financial.other_amount = form.other_amount
        financial.other_notes = form.financial_other_notes or None
        financial.submitted_by = user.id

        # SM-7: captured before being overwritten below, so a resubmission that
        # replaces the attachment can delete the previous R2 object afterwards.
        previous_document_url = activity.document_url
        activity.meetings_attended = form.meetings_attended
        activity.site_visits = form.site_visits
        activity.key_decisions = form.key_decisions
        activity.next_day_schedule = form.next_day_schedule
        if form.document_url:
            activity.document_url = form.document_url
        activity.gym_percent = form.gym_percent
        activity.swimming_percent = form.swimming_percent
        activity.cycle_percent = form.cycle_percent
        activity.other_notes = form.activity_other_notes or None
        activity.submitted_by = user.id

        health.daily_steps = form.daily_steps
        health.heart_rate = form.heart_rate
        health.sleep_hours = form.sleep_hours
        health.bp_systolic = form.bp_systolic
        health.bp_diastolic = form.bp_diastolic
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
    date: str = "",
    form: HoReportForm = Depends(_parse_report_form),
    user: User = Depends(require_roles(Role.HO)),
    db: Session = Depends(get_db),
):
    # PM-m22: backfill — the HO form parser (src/report_forms.py) has no
    # report_date field (unlike the CEO one), so the date rides as a
    # ?date= query parameter and overrides the form's today here. Same
    # 7-day window as CEOs; outside it, 400 with the shared window error.
    report_date = form.report_date
    if date:
        try:
            report_date = _DATE.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be a valid ISO date")
        window_error = report_date_window_error(report_date, today_local(), settings.REPORT_EDIT_WINDOW_DAYS)
        if window_error:
            raise HTTPException(status_code=400, detail=window_error)
        form.report_date = report_date
    # Deliberately a plain `def`, not `async def`: everything below is blocking
    # (SQLAlchemy queries with row locks, a synchronous boto3 upload of up to
    # 10 MB), so FastAPI must run it in its threadpool instead of on the event
    # loop, where it would stall every other request while locks are held.
    # UploadFile.file is a regular SpooledTemporaryFile, safe to use from here.
    #
    # B-M1: parsing/validation/upload live in src/report_forms.py (the
    # `form` dependency) and the 4-table upsert in
    # src/services/daily_report.py — this handler only wires them together
    # and writes the resubmit audit entry.
    report_date = form.report_date
    specs = [
        DailyReportRowSpec(
            key="financial", cls=HeadOfficeDailyFinancial,
            filters={"report_date": report_date},
            create_kwargs={"report_date": report_date},
            snapshot_fields=(
                ("revenue", "revenue"), ("cash_flow", "cash_flow"),
                ("receivable", "receivable"), ("payable", "payable"),
                ("other_amount", "other_amount"), ("other_notes", "financial_other_notes"),
            ),
        ),
        DailyReportRowSpec(
            key="activity", cls=HeadOfficeActivityLog,
            filters={"report_date": report_date},
            create_kwargs={"report_date": report_date},
            snapshot_fields=(
                ("meetings_attended", "meetings_attended"), ("site_visits", "site_visits"),
                ("key_decisions", "key_decisions"), ("next_day_schedule", "next_day_schedule"),
                ("gym_percent", "gym_percent"), ("swimming_percent", "swimming_percent"),
                ("cycle_percent", "cycle_percent"), ("other_notes", "activity_other_notes"),
            ),
        ),
        DailyReportRowSpec(
            key="health", cls=HeadOfficeHealthStatus,
            filters={"report_date": report_date},
            create_kwargs={"report_date": report_date},
            snapshot_fields=(
                ("daily_steps", "daily_steps"), ("heart_rate", "heart_rate"),
                ("sleep_hours", "sleep_hours"), ("bp_systolic", "bp_systolic"),
                ("bp_diastolic", "bp_diastolic"), ("other_notes", "health_other_notes"),
            ),
        ),
        DailyReportRowSpec(
            key="social", cls=HeadOfficeSocialLog,
            filters={"report_date": report_date},
            create_kwargs={"report_date": report_date},
            snapshot_fields=(
                ("event_flag", "event_flag"), ("meeting_flag", "meeting_flag"),
                ("golf_flag", "golf_flag"), ("other_notes", "social_other_notes"),
            ),
        ),
    ]
    result = upsert_daily_report(
        db=db, request=request, user=user, report_date=report_date, specs=specs,
        document_url=form.document_url, apply=_apply_ho_report(form, user),
        conflict_audit_action="HO_REPORT_SUBMIT_CONFLICT",
    )
    activity = result.rows["activity"]
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
            "financial_other_notes": result.rows["financial"].other_notes,
        }
        current_activity = {
            "meetings_attended": activity.meetings_attended,
            "site_visits": activity.site_visits,
            "key_decisions": activity.key_decisions,
            "next_day_schedule": activity.next_day_schedule,
            "gym_percent": form.gym_percent,
            "swimming_percent": form.swimming_percent,
            "cycle_percent": form.cycle_percent,
            "activity_other_notes": activity.other_notes,
        }
        current_health = {
            "daily_steps": form.daily_steps,
            "heart_rate": form.heart_rate,
            "sleep_hours": form.sleep_hours,
            "bp_systolic": form.bp_systolic,
            "bp_diastolic": form.bp_diastolic,
            "health_other_notes": form.health_other_notes,
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
        # PM-m1 fix: each area is diffed only when its previous row
        # exists — a day can hold a financial row without activity/health/
        # social rows (e.g. a partially backfilled day), and diffing
        # against None 500d. The CEO submit already guards this way.
        changes = compute_field_changes(previous_financial, current_financial)
        if previous_activity is not None:
            changes += compute_field_changes(previous_activity, current_activity)
        if previous_health is not None:
            changes += compute_field_changes(previous_health, current_health)
        if previous_social is not None:
            changes += compute_field_changes(previous_social, current_social)

    # PM-m1: HO submit was silent — share the CEO post-submit alert
    # block. (a) >50% revenue deviation vs the trailing 14-day HO
    # average notifies Chairman/HO; (b) a backdated resubmit notifies
    # Chairman/HO so a restatement of reviewed history is visible.
    # HO has no stored revenue_anomaly column (unlike DailyFinancial),
    # so the anomaly here is notification-only.
    try:
        window_start = report_date - timedelta(days=14)
        prior_amounts = [
            float(r[0] or 0)
            for r in db.query(HeadOfficeDailyFinancial.revenue).filter(
                HeadOfficeDailyFinancial.report_date >= window_start,
                HeadOfficeDailyFinancial.report_date < report_date,
            ).all()
        ]
        anomaly_avg = sum(prior_amounts) / len(prior_amounts) if prior_amounts else None
        if anomaly_avg and anomaly_avg > 0 and abs(float(form.revenue or 0) - anomaly_avg) / anomaly_avg > 0.5:
            deviation = (float(form.revenue or 0) - anomaly_avg) / anomaly_avg * 100
            for recipient in db.query(User).filter(User.role.in_([Role.CHAIRMAN, Role.HO]), User.is_active == True).all():  # noqa: E712
                notify(
                    db, recipient.id, NotificationType.ALERT,
                    "Revenue anomaly: Head Office",
                    f"Revenue Ks {float(form.revenue or 0):,.0f} on {report_date.isoformat()} "
                    f"vs 14-day average Ks {anomaly_avg:,.0f} ({deviation:+.0f}%) — please review.",
                    link="/admin/head-office",
                )
    except Exception as exc:  # noqa: BLE001 - never break a submission over the anomaly check
        logger.warning("HO revenue anomaly check failed: %s", exc)
    if previous_financial is not None and report_date < today_local():
        for recipient in db.query(User).filter(User.role.in_([Role.CHAIRMAN, Role.HO]), User.is_active == True).all():  # noqa: E712
            notify(
                db, recipient.id, NotificationType.ALERT,
                "Backdated report edit: Head Office",
                f"{user.username} resubmitted the {report_date.isoformat()} Head Office report — please review the change.",
                link="/admin/head-office",
            )
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - report already committed
        db.rollback()
        logger.warning("HO post-submit notification commit failed: %s", exc)

    audit_detail = build_resubmit_audit_detail(report_date, changes)
    log_audit(db, user, "HO_DAILY_REPORT_SUBMITTED", audit_detail, request)

    return {"ok": True}
