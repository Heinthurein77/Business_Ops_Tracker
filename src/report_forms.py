"""Parsed daily-report forms (B-M1).

Each submit-report handler used to take 36 (CEO) / 24 (HO) scalar Form
parameters and do all of its own parsing, so every new field touched the
signature, the parsing, and a test-only normalisation block in ceo.py.
Now the FormData is parsed once, by a dependency, into a typed dataclass
— the handler signature is just (form, user, db) — and the API contract
is unchanged: the frontend still posts the same FormData field names, and
every validation error (status + detail) is identical.

The builders (`build_ceo_report_form` / `build_ho_report_form`) are plain
functions so the dependency is a thin wrapper around them; direct-call
tests use the builders too instead of faking Form(...) declarations.
Order inside each builder mirrors the old handlers exactly: rate limit,
then date window, then field validation, then the attachment upload.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from src.auth import require_roles
from src.config import settings
from src.constants import Role
from src.database import get_db
from src.models import User
from src.services.daily_report import upload_report_attachment
from src.utils import (
    BP_DIASTOLIC_RANGE,
    BP_SYSTOLIC_RANGE,
    HEART_RATE_RANGE,
    MAX_NARRATIVE_TEXT_LEN,
    SLEEP_HOURS_RANGE,
    STEPS_RANGE,
    form_decimal,
    form_int,
    form_money,
    form_percent,
    form_text,
    log_audit,
    parse_bool,
    parse_bounded_decimal,
    report_date_window_error,
    SubmitRateLimiter,
    today_local,
    validate_blood_pressure,
)

# Legacy HealthStatus chip value sets — the health form collects these
# again as one-tap chips, and only these exact values may be stored (the
# dashboards switch on them).
STRESS_LEVELS = ("Low", "Medium", "High")
ENERGY_MOODS = ("Energetic", "Normal", "Drained")


@dataclass
class CeoReportForm:
    """A CEO daily report, fully parsed and validated. Money/percent/int
    fields hold their parsed values (None = not given); free text holds
    form_text's stripped string ("" = blank)."""

    report_date: date
    revenue: Decimal
    cash_flow: Decimal
    receivable: Decimal
    payable: Decimal
    other_amount: Decimal
    financial_other_notes: str
    meetings_attended: str
    meetings_count: Optional[int]
    site_visits: str
    key_decisions: str
    key_achievement: str
    next_day_schedule: str
    problems_today: str
    help_needed: str
    gym_percent: Optional[int]
    swimming_percent: Optional[int]
    cycle_percent: Optional[int]
    activity_other_notes: str
    daily_steps: Optional[int]
    heart_rate: Optional[int]
    sleep_hours: Optional[Decimal]
    bp_systolic: Optional[int]
    bp_diastolic: Optional[int]
    stress_level: Optional[str]
    energy_mood: Optional[str]
    weight_kg: Optional[Decimal]
    is_sick: bool
    health_exercise_present: bool
    health_gym_percent: Optional[int]
    health_swimming_percent: Optional[int]
    health_cycle_percent: Optional[int]
    health_other_notes: str
    event_flag: bool
    meeting_flag: bool
    golf_flag: bool
    social_other_notes: str
    document_url: Optional[str]


@dataclass
class HoReportForm:
    """An HO daily report, fully parsed and validated. HO reports are
    always for today (no report_date field) and have no meetings-count /
    key-achievement / problems / help / stress / weight fields."""

    report_date: date
    revenue: Decimal
    cash_flow: Decimal
    receivable: Decimal
    payable: Decimal
    other_amount: Decimal
    financial_other_notes: str
    meetings_attended: str
    site_visits: str
    key_decisions: str
    next_day_schedule: str
    gym_percent: Optional[int]
    swimming_percent: Optional[int]
    cycle_percent: Optional[int]
    activity_other_notes: str
    daily_steps: Optional[int]
    heart_rate: Optional[int]
    sleep_hours: Optional[Decimal]
    bp_systolic: Optional[int]
    bp_diastolic: Optional[int]
    health_other_notes: str
    event_flag: bool
    meeting_flag: bool
    golf_flag: bool
    social_other_notes: str
    document_url: Optional[str]


def _check_rate_limit(rate_limiter: SubmitRateLimiter, request: Request, user: User, db: Session, action: str) -> None:
    if rate_limiter.is_limited(user.id):
        log_audit(db, user, action, "", request)
        raise HTTPException(status_code=429, detail="Too many submissions in a short time. Please wait a moment and try again.")


def _form_choice(raw: Optional[str], allowed: tuple, label: str) -> Optional[str]:
    """Optional chip value: blank -> None; anything outside the allowed
    set -> 422 (a tampered client must not store free text in a column
    the dashboards treat as an enum)."""
    value = (raw or "").strip()
    if not value:
        return None
    if value not in allowed:
        raise HTTPException(status_code=422, detail=f"{label} must be one of: {', '.join(allowed)}")
    return value


def _form_health_percent(raw: Optional[str], label: str) -> Optional[int]:
    """Exercise % sent by the health section (health_*_percent fields).
    None means the field was absent entirely (an older client) and the
    caller keeps the pre-move behavior; a present value must be a whole
    number 0-100, else 422. Blank -> None (not given)."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{label} must be a whole number between 0 and 100")
    if not 0 <= value <= 100:
        raise HTTPException(status_code=422, detail=f"{label} must be between 0 and 100")
    return value


def _form_weight(raw: Optional[str]) -> Optional[Decimal]:
    """Optional body weight in kg: blank -> None; non-numeric or outside
    the sane 20-400 kg range -> 422."""
    if raw is None or raw.strip() == "":
        return None
    value = parse_bounded_decimal(raw, places=1, limit=Decimal(10) ** 6)
    if value is None or not (Decimal("20") <= value <= Decimal("400")):
        raise HTTPException(status_code=422, detail="Weight must be a number between 20 and 400 kg")
    return value


def _validate_common_financial(revenue, cash_flow, receivable, payable, other_amount):
    """The four mandatory money fields + optional Other Amount, shared by
    both reports. Returns (revenue, cash_flow, receivable, payable,
    other_amount) as Decimals. Blank stays "not given" until the mandatory
    check below; Other Amount defaults to 0 rather than blocking
    submission when left blank."""
    revenue_val = form_money(revenue, "Revenue")
    cash_flow_val = form_money(cash_flow, "Cash Flow")
    receivable_val = form_money(receivable, "Receivable")
    payable_val = form_money(payable, "Payable")
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
    return revenue_val, cash_flow_val, receivable_val, payable_val, other_amount_val


def _validate_common_vitals(daily_steps, heart_rate, sleep_hours, bp_systolic, bp_diastolic):
    daily_steps_val = form_int(daily_steps, *STEPS_RANGE)
    heart_rate_val = form_int(heart_rate, *HEART_RATE_RANGE)
    sleep_hours_val = form_decimal(sleep_hours, *SLEEP_HOURS_RANGE, places=1)
    bp_systolic_val = form_int(bp_systolic, *BP_SYSTOLIC_RANGE)
    bp_diastolic_val = form_int(bp_diastolic, *BP_DIASTOLIC_RANGE)
    validate_blood_pressure(bp_systolic_val, bp_diastolic_val)
    return daily_steps_val, heart_rate_val, sleep_hours_val, bp_systolic_val, bp_diastolic_val


def build_ceo_report_form(
    *,
    request: Request,
    user: User,
    db: Session,
    rate_limiter: SubmitRateLimiter,
    report_date: str = "",
    revenue: str = "",
    cash_flow: str = "",
    receivable: str = "",
    payable: str = "",
    other_amount: str = "",
    financial_other_notes: str = "",
    meetings_attended: str = "",
    meetings_count: str = "",
    site_visits: str = "",
    key_decisions: str = "",
    key_achievement: str = "",
    next_day_schedule: str = "",
    problems_today: str = "",
    help_needed: str = "",
    gym_percent: str = "",
    swimming_percent: str = "",
    cycle_percent: str = "",
    activity_other_notes: str = "",
    daily_steps: str = "",
    heart_rate: str = "",
    sleep_hours: str = "",
    bp_systolic: str = "",
    bp_diastolic: str = "",
    stress_level: str = "",
    energy_mood: str = "",
    weight_kg: str = "",
    is_sick: str = "false",
    health_gym_percent: Optional[str] = None,
    health_swimming_percent: Optional[str] = None,
    health_cycle_percent: Optional[str] = None,
    health_other_notes: str = "",
    event_flag: str = "false",
    meeting_flag: str = "false",
    golf_flag: str = "false",
    social_other_notes: str = "",
    attachment=None,
) -> CeoReportForm:
    _check_rate_limit(rate_limiter, request, user, db, "REPORT_SUBMIT_RATE_LIMITED")

    # The dashboard form normally files today's report; an explicit
    # report_date (sent when the dashboard was opened from the History
    # calendar for a specific day) files that day's report instead. The
    # business unit always comes from the caller's own assignment, never
    # from the request, so a CEO can only ever write to their own unit.
    # M-03: only dates inside the edit window are accepted — today plus
    # the previous REPORT_EDIT_WINDOW_DAYS days. Older dates (and future
    # dates) are rejected; correcting them is an admin-approved path.
    if report_date and report_date.strip():
        try:
            parsed_date = date.fromisoformat(report_date.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="report_date must be a valid date (YYYY-MM-DD)")
    else:
        parsed_date = today_local()
    window_error = report_date_window_error(parsed_date, today_local(), settings.REPORT_EDIT_WINDOW_DAYS)
    if window_error:
        raise HTTPException(status_code=400, detail=window_error)

    # --- Validate every input up front, before any DB work, row lock or
    # upload. Invalid values are a clear HTTP 400 — never a 500, and never
    # stored (NaN/Infinity/huge money would poison the dashboards' SUMs). The
    # ranges mirror the database CHECK constraints. Blank stays "not given". ---
    revenue_val, cash_flow_val, receivable_val, payable_val, other_amount_val = _validate_common_financial(
        revenue, cash_flow, receivable, payable, other_amount
    )
    daily_steps_val, heart_rate_val, sleep_hours_val, bp_systolic_val, bp_diastolic_val = _validate_common_vitals(
        daily_steps, heart_rate, sleep_hours, bp_systolic, bp_diastolic
    )

    gym_percent_val = form_percent(gym_percent, "Gym %")
    swimming_percent_val = form_percent(swimming_percent, "Swimming %")
    cycle_percent_val = form_percent(cycle_percent, "Cycle %")

    meetings_count_val = form_int(meetings_count, "Meetings count", 0, 99)

    # Health-section additions (2026-10-22): the chips, weight and sick
    # toggle validate to 422 — they are new fields, so they follow the
    # JSON-API convention rather than the legacy form 400s above. The
    # health_*_percent fields' mere presence switches exercise storage
    # from the activity row to the health row (see the writes in ceo.py).
    stress_level_val = _form_choice(stress_level, STRESS_LEVELS, "Stress level")
    energy_mood_val = _form_choice(energy_mood, ENERGY_MOODS, "Energy mood")
    weight_kg_val = _form_weight(weight_kg)
    is_sick_val = parse_bool(is_sick)
    health_exercise_present = (
        health_gym_percent is not None or health_swimming_percent is not None or health_cycle_percent is not None
    )
    health_gym_percent_val = _form_health_percent(health_gym_percent, "Gym %")
    health_swimming_percent_val = _form_health_percent(health_swimming_percent, "Swimming %")
    health_cycle_percent_val = _form_health_percent(health_cycle_percent, "Cycle %")

    document_url = upload_report_attachment(db, attachment)

    return CeoReportForm(
        report_date=parsed_date,
        revenue=revenue_val,
        cash_flow=cash_flow_val,
        receivable=receivable_val,
        payable=payable_val,
        other_amount=other_amount_val,
        financial_other_notes=form_text(financial_other_notes, "Financial notes", MAX_NARRATIVE_TEXT_LEN),
        meetings_attended=form_text(meetings_attended, "Meetings attended", MAX_NARRATIVE_TEXT_LEN),
        meetings_count=meetings_count_val,
        site_visits=form_text(site_visits, "Site visits", MAX_NARRATIVE_TEXT_LEN),
        key_decisions=form_text(key_decisions, "Key decisions", MAX_NARRATIVE_TEXT_LEN),
        key_achievement=form_text(key_achievement, "Key achievement", MAX_NARRATIVE_TEXT_LEN),
        next_day_schedule=form_text(next_day_schedule, "Next day schedule", MAX_NARRATIVE_TEXT_LEN),
        problems_today=form_text(problems_today, "Problems today", MAX_NARRATIVE_TEXT_LEN),
        help_needed=form_text(help_needed, "Help needed", MAX_NARRATIVE_TEXT_LEN),
        gym_percent=gym_percent_val,
        swimming_percent=swimming_percent_val,
        cycle_percent=cycle_percent_val,
        activity_other_notes=form_text(activity_other_notes, "Activity notes", MAX_NARRATIVE_TEXT_LEN),
        daily_steps=daily_steps_val,
        heart_rate=heart_rate_val,
        sleep_hours=sleep_hours_val,
        bp_systolic=bp_systolic_val,
        bp_diastolic=bp_diastolic_val,
        stress_level=stress_level_val,
        energy_mood=energy_mood_val,
        weight_kg=weight_kg_val,
        is_sick=is_sick_val,
        health_exercise_present=health_exercise_present,
        health_gym_percent=health_gym_percent_val,
        health_swimming_percent=health_swimming_percent_val,
        health_cycle_percent=health_cycle_percent_val,
        health_other_notes=form_text(health_other_notes, "Health notes", MAX_NARRATIVE_TEXT_LEN),
        event_flag=parse_bool(event_flag),
        meeting_flag=parse_bool(meeting_flag),
        golf_flag=parse_bool(golf_flag),
        social_other_notes=form_text(social_other_notes, "Social notes", MAX_NARRATIVE_TEXT_LEN),
        document_url=document_url,
    )


def build_ho_report_form(
    *,
    request: Request,
    user: User,
    db: Session,
    rate_limiter: SubmitRateLimiter,
    revenue: str = "",
    cash_flow: str = "",
    receivable: str = "",
    payable: str = "",
    other_amount: str = "",
    financial_other_notes: str = "",
    meetings_attended: str = "",
    site_visits: str = "",
    key_decisions: str = "",
    next_day_schedule: str = "",
    gym_percent: str = "",
    swimming_percent: str = "",
    cycle_percent: str = "",
    activity_other_notes: str = "",
    daily_steps: str = "",
    heart_rate: str = "",
    sleep_hours: str = "",
    bp_systolic: str = "",
    bp_diastolic: str = "",
    health_other_notes: str = "",
    event_flag: str = "false",
    meeting_flag: str = "false",
    golf_flag: str = "false",
    social_other_notes: str = "",
    attachment=None,
) -> HoReportForm:
    _check_rate_limit(rate_limiter, request, user, db, "HO_REPORT_SUBMIT_RATE_LIMITED")

    report_date = today_local()

    revenue_val, cash_flow_val, receivable_val, payable_val, other_amount_val = _validate_common_financial(
        revenue, cash_flow, receivable, payable, other_amount
    )
    daily_steps_val, heart_rate_val, sleep_hours_val, bp_systolic_val, bp_diastolic_val = _validate_common_vitals(
        daily_steps, heart_rate, sleep_hours, bp_systolic, bp_diastolic
    )

    gym_percent_val = form_percent(gym_percent, "Gym %")
    swimming_percent_val = form_percent(swimming_percent, "Swimming %")
    cycle_percent_val = form_percent(cycle_percent, "Cycle %")

    document_url = upload_report_attachment(db, attachment)

    return HoReportForm(
        report_date=report_date,
        revenue=revenue_val,
        cash_flow=cash_flow_val,
        receivable=receivable_val,
        payable=payable_val,
        other_amount=other_amount_val,
        financial_other_notes=form_text(financial_other_notes, "Financial notes", MAX_NARRATIVE_TEXT_LEN),
        meetings_attended=form_text(meetings_attended, "Meetings attended", MAX_NARRATIVE_TEXT_LEN),
        site_visits=form_text(site_visits, "Site visits", MAX_NARRATIVE_TEXT_LEN),
        key_decisions=form_text(key_decisions, "Key decisions", MAX_NARRATIVE_TEXT_LEN),
        next_day_schedule=form_text(next_day_schedule, "Next day schedule", MAX_NARRATIVE_TEXT_LEN),
        gym_percent=gym_percent_val,
        swimming_percent=swimming_percent_val,
        cycle_percent=cycle_percent_val,
        activity_other_notes=form_text(activity_other_notes, "Activity notes", MAX_NARRATIVE_TEXT_LEN),
        daily_steps=daily_steps_val,
        heart_rate=heart_rate_val,
        sleep_hours=sleep_hours_val,
        bp_systolic=bp_systolic_val,
        bp_diastolic=bp_diastolic_val,
        health_other_notes=form_text(health_other_notes, "Health notes", MAX_NARRATIVE_TEXT_LEN),
        event_flag=parse_bool(event_flag),
        meeting_flag=parse_bool(meeting_flag),
        golf_flag=parse_bool(golf_flag),
        social_other_notes=form_text(social_other_notes, "Social notes", MAX_NARRATIVE_TEXT_LEN),
        document_url=document_url,
    )


def ceo_report_form(rate_limiter: SubmitRateLimiter) -> Callable:
    """FastAPI dependency factory: binds the CEO rate limiter (each router
    owns one instance so CEO and HO keep separate budgets) and returns the
    dependency that parses the multipart form into a CeoReportForm."""

    def parse(
        request: Request,
        report_date: str = Form(""),
        revenue: str = Form(""),
        cash_flow: str = Form(""),
        receivable: str = Form(""),
        payable: str = Form(""),
        other_amount: str = Form(""),
        financial_other_notes: str = Form(""),
        meetings_attended: str = Form(""),
        meetings_count: str = Form(""),
        site_visits: str = Form(""),
        key_decisions: str = Form(""),
        key_achievement: str = Form(""),
        next_day_schedule: str = Form(""),
        problems_today: str = Form(""),
        help_needed: str = Form(""),
        gym_percent: str = Form(""),
        swimming_percent: str = Form(""),
        cycle_percent: str = Form(""),
        activity_other_notes: str = Form(""),
        daily_steps: str = Form(""),
        heart_rate: str = Form(""),
        sleep_hours: str = Form(""),
        bp_systolic: str = Form(""),
        bp_diastolic: str = Form(""),
        stress_level: str = Form(""),
        energy_mood: str = Form(""),
        weight_kg: str = Form(""),
        is_sick: str = Form("false"),
        health_gym_percent: Optional[str] = Form(None),
        health_swimming_percent: Optional[str] = Form(None),
        health_cycle_percent: Optional[str] = Form(None),
        health_other_notes: str = Form(""),
        event_flag: str = Form("false"),
        meeting_flag: str = Form("false"),
        golf_flag: str = Form("false"),
        social_other_notes: str = Form(""),
        attachment: Optional[UploadFile] = File(None),
        user: User = Depends(require_roles(Role.CEO)),
        db: Session = Depends(get_db),
    ) -> CeoReportForm:
        return build_ceo_report_form(
            request=request, user=user, db=db, rate_limiter=rate_limiter,
            report_date=report_date, revenue=revenue, cash_flow=cash_flow,
            receivable=receivable, payable=payable, other_amount=other_amount,
            financial_other_notes=financial_other_notes, meetings_attended=meetings_attended,
            meetings_count=meetings_count, site_visits=site_visits, key_decisions=key_decisions,
            key_achievement=key_achievement, next_day_schedule=next_day_schedule,
            problems_today=problems_today, help_needed=help_needed, gym_percent=gym_percent,
            swimming_percent=swimming_percent, cycle_percent=cycle_percent,
            activity_other_notes=activity_other_notes, daily_steps=daily_steps,
            heart_rate=heart_rate, sleep_hours=sleep_hours, bp_systolic=bp_systolic,
            bp_diastolic=bp_diastolic, stress_level=stress_level, energy_mood=energy_mood,
            weight_kg=weight_kg, is_sick=is_sick, health_gym_percent=health_gym_percent,
            health_swimming_percent=health_swimming_percent, health_cycle_percent=health_cycle_percent,
            health_other_notes=health_other_notes, event_flag=event_flag,
            meeting_flag=meeting_flag, golf_flag=golf_flag, social_other_notes=social_other_notes,
            attachment=attachment,
        )

    return parse


def ho_report_form(rate_limiter: SubmitRateLimiter) -> Callable:
    """FastAPI dependency factory for the HO report form (see above)."""

    def parse(
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
    ) -> HoReportForm:
        return build_ho_report_form(
            request=request, user=user, db=db, rate_limiter=rate_limiter,
            revenue=revenue, cash_flow=cash_flow, receivable=receivable,
            payable=payable, other_amount=other_amount,
            financial_other_notes=financial_other_notes, meetings_attended=meetings_attended,
            site_visits=site_visits, key_decisions=key_decisions,
            next_day_schedule=next_day_schedule, gym_percent=gym_percent,
            swimming_percent=swimming_percent, cycle_percent=cycle_percent,
            activity_other_notes=activity_other_notes, daily_steps=daily_steps,
            heart_rate=heart_rate, sleep_hours=sleep_hours, bp_systolic=bp_systolic,
            bp_diastolic=bp_diastolic, health_other_notes=health_other_notes,
            event_flag=event_flag, meeting_flag=meeting_flag, golf_flag=golf_flag,
            social_other_notes=social_other_notes, attachment=attachment,
        )

    return parse
