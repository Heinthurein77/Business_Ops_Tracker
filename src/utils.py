"""
Shared helpers used across routers: audit logging, timezone-aware date
handling, the CEO well-being risk model, and the activity calendar builder.
Kept in one module (rather than duplicated per-router) so the definition of
"critical" never drifts between the CEO dashboard, the admin overview, and
the business-unit detail page.
"""
import calendar as calendar_module
import json
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from src.config import settings
from src.models import ActivityLog, AuditLog, CeoProfile, DailyFinancial, HeadOfficeDailyFinancial, HealthStatus, MonthlyTarget, SocialLog, User

APP_TZ = ZoneInfo(settings.APP_TIMEZONE)

PHYSICAL_HEALTH_OPTIONS = ["Healthy", "Tired", "Sick"]
STRESS_LEVEL_OPTIONS = ["Low", "Medium", "High"]
ENERGY_MOOD_OPTIONS = ["Energetic", "Normal", "Drained"]
SLEEP_QUALITY_OPTIONS = ["Good", "Average", "Poor"]

_RISK_RANK = {"green": 0, "yellow": 1, "red": 2}


def today_local() -> date:
    return datetime.now(APP_TZ).date()


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # 5 = Saturday, 6 = Sunday


_PERIOD_FORMAT = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_period(period: Optional[str]) -> Optional[str]:
    """Validates a YYYY-MM `period`/`month` query value and returns it
    unchanged. Report endpoints used to feed the raw string into
    split('-') / int() and answer garbage like ?period=garbage with a 500
    (audit finding L3); now anything that isn't a real year-month is a
    422, like any other malformed parameter. None and "" pass through —
    callers treat them as "no period given"."""
    if period is None or period == "":
        return period
    if not _PERIOD_FORMAT.match(period):
        raise HTTPException(status_code=422, detail="period must be in YYYY-MM format")
    return period


def calculate_age(date_of_birth: date, as_of: Optional[date] = None) -> int:
    """Whole-years age as of a given date (defaults to today), correctly
    handling whether this year's birthday has occurred yet — a naive
    `as_of.year - dob.year` overcounts by one until the birthday passes."""
    if as_of is None:
        as_of = today_local()
    years = as_of.year - date_of_birth.year
    if (as_of.month, as_of.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return years


def serialize_ceo_profile(ceo_user: Optional[User], profile: Optional[CeoProfile], today: date) -> Optional[dict]:
    """Single source of truth for a CEO's profile payload — used by the
    Executive Dashboard (operations grid), Business Unit Detail, the
    standalone CEO Profiles list, and the CEO's own dashboard, so all four
    always show identical data for the same CEO instead of separate copies
    of this shape."""
    if ceo_user is None:
        return None
    return {
        "full_name": ceo_user.full_name,
        "is_profile_completed": ceo_user.is_profile_completed,
        "executive_title": profile.executive_title if profile else None,
        "phone_number": profile.phone_number if profile else None,
        "email": profile.email if profile else None,
        "date_of_birth": profile.date_of_birth.isoformat() if profile and profile.date_of_birth else None,
        "age": calculate_age(profile.date_of_birth, today) if profile and profile.date_of_birth else None,
        "address": profile.address if profile else None,
        "business_details": profile.business_details if profile else None,
        "profile_pic_url": profile.profile_pic_url if profile else None,
    }


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    zero_based = month - 1 + delta
    return year + zero_based // 12, zero_based % 12 + 1


def step_status(steps: Optional[int]) -> Optional[str]:
    """Classifies a CEO's daily step count into the same red/yellow/green tiers
    used across the well-being guard system: under 3,000 steps is sedentary
    (red), under 7,000 is moderate (yellow), 7,000+ is active (green)."""
    if steps is None:
        return None
    if steps < 3000:
        return "red"
    if steps < 7000:
        return "yellow"
    return "green"


def _vitals_tier(health: HealthStatus) -> Optional[str]:
    """Folds heart rate and blood pressure into the same red/yellow/green
    scale used elsewhere, using standard clinical reference ranges (resting
    HR 60-100 bpm; blood pressure stages per AHA guidance). Returns None when
    neither vital was submitted, so callers can skip it entirely."""
    tiers = []
    if health.heart_rate is not None:
        hr = health.heart_rate
        if hr < 50 or hr > 120:
            tiers.append("red")
        elif hr < 60 or hr > 100:
            tiers.append("yellow")
        else:
            tiers.append("green")
    if health.bp_systolic is not None and health.bp_diastolic is not None:
        sys_bp, dia_bp = health.bp_systolic, health.bp_diastolic
        if sys_bp >= 140 or dia_bp >= 90:
            tiers.append("red")
        elif sys_bp >= 130 or dia_bp >= 80:
            tiers.append("yellow")
        else:
            tiers.append("green")
    if not tiers:
        return None
    return max(tiers, key=lambda t: _RISK_RANK[t])


def health_risk_notes(health: Optional[HealthStatus]) -> list[str]:
    """Human-readable fragments explaining an elevated health risk tier, driven
    only by steps and vitals (heart rate / blood pressure) — physical_health
    and stress_level are legacy fields no longer collected from the form, so
    they never factor into this explanation."""
    if health is None:
        return []
    notes = []
    tier = step_status(health.daily_steps)
    if tier == "red":
        notes.append(f"sedentary day ({health.daily_steps:,} steps)")
    elif tier == "yellow":
        notes.append(f"moderate activity ({health.daily_steps:,} steps)")

    if health.heart_rate is not None:
        hr = health.heart_rate
        if hr < 50 or hr > 120:
            notes.append(f"heart rate out of range ({hr} bpm)")
        elif hr < 60 or hr > 100:
            notes.append(f"elevated heart rate ({hr} bpm)")

    if health.bp_systolic is not None and health.bp_diastolic is not None:
        sys_bp, dia_bp = health.bp_systolic, health.bp_diastolic
        if sys_bp >= 140 or dia_bp >= 90:
            notes.append(f"high blood pressure ({sys_bp}/{dia_bp})")
        elif sys_bp >= 130 or dia_bp >= 80:
            notes.append(f"elevated blood pressure ({sys_bp}/{dia_bp})")
    return notes


def compute_health_risk(health: Optional[HealthStatus]) -> str:
    """Classifies a CEO's latest health submission into a risk tier shared by
    every page that screens for burnout. The step counter and vitals (heart
    rate / blood pressure) are folded in as additional risk factors rather
    than separate metrics, so a sedentary or hypertensive CEO surfaces here
    even if every other self-reported field looks fine."""
    if health is None:
        return "unknown"
    if health.physical_health == "Sick" or health.stress_level == "High":
        tier = "red"
    elif (
        health.physical_health == "Tired"
        or health.stress_level == "Medium"
        or health.energy_mood == "Drained"
        or health.sleep_quality == "Poor"
    ):
        tier = "yellow"
    else:
        tier = "green"

    for other_tier in (step_status(health.daily_steps), _vitals_tier(health)):
        if other_tier and _RISK_RANK[other_tier] > _RISK_RANK[tier]:
            tier = other_tier
    return tier


def evaluate_financial_health(revenue, cash_flow, receivable, payable):
    """Evaluates one day's/month's financial figures into a traffic-light
    status, driven by cash-flow solvency and receivable-to-revenue ratios
    rather than a single fixed amount — so the same rules are equally
    meaningful for a small business unit and a large one.

    Rules (checked in this order; RED takes priority over YELLOW):

    RED (critical risk):
      - Cash flow is negative.

    YELLOW (moderate warning) — any of, only when no RED condition fired:
      - Cash flow < Payable (but the RED insolvency check above didn't fire,
        so receivables are large enough to cover the shortfall on paper).
      - Receivable > Revenue (only evaluated when Revenue > 0).
      - Cash flow is positive but under 20% of Revenue (only evaluated when
        Revenue > 0) — thin margin relative to the top line.

    GREEN (healthy) — none of the above; typically cash flow covers payable,
    cash flow is positive, and receivable is a modest fraction of revenue.

    @param revenue: float|Decimal|None
    @param cash_flow: float|Decimal|None
    @param receivable: float|Decimal|None
    @param payable: float|Decimal|None
    @return: (status_code, status_message, warning_flags)
        status_code: "RED" | "YELLOW" | "GREEN"
        status_message: Burmese-language explanation of the overall tier
        warning_flags: list[str] of the specific English-language issues detected
    """
    revenue = float(revenue or 0)
    cash_flow = float(cash_flow or 0)
    receivable = float(receivable or 0)
    payable = float(payable or 0)

    warning_flags = []
    is_red = False

    if cash_flow < 0:
        warning_flags.append("Cash flow is negative")
        is_red = True

    is_yellow = False
    if not is_red:
        if cash_flow < payable:
            warning_flags.append("Cash flow alone is below payable obligations")
            is_yellow = True
        if revenue > 0 and receivable > revenue:
            warning_flags.append("Receivable exceeds total revenue")
            is_yellow = True
        if revenue > 0 and 0 < cash_flow < revenue * 0.2:
            warning_flags.append("Cash flow is positive but under 20% of revenue")
            is_yellow = True

    if is_red:
        status_code = "RED"
        status_message = "ငွေကြေးအခြေအနေမှာ အလွန်အန္တရာယ်ရှိပါသည် — ချက်ချင်းဆောင်ရွက်ရန် လိုအပ်ပါသည်။"
    elif is_yellow:
        status_code = "YELLOW"
        status_message = "ငွေကြေးအခြေအနေမှာ သတိပေးအဆင့်ဖြစ်ပြီး နီးကပ်စွာ စောင့်ကြည့်ရန် လိုအပ်ပါသည်။"
    else:
        status_code = "GREEN"
        status_message = "ငွေကြေးအခြေအနေ ကောင်းမွန်ပြီး ကျန်းမာသောအခြေအနေတွင် ရှိပါသည်။"

    return status_code, status_message, warning_flags


def financial_quadrant_status(latest_fin: Optional[DailyFinancial], today_fin: Optional[DailyFinancial]) -> str:
    """Financial quadrant of the BU health circle. Red if today's mandatory
    submission is missing or evaluate_financial_health() rates the latest
    submission RED, yellow if YELLOW, green if GREEN, grey if nothing has
    ever been submitted. Financial submission is mandatory every day — no
    weekday/weekend exception."""
    if today_fin is None:
        return "red"
    if latest_fin is None or latest_fin.cash_flow is None:
        return "grey"
    status_code, _, _ = evaluate_financial_health(
        latest_fin.revenue, latest_fin.cash_flow, latest_fin.receivable, latest_fin.payable
    )
    return status_code.lower()


def resolve_financial_status(
    latest_financial: Optional[DailyFinancial],
    today_financial: Optional[DailyFinancial],
    budget_status: Optional[str] = None,
) -> str:
    """Single source of truth for the Financial quadrant's red/yellow/green
    verdict — used by compute_bu_quadrant_summary (Business Unit Detail, CEO
    Dashboard) and the Executive Dashboard/Operations grid, so the two can
    never disagree about the same BU on the same day (this exact class of
    drift has happened twice now from each call site re-implementing its own
    copy of the override order).

    Priority order: a missing mandatory submission today, or no financial
    data at all, always wins over the budget-status override — resolve_daily_
    performance() can now fall back to a past day's figures when today is
    blank, so a good budget_status alone must never paper over today's
    missing report."""
    if today_financial is None:
        return "red"
    if latest_financial is None or latest_financial.cash_flow is None:
        return "grey"
    if budget_status == "green":
        return "green"
    if budget_status == "red":
        return "red"
    return financial_quadrant_status(latest_financial, today_financial)


def activity_quadrant_status(latest_activity: Optional[ActivityLog]) -> str:
    """Activity quadrant based on the average of the three fitness percentages
    (Gym / Swimming / Cycle) — grey if none were ever submitted."""
    if latest_activity is None:
        return "grey"
    percents = [p for p in (latest_activity.gym_percent, latest_activity.swimming_percent, latest_activity.cycle_percent) if p is not None]
    if not percents:
        return "grey"
    avg = sum(percents) / len(percents)
    if avg >= 70:
        return "green"
    if avg >= 40:
        return "yellow"
    return "red"


def social_quadrant_status(latest_social: Optional[SocialLog]) -> str:
    """Social quadrant: there's no inherently 'bad' toggle combination here
    (attending a golf outing isn't a red flag), so this only distinguishes
    reported engagement (green) from a submitted-but-idle day (yellow) from
    never having submitted at all (grey)."""
    if latest_social is None:
        return "grey"
    engaged = latest_social.event_flag or latest_social.meeting_flag or latest_social.golf_flag
    return "green" if engaged else "yellow"


def compute_monthly_social_counts(db: Session, model, extra_filters: list, month_start: date, current_date: date) -> dict:
    """Tallies how many days this month (day 1 of month_start through
    current_date inclusive) had each type of social engagement — Event /
    Meeting / Golf — counted from the daily Yes/No flags already being
    submitted, not a separate tracked metric. Generic over SocialLog
    (business-unit-scoped, pass [SocialLog.business_unit_id == bu_id] as
    extra_filters) and HeadOfficeSocialLog (HO has no business unit to
    scope by, pass []) so one function serves both the 23 BUs and Head
    Office. `days_submitted` is the denominator for an engagement-rate
    reading alongside the raw counts, if ever needed."""
    row = (
        db.query(
            func.coalesce(func.sum(case((model.event_flag == True, 1), else_=0)), 0),  # noqa: E712
            func.coalesce(func.sum(case((model.meeting_flag == True, 1), else_=0)), 0),  # noqa: E712
            func.coalesce(func.sum(case((model.golf_flag == True, 1), else_=0)), 0),  # noqa: E712
            func.count(model.id),
        )
        .filter(model.report_date >= month_start, model.report_date <= current_date, *extra_filters)
        .first()
    )
    event_count, meeting_count, golf_count, days_submitted = row
    return {
        "event_count": int(event_count),
        "meeting_count": int(meeting_count),
        "golf_count": int(golf_count),
        "days_submitted": int(days_submitted),
    }


def format_local_datetime(dt: Optional[datetime], fmt: str = "%d %b %Y, %I:%M %p") -> Optional[str]:
    """Formats a naive UTC timestamp (as stored via datetime.utcnow()) into the
    configured local timezone for display."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(APP_TZ).strftime(fmt)


def format_local_time(dt: Optional[datetime]) -> Optional[str]:
    return format_local_datetime(dt, "%I:%M %p")


def log_audit(
    db: Session,
    user: Optional[User],
    action: str,
    details: str = "",
    request: Optional[Request] = None,
    username: Optional[str] = None,
):
    """Writes one audit row and commits. `username` is recorded when there is
    no authenticated `user` (e.g. a failed login for a name that may not
    exist), so those events stay attributable and countable by the throttle.
    Anything a caller may have copied from the outside world is scrubbed here
    — control characters/newlines can't forge log lines, and length is capped
    so an unauthenticated request can't stuff the table."""
    # Imported here (not at module top) to keep this function self-contained.
    from src.client_ip import get_client_ip
    from src.throttle import MAX_USERNAME_LEN, strip_control_chars

    recorded_username = user.username if user else username
    if recorded_username is not None:
        recorded_username = strip_control_chars(recorded_username)[:MAX_USERNAME_LEN]

    text = strip_control_chars(details or "", " ")
    # Same-day resubmission diffs are stored as JSON (see
    # build_resubmit_audit_detail) and can legitimately exceed the general cap
    # when many fields change at once; cutting them mid-string would make the
    # "What Changed" view silently drop the whole entry, so they get more room.
    details_limit = 4000 if "resubmit_changes_json=" in text else 1000

    entry = AuditLog(
        user_id=user.id if user else None,
        username=recorded_username,
        action=action,
        details=text[:details_limit],
        ip_address=get_client_ip(request),
    )
    db.add(entry)
    db.commit()


# --- Legacy (pre-expansion) resubmit-change format — financial fields only,
# comma-joined "field:old->new" fragments. Kept solely to still render
# historical audit rows written before every field was tracked; new
# submissions always write the JSON format below instead, since free-text
# fields (meeting notes, key decisions) can themselves contain commas or
# "->" that would silently corrupt this string format's parsing. ---
_LEGACY_RESUBMIT_FIELD_LABELS = {
    "revenue": "Revenue",
    "cash_flow": "Cash Flow",
    "receivable": "Receivable",
    "payable": "Payable",
}


def _format_legacy_resubmit_value(raw: str) -> str:
    if raw == "None":
        return "—"
    try:
        return f"Ks {float(raw):,.2f}"
    except ValueError:
        return raw


def _parse_legacy_resubmit_changes(details: str) -> list[dict]:
    if "resubmit_changes=" not in details:
        return []
    raw = details.split("resubmit_changes=", 1)[1]
    if not raw or raw == "none":
        return []
    changes = []
    for fragment in raw.split(","):
        if ":" not in fragment or "->" not in fragment:
            continue
        field, rest = fragment.split(":", 1)
        old, new = rest.split("->", 1)
        changes.append(
            {
                "field": _LEGACY_RESUBMIT_FIELD_LABELS.get(field, field),
                "old": _format_legacy_resubmit_value(old),
                "new": _format_legacy_resubmit_value(new),
            }
        )
    return changes


# --- Current resubmit-change tracking — every field across all 4 report
# areas (Financial/Activity/Health/Social), not just the 4 financial
# numbers, so "What Changed" reflects the real edit rather than silently
# reading "no changes" whenever the financial figures happened to be
# resubmitted unchanged. ---
FIELD_LABELS = {
    "revenue": "Revenue",
    "cash_flow": "Cash Flow",
    "receivable": "Receivable",
    "payable": "Payable",
    "other_amount": "Other Amount",
    "financial_other_notes": "Financial Notes",
    "meetings_attended": "Meetings Attended",
    "site_visits": "Site Visits",
    "key_decisions": "Key Decisions",
    "next_day_schedule": "Next Day Schedule",
    "gym_percent": "Gym %",
    "swimming_percent": "Swimming %",
    "cycle_percent": "Cycle %",
    "activity_other_notes": "Activity Notes",
    "daily_steps": "Steps",
    "heart_rate": "Heart Rate",
    "sleep_hours": "Sleep Hours",
    "bp_systolic": "BP Systolic",
    "bp_diastolic": "BP Diastolic",
    "health_other_notes": "Health Notes",
    "event_flag": "Event",
    "meeting_flag": "Meeting",
    "golf_flag": "Golf",
    "social_other_notes": "Social Notes",
}
_MONEY_FIELDS = {"revenue", "cash_flow", "receivable", "payable", "other_amount"}
_PERCENT_FIELDS = {"gym_percent", "swimming_percent", "cycle_percent"}
_BOOL_FIELDS = {"event_flag", "meeting_flag", "golf_flag"}
_TEXT_FIELDS = {
    "financial_other_notes", "meetings_attended", "site_visits", "key_decisions",
    "next_day_schedule", "activity_other_notes", "health_other_notes", "social_other_notes",
}

# Personal data that must never be copied into audit_logs.details: health
# vitals and every free-text field. The audit row still records THAT the
# field changed (its label), but old/new are replaced by this marker. Money
# fields are deliberately NOT hidden — they are the tamper trail for
# financial edits.
HIDDEN_VALUE_MARKER = "(hidden)"
_HEALTH_VITAL_FIELDS = {"daily_steps", "heart_rate", "sleep_hours", "bp_systolic", "bp_diastolic"}
_HIDDEN_VALUE_FIELDS = _HEALTH_VITAL_FIELDS | _TEXT_FIELDS
_HIDDEN_VALUE_LABELS = {FIELD_LABELS[f] for f in _HIDDEN_VALUE_FIELDS}


def _format_field_value(field: str, value) -> str:
    """Formats one field's value for display in a change entry — by the
    field's own type (money / percent / Yes-No / free text preview / plain
    number), not a generic str(value), so e.g. a bare "None" never leaks
    into the UI and long free text doesn't blow out the row. Health vitals
    and free-text fields are never rendered (see HIDDEN_VALUE_MARKER)."""
    if field in _HIDDEN_VALUE_FIELDS:
        return HIDDEN_VALUE_MARKER
    if value is None:
        return "—"
    if field in _MONEY_FIELDS:
        try:
            return f"Ks {float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
    if field in _PERCENT_FIELDS:
        return f"{value}%"
    if field in _BOOL_FIELDS:
        return "Yes" if value else "No"
    return str(value)


def compute_field_changes(previous: dict, current: dict) -> list[dict]:
    """Diffs two {field: raw_value} snapshots — one captured immediately
    before a same-day resubmission overwrites the row, one from the
    just-submitted form — into display-ready {field, old, new} entries, only
    for fields that actually changed. `previous` and `current` share the
    same keys (both built from FIELD_LABELS' keys for one report area) so
    this is a straight dict walk, not a schema-aware diff. Comparison is on
    the raw values (Decimal/int/bool/str), before formatting, so e.g.
    Decimal("35678.00") vs Decimal("35678") correctly reads as unchanged."""
    changes = []
    for field, new_val in current.items():
        old_val = previous.get(field)
        if old_val != new_val:
            changes.append(
                {
                    "field": FIELD_LABELS.get(field, field),
                    "old": _format_field_value(field, old_val),
                    "new": _format_field_value(field, new_val),
                }
            )
    return changes


def build_resubmit_audit_detail(report_date: date, changes: Optional[list[dict]]) -> str:
    """Builds the DAILY_REPORT_SUBMITTED / HO_DAILY_REPORT_SUBMITTED audit
    `details` string. `changes=None` means this is the day's first
    submission (nothing existed yet to diff against — see submit_report in
    ceo.py/ho.py); an empty list means a same-day resubmission that changed
    nothing; a non-empty list is JSON-encoded (see compute_field_changes)
    rather than the legacy comma-joined format, so free-text field values
    can't corrupt the parsing."""
    detail = f"date={report_date.isoformat()}"
    if changes is not None:
        detail += " resubmit_changes_json=" + json.dumps(changes, ensure_ascii=False)
    return detail


def parse_resubmit_changes(details: str) -> list[dict]:
    """Reads back whatever build_resubmit_audit_detail wrote — the current
    JSON format for any submission made after every-field tracking shipped,
    or the legacy financial-only string format for older audit rows."""
    if "resubmit_changes_json=" in details:
        raw = details.split("resubmit_changes_json=", 1)[1]
        try:
            changes = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(changes, list):
            return []
        # Rows written before health vitals / free text were hidden at write
        # time still hold their plaintext values in the DB — redact on read
        # so old rows render as "(hidden)" too instead of exposing them.
        redacted = []
        for change in changes:
            if isinstance(change, dict) and change.get("field") in _HIDDEN_VALUE_LABELS:
                change = {**change, "old": HIDDEN_VALUE_MARKER, "new": HIDDEN_VALUE_MARKER}
            redacted.append(change)
        return redacted
    return _parse_legacy_resubmit_changes(details)


def todays_submission_events(db: Session, user_id: Optional[int], action: str, today: date) -> list[dict]:
    """Every report-submission audit entry for this user today, oldest first —
    used to show "Submitted N times today" (with each real timestamp, and
    what changed at each resubmission) on a CEO's/HO's own dashboard and the
    Chairman/HO's view of that report. Each resubmission already gets its own
    DAILY_REPORT_SUBMITTED / HO_DAILY_REPORT_SUBMITTED audit row with a
    resubmit_changes fragment (see submit_report in ceo.py/ho.py), so this is
    a read-only query over rows that already exist — no new tracking
    mechanism needed, just surfacing what's already logged. Matched by the
    literal `date=<today>` prefix log_audit's caller writes into `details`,
    same convention /api/admin/report-logs already parses."""
    if user_id is None:
        return []
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.user_id == user_id,
            AuditLog.action == action,
            AuditLog.details.like(f"date={today.isoformat()}%"),
        )
        .order_by(AuditLog.timestamp.asc())
        .all()
    )
    return [
        {
            "time_display": format_local_datetime(r.timestamp, "%I:%M:%S %p"),
            "timestamp": r.timestamp.isoformat(),
            "changes": parse_resubmit_changes(r.details or ""),
        }
        for r in rows
    ]


def build_ceo_activity_calendar(
    db: Session, business_unit_id: int, year: int, month: int, today: date, user_id: Optional[int]
) -> list:
    """Builds a Monday-first month grid for the CEO dashboard's calendar view.
    Each day carries every business area submitted that day (activity,
    financial, health, social) — not just the activity log — so clicking on
    any past day (e.g. "yesterday") surfaces the full day's data in one place
    instead of only the meetings/site-visits/decisions fields. Each day
    carries only JSON-safe primitive fields (never a raw ORM object or a
    date object).

    Financial rows belong to the business unit (a replacement CEO inherits
    them), but activity / health / social rows are the submitting CEO's own
    personal data, so those are additionally scoped to `user_id` — a newly
    appointed CEO of the same unit must not see their predecessor's history.

    `user_id=None` is the Chairman/HO view (admin CEO calendar): personal
    rows are NOT scoped to a submitter, so executives see the unit's days
    exactly as recorded. An ActivityLog is unique per (BU, date), so there
    is still at most one activity row per day; the day payload then names
    its submitter via `activity_submitted_by_username`."""
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar_module.monthrange(year, month)[1])

    def _in_range(model, personal: bool = False):
        query = db.query(model).filter(
            model.business_unit_id == business_unit_id, model.report_date >= first_day, model.report_date <= last_day
        )
        if personal and user_id is not None:
            query = query.filter(model.submitted_by == user_id)
        return query.all()

    activity_logs = _in_range(ActivityLog, personal=True)
    logs_by_date = {log.report_date: log for log in activity_logs}
    financial_by_date = {f.report_date: f for f in _in_range(DailyFinancial)}
    health_by_date = {h.report_date: h for h in _in_range(HealthStatus, personal=True)}
    social_by_date = {s.report_date: s for s in _in_range(SocialLog, personal=True)}

    submitter_usernames = {}
    submitter_ids = {log.submitted_by for log in activity_logs if log.submitted_by is not None}
    if submitter_ids:
        submitter_usernames = {
            u.id: u.username for u in db.query(User).filter(User.id.in_(submitter_ids)).all()
        }

    weeks = []
    for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month):
        week_cells = []
        for d in week:
            log = logs_by_date.get(d)
            fin = financial_by_date.get(d)
            health = health_by_date.get(d)
            social = social_by_date.get(d)
            week_cells.append(
                {
                    "date_iso": d.isoformat(),
                    "date_display": d.strftime("%A, %d %B %Y"),
                    "day_number": d.day,
                    "in_month": d.month == month,
                    "is_weekend": is_weekend(d),
                    "is_today": d == today,
                    "is_future": d > today,
                    "has_report": log is not None,
                    "meetings_attended": log.meetings_attended if log else None,
                    "site_visits": log.site_visits if log else None,
                    "key_decisions": log.key_decisions if log else None,
                    "next_day_schedule": log.next_day_schedule if log else None,
                    "document_url": log.document_url if log else None,
                    "gym_percent": log.gym_percent if log else None,
                    "swimming_percent": log.swimming_percent if log else None,
                    "cycle_percent": log.cycle_percent if log else None,
                    "activity_other_notes": log.other_notes if log else None,
                    "activity_submitted_by": log.submitted_by if log else None,
                    "activity_submitted_by_username": (
                        submitter_usernames.get(log.submitted_by) if log else None
                    ),
                    "revenue": float(fin.revenue) if fin and fin.revenue is not None else None,
                    "cash_flow": float(fin.cash_flow) if fin and fin.cash_flow is not None else None,
                    "receivable": float(fin.receivable) if fin and fin.receivable is not None else None,
                    "payable": float(fin.payable) if fin and fin.payable is not None else None,
                    "financial_status": (
                        evaluate_financial_health(fin.revenue, fin.cash_flow, fin.receivable, fin.payable)[0].lower()
                        if fin and fin.cash_flow is not None
                        else None
                    ),
                    "physical_health": health.physical_health if health else None,
                    "stress_level": health.stress_level if health else None,
                    "energy_mood": health.energy_mood if health else None,
                    "sleep_quality": health.sleep_quality if health else None,
                    "daily_steps": health.daily_steps if health else None,
                    "heart_rate": health.heart_rate if health else None,
                    "sleep_hours": float(health.sleep_hours) if health and health.sleep_hours is not None else None,
                    "bp_systolic": health.bp_systolic if health else None,
                    "bp_diastolic": health.bp_diastolic if health else None,
                    "event_flag": social.event_flag if social else None,
                    "meeting_flag": social.meeting_flag if social else None,
                    "golf_flag": social.golf_flag if social else None,
                }
            )
        weeks.append(week_cells)
    return weeks


def _target_field(monthly_target, field: str) -> float:
    """monthly_target may be a MonthlyTarget ORM row or a plain dict (e.g. in
    tests) — this reads the same field either way."""
    raw = monthly_target.get(field) if isinstance(monthly_target, dict) else getattr(monthly_target, field)
    return float(raw or 0)


def evaluate_cumulative_financial_health(
    cumulative_net_revenue: float,
    monthly_target,
    evaluation_date: date,
) -> Optional[dict]:
    """Single-step Cumulative Financial Health evaluation — the one benchmark
    the Financial quadrant is judged on everywhere in this app (Executive
    Dashboard, Operations, Comparison, Business Unit Detail, CEO Dashboard).
    Replaces the old per-day multi-metric check (cash flow / receivable /
    payable breaches): System Health is now decided solely on how Cumulative
    Net Revenue (Sum of Revenue + Other Amount from day 1 of the month
    through evaluation_date) is tracking against the CEO's Monthly Revenue
    Target.

    Expected MTD Target is the monthly target pro-rated by day-of-month
    (monthly_target * day_number / days_in_month) — this one formula handles
    both cases the spec calls out separately: for a mid-month day it's a
    fair slice of the full target, and on the last day of the month
    day_number == days_in_month so it automatically equals the full monthly
    target (month-end finalization), with no separate branch needed.

    Achievement % is measured against that pro-rated Expected MTD Target (so
    a business exactly on pace reads 100% on any day of the month, not just
    at month-end). Variance Amount, shown to the user as an absolute Kyat
    gap, is measured against the FULL monthly target instead — "how far from
    this month's whole goal are we right now" — so it's normal/expected for
    Variance to run negative until late in the month even while Achievement
    reads GREEN.

    Rules:
      🔴 RED    — Achievement < 50% of Expected MTD Target (critical underperformance)
      🟡 YELLOW — Achievement 50-79.9% of Expected MTD Target
      🟢 GREEN  — Achievement >= 80% of Expected MTD Target

    @return: None if no monthly target is set (or its revenue target is 0),
        otherwise {
            status: "RED"|"YELLOW"|"GREEN", color_code: "#RRGGBB",
            achievement_pct: float — cumulative_net_revenue vs Expected MTD Target,
            cumulative_net_revenue, expected_mtd_target, full_monthly_target: float,
            variance_amount: float — cumulative_net_revenue minus the FULL monthly target,
            day_number, days_in_month: int,
            summary_notes: list[str] (Burmese),
        }
    """
    monthly_revenue_target = _target_field(monthly_target, "monthly_revenue_target")
    if monthly_revenue_target <= 0:
        return None

    days_in_month = calendar_module.monthrange(evaluation_date.year, evaluation_date.month)[1]
    day_number = evaluation_date.day
    expected_mtd_target = monthly_revenue_target * (day_number / days_in_month)

    achievement_pct = round(cumulative_net_revenue / expected_mtd_target * 100, 1) if expected_mtd_target > 0 else None
    variance_amount = round(cumulative_net_revenue - monthly_revenue_target, 2)

    summary_notes = []
    if achievement_pct is None or achievement_pct >= 80:
        status, color_code = "GREEN", "#10B981"
    elif achievement_pct >= 50:
        status, color_code = "YELLOW", "#F59E0B"
        summary_notes.append(f"လစဉ်ဆက်စပ်ဝင်ငွေ (MTD)     Target၏ {achievement_pct:.0f}% သာ ရရှိပါသေးသည် (သတိပေးအဆင့်)")
    else:
        status, color_code = "RED", "#EF4444"
        summary_notes.append(f"လစဉ်ဆက်စပ်ဝင်ငွေ (MTD) Target၏ {achievement_pct:.0f}% သာရရှိပြီး 50% အောက်နည်းပါးနေပါသည်")

    return {
        "status": status,
        "color_code": color_code,
        "achievement_pct": achievement_pct,
        "cumulative_net_revenue": round(cumulative_net_revenue, 2),
        "expected_mtd_target": round(expected_mtd_target, 2),
        "full_monthly_target": round(monthly_revenue_target, 2),
        "variance_amount": variance_amount,
        "day_number": day_number,
        "days_in_month": days_in_month,
        "summary_notes": summary_notes,
    }


def compute_mtd_cumulative_by_bu(
    db: Session, business_unit_ids: list, target_month: date, current_date: date
) -> dict:
    """Bulk PostgreSQL aggregation of MTD Revenue, MTD Other Amount, and their
    sum (Cumulative Net Revenue) — day 1 of target_month through current_date
    inclusive — for every business unit in one grouped query. Used by the
    admin overview so all 23 BUs' MTD totals cost one round trip instead of 23.

    Equivalent to the following SQL view/query, for reference:

        SELECT business_unit_id,
               COALESCE(SUM(revenue), 0) AS mtd_revenue,
               COALESCE(SUM(other_amount), 0) AS mtd_other_amount,
               COALESCE(SUM(revenue), 0) + COALESCE(SUM(other_amount), 0) AS cumulative_net_revenue
        FROM daily_financials
        WHERE report_date >= :target_month
          AND report_date <= :current_date
        GROUP BY business_unit_id;

    @return: {business_unit_id: {"revenue": float, "other_amount": float, "total": float}},
        only for BUs with at least one row in range.
    """
    rows = (
        db.query(
            DailyFinancial.business_unit_id,
            func.coalesce(func.sum(DailyFinancial.revenue), 0),
            func.coalesce(func.sum(DailyFinancial.other_amount), 0),
        )
        .filter(
            DailyFinancial.business_unit_id.in_(business_unit_ids),
            DailyFinancial.report_date >= target_month,
            DailyFinancial.report_date <= current_date,
        )
        .group_by(DailyFinancial.business_unit_id)
        .all()
    )
    return {
        bu_id: {"revenue": float(revenue), "other_amount": float(other), "total": float(revenue) + float(other)}
        for bu_id, revenue, other in rows
    }


def evaluate_cumulative_health(db: Session, business_unit_id: int, target_month: date, current_date: date) -> Optional[dict]:
    """Single-BU convenience wrapper around evaluate_cumulative_financial_health
    — sums this BU's Cumulative Net Revenue for the month itself (one query)
    rather than requiring the caller to have already bulk-fetched it, for the
    call sites (CEO's own dashboard, Business Unit Detail) that only ever
    need one business unit at a time. gather_admin_overview, which evaluates
    all 23 BUs together, uses compute_mtd_cumulative_by_bu instead to avoid
    23 separate round trips.

    Adapted from the requested evaluate_cumulative_health(biz_id, target_month,
    current_date) signature: `db` is threaded through explicitly as the first
    argument, matching how every other DB-aware helper in this module takes
    its Session rather than opening one internally.
    """
    monthly_target = (
        db.query(MonthlyTarget)
        .filter(MonthlyTarget.business_unit_id == business_unit_id, MonthlyTarget.target_month == target_month)
        .first()
    )
    if not monthly_target:
        return None
    cumulative_by_bu = compute_mtd_cumulative_by_bu(db, [business_unit_id], target_month, current_date)
    cumulative_net_revenue = cumulative_by_bu.get(business_unit_id, {}).get("total", 0.0)
    result = evaluate_cumulative_financial_health(cumulative_net_revenue, monthly_target, current_date)
    if result:
        result["mtd_revenue"] = cumulative_by_bu.get(business_unit_id, {}).get("revenue", 0.0)
        result["mtd_other_amount"] = cumulative_by_bu.get(business_unit_id, {}).get("other_amount", 0.0)
    return result


def evaluate_ho_cumulative_health(db: Session, monthly_target, target_month: date, current_date: date) -> Optional[dict]:
    """HO-equivalent of evaluate_cumulative_health — sums HeadOfficeDailyFinancial's
    Cumulative Net Revenue for the month (a single entity, not per-business-unit,
    so no compute_mtd_cumulative_by_bu-style grouping is needed) and evaluates it
    against monthly_target with the same evaluate_cumulative_financial_health
    rules a business unit is judged by. Takes the target directly (rather than
    querying for it) since call sites already have it in hand either way."""
    if not monthly_target:
        return None
    row = (
        db.query(
            func.coalesce(func.sum(HeadOfficeDailyFinancial.revenue), 0),
            func.coalesce(func.sum(HeadOfficeDailyFinancial.other_amount), 0),
        )
        .filter(
            HeadOfficeDailyFinancial.report_date >= target_month,
            HeadOfficeDailyFinancial.report_date <= current_date,
        )
        .first()
    )
    mtd_revenue = float(row[0] or 0)
    mtd_other_amount = float(row[1] or 0)
    cumulative_net_revenue = mtd_revenue + mtd_other_amount
    result = evaluate_cumulative_financial_health(cumulative_net_revenue, monthly_target, current_date)
    if result:
        result["mtd_revenue"] = mtd_revenue
        result["mtd_other_amount"] = mtd_other_amount
    return result


def compute_bu_quadrant_summary(
    latest_financial: Optional[DailyFinancial],
    today_financial: Optional[DailyFinancial],
    latest_activity: Optional[ActivityLog],
    latest_social: Optional[SocialLog],
    latest_health: Optional[HealthStatus],
    budget_status: Optional[str] = None,
) -> dict:
    """Computes all 4 quadrant statuses + their human-readable reasons for one
    business unit. Single source of truth shared by the Chairman/HO's Business
    Unit Detail page and the CEO's own dashboard, so a CEO viewing their own
    unit always sees exactly the same status/reason an executive would see for
    that same unit — no separate copy of this logic to drift out of sync.

    @param budget_status: the actual-vs-CEO-target verdict ("green"/"yellow"/
        "red"/"grey"/None) for the same day, if a monthly target is set.
        "green" — the CEO is hitting their own target — overrides the
        Financial quadrant to green, instead of showing a conflicting
        Warning/Critical from the generic cash-flow health rules below;
        hitting the target you were actually judged against is the more
        meaningful signal. Symmetrically, "red" — badly missing the target
        (e.g. 0.1% of daily revenue target) — overrides the quadrant to red
        even if cash flow alone still looks fine on paper, since a target
        miss that severe should never be hidden behind a milder cash-flow
        reading. Yellow/grey budget verdicts don't override anything.
    """
    financial_status = resolve_financial_status(latest_financial, today_financial, budget_status)
    social_status = social_quadrant_status(latest_social)
    activity_status = activity_quadrant_status(latest_activity)
    health_status_raw = compute_health_risk(latest_health)
    health_status = "grey" if health_status_raw == "unknown" else health_status_raw

    if today_financial is None:
        financial_reason = "Missing today's mandatory financial submission"
    elif latest_financial is None or latest_financial.cash_flow is None:
        financial_reason = "No financial data submitted yet"
    elif budget_status == "green":
        financial_reason = "Meeting this month's CEO-set target"
    elif budget_status == "red":
        financial_reason = "Badly missing this month's CEO-set target"
    else:
        _, _, financial_flags = evaluate_financial_health(
            latest_financial.revenue, latest_financial.cash_flow, latest_financial.receivable, latest_financial.payable
        )
        financial_reason = "; ".join(financial_flags) if financial_flags else "Healthy cash position on the latest submission"

    activity_reasons = {
        "red": "Low average fitness engagement (Gym/Swim/Cycle)",
        "yellow": "Moderate average fitness engagement (Gym/Swim/Cycle)",
        "green": "Strong average fitness engagement (Gym/Swim/Cycle)",
        "grey": "No activity percentages submitted yet",
    }
    activity_reason = activity_reasons[activity_status]

    social_reasons = {
        "green": "Engaged in at least one of Event / Meeting / Golf",
        "yellow": "Submitted today but no social engagement recorded",
        "grey": "No social data submitted yet",
    }
    social_reason = social_reasons.get(social_status, social_reasons["grey"])

    step_note = f" · {latest_health.daily_steps:,} steps" if latest_health and latest_health.daily_steps is not None else ""
    if health_status == "red":
        notes = health_risk_notes(latest_health)
        health_reason = "Critical: " + (", ".join(notes) if notes else "elevated risk factors")
    elif health_status == "yellow":
        notes = health_risk_notes(latest_health)
        health_reason = "Fatigue watch: " + (", ".join(notes) if notes else "elevated risk factors")
    elif health_status == "green":
        health_reason = "Healthy and within normal thresholds" + step_note
    else:
        health_reason = "No health data submitted yet"

    return {
        "financial_status": financial_status,
        "financial_reason": financial_reason,
        "social_status": social_status,
        "social_reason": social_reason,
        "activity_status": activity_status,
        "activity_reason": activity_reason,
        "health_status": health_status,
        "health_reason": health_reason,
    }


def compute_ho_quadrant_summary(
    latest_financial: Optional[DailyFinancial],
    today_financial: Optional[DailyFinancial],
    latest_activity: Optional[ActivityLog],
    latest_social: Optional[SocialLog],
    latest_health: Optional[HealthStatus],
    budget_status: Optional[str] = None,
) -> dict:
    """Counterpart of compute_bu_quadrant_summary for HO Staff's own daily
    report — same monthly-target override behavior a business unit gets,
    just backed by HeadOfficeMonthlyTarget instead of MonthlyTarget. Shared
    by the HO submission dashboard and the Chairman/HO-facing Head Office
    admin page so both always agree on the same day's status.

    @param budget_status: the Cumulative Net Revenue vs HO's monthly target
        verdict ("green"/"yellow"/"red"/"grey"/None), same override rules as
        compute_bu_quadrant_summary — see that docstring for the reasoning.
    """
    financial_status = resolve_financial_status(latest_financial, today_financial, budget_status)
    social_status = social_quadrant_status(latest_social)
    activity_status = activity_quadrant_status(latest_activity)
    health_status_raw = compute_health_risk(latest_health)
    health_status = "grey" if health_status_raw == "unknown" else health_status_raw

    activity_reasons = {
        "red": "Low average fitness engagement (Gym/Swim/Cycle)",
        "yellow": "Moderate average fitness engagement (Gym/Swim/Cycle)",
        "green": "Strong average fitness engagement (Gym/Swim/Cycle)",
        "grey": "No activity percentages submitted yet",
    }
    social_reasons = {
        "green": "Engaged in at least one of Event / Meeting / Golf",
        "yellow": "Submitted today but no social engagement recorded",
        "grey": "No social data submitted yet",
    }

    step_note = f" · {latest_health.daily_steps:,} steps" if latest_health and latest_health.daily_steps is not None else ""
    if health_status == "red":
        notes = health_risk_notes(latest_health)
        health_reason = "Critical: " + (", ".join(notes) if notes else "elevated risk factors")
    elif health_status == "yellow":
        notes = health_risk_notes(latest_health)
        health_reason = "Fatigue watch: " + (", ".join(notes) if notes else "elevated risk factors")
    elif health_status == "green":
        health_reason = "Healthy and within normal thresholds" + step_note
    else:
        health_reason = "No health data submitted yet"

    if today_financial is None:
        financial_reason = "Missing today's mandatory financial submission"
    elif latest_financial is None or latest_financial.cash_flow is None:
        financial_reason = "No financial data submitted yet"
    elif budget_status == "green":
        financial_reason = "Meeting this month's target"
    elif budget_status == "red":
        financial_reason = "Badly missing this month's target"
    else:
        _, _, financial_flags = evaluate_financial_health(
            latest_financial.revenue, latest_financial.cash_flow, latest_financial.receivable, latest_financial.payable
        )
        financial_reason = "; ".join(financial_flags) if financial_flags else "Healthy cash position on the latest submission"

    return {
        "financial_status": financial_status,
        "financial_reason": financial_reason,
        "social_status": social_status,
        "social_reason": social_reasons.get(social_status, social_reasons["grey"]),
        "activity_status": activity_status,
        "activity_reason": activity_reasons[activity_status],
        "health_status": health_status,
        "health_reason": health_reason,
    }


def build_financial_rows(financial_history: list, targets_by_month: dict) -> list:
    """Builds Financial History / Day-by-Day Review rows for one BU. Shared by
    the Chairman/HO's Business Unit Detail page and the CEO's own dashboard so
    both ever show the exact same per-day figures and status for that unit.

    Each row's "performance" is the Cumulative Net Revenue evaluation as of
    THAT row's own date — not just that single day's revenue — so stepping
    back through the review shows how the month-to-date picture actually
    built up day by day. financial_history arrives newest-first (every
    caller queries it that way for display), so cumulative sums are computed
    from a temporary oldest-first pass and then re-attached to the rows in
    the original order.

    Each row's "status" is promoted to green when that same day's cumulative
    "performance" is GREEN, and demoted to red when it's RED — mirroring the
    override compute_bu_quadrant_summary applies to the top-level Financial
    Status card — so this table never disagrees with that card about the
    same day."""
    performance_by_date = {}
    cumulative_by_month: dict = {}
    for f in sorted(financial_history, key=lambda row: row.report_date):
        month_key = date(f.report_date.year, f.report_date.month, 1)
        running_total = cumulative_by_month.get(month_key, 0.0) + float(f.revenue or 0) + float(f.other_amount or 0)
        cumulative_by_month[month_key] = running_total
        target = targets_by_month.get(month_key)
        performance_by_date[f.report_date] = (
            evaluate_cumulative_financial_health(running_total, target, f.report_date) if target else None
        )

    rows = []
    for f in financial_history:
        performance = performance_by_date[f.report_date]
        if f.cash_flow is None:
            status = "grey"
        elif performance and performance["status"] == "GREEN":
            status = "green"
        elif performance and performance["status"] == "RED":
            status = "red"
        else:
            status = evaluate_financial_health(f.revenue, f.cash_flow, f.receivable, f.payable)[0].lower()
        rows.append(
            {
                "report_date": f.report_date.strftime("%d %b %Y"),
                "submitted_at": format_local_datetime(f.updated_at),
                "revenue": float(f.revenue),
                "expenses": float(f.expenses),
                "net_profit": float(f.net_profit),
                "cash_in_hand": float(f.cash_in_hand),
                "cash_flow": float(f.cash_flow) if f.cash_flow is not None else None,
                "receivable": float(f.receivable) if f.receivable is not None else None,
                "payable": float(f.payable) if f.payable is not None else None,
                "other_amount": float(f.other_amount) if f.other_amount is not None else None,
                "status": status,
                "performance": performance,
            }
        )
    return rows


def build_activity_rows(activity_history: list) -> list:
    """Builds Activity History / Day-by-Day Review rows for one BU. Shared by
    the Business Unit Detail page and the CEO's own dashboard."""
    return [
        {
            "report_date": a.report_date.strftime("%d %b %Y"),
            "submitted_at": format_local_datetime(a.updated_at),
            "meetings_attended": a.meetings_attended,
            "site_visits": a.site_visits,
            "key_decisions": a.key_decisions,
            "next_day_schedule": a.next_day_schedule,
            "document_url": a.document_url,
            "gym_percent": a.gym_percent,
            "swimming_percent": a.swimming_percent,
            "cycle_percent": a.cycle_percent,
            "other_notes": a.other_notes,
        }
        for a in activity_history
    ]


def build_health_rows(health_history: list) -> list:
    """Builds Health History / Day-by-Day Review rows for one BU. Shared by
    the Business Unit Detail page and the CEO's own dashboard."""
    return [
        {
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
            "other_notes": h.other_notes,
        }
        for h in health_history
    ]


def build_social_rows(social_history: list) -> list:
    """Builds Social History / Day-by-Day Review rows for one BU. Shared by
    the Business Unit Detail page and the CEO's own dashboard."""
    return [
        {
            "report_date": s.report_date.strftime("%d %b %Y"),
            "submitted_at": format_local_datetime(s.updated_at),
            "event_flag": s.event_flag,
            "meeting_flag": s.meeting_flag,
            "golf_flag": s.golf_flag,
            "other_notes": s.other_notes,
        }
        for s in social_history
    ]


# --- Input validation shared by /api/ceo and /api/ho submit-report / profile /
# monthly-target. Every check here returns a clean HTTP 400 with a readable
# message rather than letting bad input reach the DB (where it would either
# poison SUM() aggregates — NaN/Infinity — overflow a Numeric column, or trip
# a CHECK constraint and surface as a 500). Ranges deliberately mirror the
# database CHECK constraints, so these API checks are never looser than them.
MONEY_ABS_LIMIT = Decimal(10) ** 13  # money columns are Numeric(16, 2); DB CHECK is abs(x) < 1e13
MAX_NARRATIVE_TEXT_LEN = 4000  # activity narrative + every *_other_notes field
MAX_SHORT_TEXT_LEN = 2000  # address and similar single-purpose text
_MAX_NUMERIC_INPUT_LEN = 40  # no legitimate figure needs more characters than this

# (label, min, max) — inclusive ranges for the optional health / fitness fields.
STEPS_RANGE = ("Step count", 0, 200000)
HEART_RATE_RANGE = ("Heart rate", 20, 300)
SLEEP_HOURS_RANGE = ("Sleep hours", 0, 24)
BP_SYSTOLIC_RANGE = ("Systolic blood pressure", 40, 300)
BP_DIASTOLIC_RANGE = ("Diastolic blood pressure", 20, 200)
PERCENT_MIN, PERCENT_MAX = 0, 100


def parse_bounded_decimal(raw: Optional[str], places: int = 2, limit: Decimal = MONEY_ABS_LIMIT) -> Optional[Decimal]:
    """Blank -> None. Anything that isn't a finite number with abs(value) <
    `limit` -> None as well (NaN / Infinity / sNaN / 1e20 / garbage / absurdly
    long strings). Otherwise the value rounded half-up to `places` decimals —
    the precision the column stores — so the in-memory value always equals
    what the DB will hold (keeps same-day resubmission diffs honest)."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "" or len(raw) > _MAX_NUMERIC_INPUT_LEN:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite() or value.copy_abs() >= limit:
        return None
    try:
        value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    if value.copy_abs() >= limit:  # e.g. 9999999999999.996 rounding up to the limit
        return None
    if value == 0:
        value = value.copy_abs()  # never store/display "-0.00"
    return value


def _is_blank(raw: Optional[str]) -> bool:
    return raw is None or raw.strip() == ""


def form_money(raw: Optional[str], label: str) -> Optional[Decimal]:
    """Optional money field from a form: blank -> None, invalid -> HTTP 400."""
    if _is_blank(raw):
        return None
    value = parse_bounded_decimal(raw)
    if value is None:
        raise HTTPException(
            status_code=400,
            detail=f"{label} must be a valid number (absolute value below {int(MONEY_ABS_LIMIT):,})",
        )
    return value


def form_int(raw: Optional[str], label: str, lo: int, hi: int) -> Optional[int]:
    """Optional whole-number field: blank -> None; not a whole number or
    outside [lo, hi] -> HTTP 400."""
    if _is_blank(raw):
        return None
    text = raw.strip()
    try:
        if len(text) > _MAX_NUMERIC_INPUT_LEN:
            raise ValueError
        value = int(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} must be a whole number")
    if not (lo <= value <= hi):
        raise HTTPException(status_code=400, detail=f"{label} must be between {lo:,} and {hi:,}")
    return value


def form_decimal(raw: Optional[str], label: str, lo: int, hi: int, places: int = 1) -> Optional[Decimal]:
    """Optional decimal field with a range (e.g. sleep hours): blank -> None,
    invalid or outside [lo, hi] -> HTTP 400."""
    if _is_blank(raw):
        return None
    value = parse_bounded_decimal(raw, places=places, limit=Decimal(hi) + 1)
    if value is None or not (lo <= value <= hi):
        raise HTTPException(status_code=400, detail=f"{label} must be a number between {lo} and {hi}")
    return value


def form_percent(raw: Optional[str], label: str) -> Optional[int]:
    return form_int(raw, label, PERCENT_MIN, PERCENT_MAX)


def validate_blood_pressure(systolic: Optional[int], diastolic: Optional[int]) -> None:
    """Both readings are optional, but when both are given the diastolic
    (lower) figure must be below the systolic one."""
    if systolic is not None and diastolic is not None and diastolic >= systolic:
        raise HTTPException(
            status_code=400,
            detail="Diastolic blood pressure must be lower than systolic blood pressure",
        )


def form_text(raw: Optional[str], label: str, max_len: int) -> str:
    """Free-text field: returns the stripped value; too long (never silently
    truncated), containing NUL characters, or not encodable as UTF-8 (both of
    which Postgres would reject with a 500) -> HTTP 400."""
    value = (raw or "").strip()
    if len(value) > max_len:
        raise HTTPException(status_code=400, detail=f"{label} is too long (max {max_len:,} characters)")
    if "\x00" in value:
        raise HTTPException(status_code=400, detail=f"{label} contains invalid characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(status_code=400, detail=f"{label} contains invalid characters")
    return value


def _target_amount(value, label: str, allow_negative: bool) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        amount = Decimal("NaN")
    if not amount.is_finite() or amount.copy_abs() >= MONEY_ABS_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"{label} must be a valid number (absolute value below {int(MONEY_ABS_LIMIT):,})",
        )
    if amount < 0 and not allow_negative:
        raise HTTPException(status_code=400, detail=f"{label} cannot be negative")
    try:
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise HTTPException(status_code=400, detail=f"{label} must be a valid number")
    return amount.copy_abs() if amount == 0 else amount


def parse_monthly_target_amounts(payload) -> dict:
    """Validates a MonthlyTargetRequest's four amounts (pydantic's float
    accepts Infinity and huge values; only NaN and negatives are rejected by
    its ge=0) and returns them as Decimals keyed by MonthlyTarget /
    HeadOfficeMonthlyTarget column name. Revenue / max receivable / max
    payable must be >= 0; the cash-flow target may legitimately be negative.
    Raises HTTP 400 — call BEFORE touching the database."""
    return {
        "monthly_revenue_target": _target_amount(payload.monthly_revenue_target, "Monthly revenue target", False),
        "monthly_cash_flow_target": _target_amount(payload.monthly_cash_flow_target, "Monthly cash flow target", True),
        "monthly_max_receivable": _target_amount(payload.monthly_max_receivable, "Monthly max receivable", False),
        "monthly_max_payable": _target_amount(payload.monthly_max_payable, "Monthly max payable", False),
    }
