from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from src.crypto import EncryptedDate, EncryptedText
from src.database import Base

def _utcnow() -> datetime:
    """Naive UTC now — identical to src.utils.utcnow(), defined here
    because models.py cannot import src.utils (utils imports models).
    Replaces the deprecated _utcnow() as a column default /
    onupdate callable; storage semantics are unchanged."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today_local() -> date:
    """Yangon calendar date (SM-12): date columns default to the app's
    local day, not the server (UTC) day — between 00:00 and 06:30 Yangon
    time the two differ, so a server-date default can stamp a row a day
    early. Same clock as src.utils.today_local()."""
    from zoneinfo import ZoneInfo

    from src.config import settings

    return datetime.now(ZoneInfo(settings.APP_TIMEZONE)).date()


# Database-level integrity rules. The API validates all of this too
# (schemas.py and the submit-report routes); these are the last line of
# defence and are created by migration b1c5d9e3a7f2 (Postgres). They live here
# as well so the models remain the single description of the schema.
_MONEY_LIMIT = 10**13


def _pg_check(sqltext, name):
    """A CHECK constraint that is only emitted on Postgres. The rules use
    Postgres syntax (extract(), btrim()) and SQLite — local development only —
    can't alter constraints after the fact anyway, so it relies on the API's
    validation; production integrity is Postgres."""
    return CheckConstraint(sqltext, name=name).ddl_if(dialect="postgresql")


def _money_range(*columns):
    return " AND ".join(f"{c} BETWEEN -{_MONEY_LIMIT} AND {_MONEY_LIMIT}" for c in columns)


_TARGETS_RANGE = (
    f"monthly_revenue_target BETWEEN 0 AND {_MONEY_LIMIT} "
    f"AND monthly_cash_flow_target BETWEEN -{_MONEY_LIMIT} AND {_MONEY_LIMIT} "
    f"AND monthly_max_receivable BETWEEN 0 AND {_MONEY_LIMIT} "
    f"AND monthly_max_payable BETWEEN 0 AND {_MONEY_LIMIT}"
)
_VITALS_RANGE = (
    "daily_steps BETWEEN 0 AND 200000 AND heart_rate BETWEEN 20 AND 300 AND sleep_hours BETWEEN 0 AND 24 "
    "AND bp_systolic BETWEEN 40 AND 300 AND bp_diastolic BETWEEN 20 AND 200 "
    "AND (bp_systolic IS NULL OR bp_diastolic IS NULL OR bp_diastolic < bp_systolic)"
)
_PERCENT_RANGE = "gym_percent BETWEEN 0 AND 100 AND swimming_percent BETWEEN 0 AND 100 AND cycle_percent BETWEEN 0 AND 100"


class BusinessUnit(Base):
    __tablename__ = "business_units"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    users = relationship("User", back_populates="business_unit")
    financials = relationship("DailyFinancial", back_populates="business_unit")
    activity_logs = relationship("ActivityLog", back_populates="business_unit")
    health_statuses = relationship("HealthStatus", back_populates="business_unit")
    social_logs = relationship("SocialLog", back_populates="business_unit")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # At most one ACTIVE CEO per business unit — every dashboard lookup
        # assumes it. (Partial unique index: works on Postgres and SQLite.)
        Index(
            "uq_users_one_active_ceo_per_bu",
            "business_unit_id",
            unique=True,
            postgresql_where=text("role = 'CEO' AND is_active"),
            sqlite_where=text("role = 'CEO' AND is_active = 1"),
        ),
        _pg_check("role IN ('Chairman', 'HO', 'CEO')", name="ck_users_role"),
        # BU assignment: a CEO always belongs to exactly one BU;
        # Chairman/HO are group-level and never carry a BU. (The
        # Accountant role was removed with the ERP deletion — see the
        # 2026-11-17 drop migration; no role other than CEO may hold a BU.)
        _pg_check(
            "(role = 'CEO' AND business_unit_id IS NOT NULL) OR "
            "(role IN ('Chairman', 'HO') AND business_unit_id IS NULL)",
            name="ck_users_bu_assignment",
        ),
        _pg_check("username = lower(username)", name="ck_users_username_lower"),
    )

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(150), nullable=True)
    role = Column(String(20), nullable=False)  # Chairman | HO | CEO
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=True, index=True)  # m-12: per-BU lookups
    require_password_change = Column(Boolean, default=False, nullable=False)
    # Only meaningful for role == "CEO" — Chairman/HO have no profile to
    # complete, so this stays True (irrelevant) for them. Checked on every
    # authenticated request (same pattern as require_password_change) so a
    # CEO can't bypass the mandatory profile-setup gate by calling API
    # routes directly instead of going through the frontend redirect.
    is_profile_completed = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    # Bumped on logout and on every password change (self-service or
    # admin-issued). Embedded in each issued JWT so a token minted before the
    # bump reads as stale and is rejected, even though it hasn't expired yet
    # — the only revocation mechanism available for these otherwise-stateless
    # tokens.
    token_version = Column(Integer, default=0, nullable=False)
    # M-01 (TOTP second factor): the per-user TOTP secret, encrypted at
    # rest with the same field encryption as the profile columns
    # (src/crypto.py). totp_enabled is False until the owner proves
    # possession by confirming a code; the Chairman may not log in at all
    # without an enabled TOTP (see routers/auth.py login()).
    totp_secret = Column(EncryptedText, nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    totp_enrolled_at = Column(DateTime, nullable=True)
    # Set only while the account's password is a Chairman-issued temporary
    # one; login treats an expired temporary password like a wrong one, so an
    # unclaimed credential can't stay live indefinitely. Cleared as soon as
    # the owner sets their own password.
    temp_password_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    business_unit = relationship("BusinessUnit", back_populates="users")


class DailyFinancial(Base):
    __tablename__ = "daily_financials"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_financial_bu_date"),
        _pg_check(
            _money_range("revenue", "cash_flow", "receivable", "payable", "other_amount"),
            name="ck_daily_financials_amount_range",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, default=_today_local, index=True)
    revenue = Column(Numeric(16, 2), nullable=False, default=0)
    expenses = Column(Numeric(16, 2), nullable=False, default=0)
    net_profit = Column(Numeric(16, 2), nullable=False, default=0)
    cash_in_hand = Column(Numeric(16, 2), nullable=False, default=0)
    cash_flow = Column(Numeric(16, 2), nullable=True)
    receivable = Column(Numeric(16, 2), nullable=True)
    payable = Column(Numeric(16, 2), nullable=True)
    # Any additional day's income outside the main Revenue figure (e.g. a
    # one-off sale, refund, or side income) — folded into Cumulative Net
    # Revenue for the MTD-vs-target evaluation, but tracked separately from
    # Revenue so the two can still be reported on individually.
    other_amount = Column(Numeric(16, 2), nullable=True)
    # Free-text catch-all for anything not covered by the fields above —
    # mirrors ActivityLog/HealthStatus/SocialLog's own other_notes column.
    # D-14(b): anomaly flag — set at CEO daily submit when the day's revenue
    # deviates >50% from the BU's trailing 14-day average, for HO review.
    # Stored (not computed at read time) so the flag survives resubmission
    # edits and is visible to every review view that reads the row.
    revenue_anomaly = Column(Boolean, default=False, nullable=False)
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit", back_populates="financials")


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_activity_bu_date"),
        _pg_check(_PERCENT_RANGE, name="ck_activity_logs_percent_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, default=_today_local, index=True)
    meetings_attended = Column(Text, nullable=True)
    site_visits = Column(Text, nullable=True)
    key_decisions = Column(Text, nullable=True)
    next_day_schedule = Column(Text, nullable=True)
    # Blockers the CEO hit today, and support they are asking HO/Chairman
    # for — a non-empty help_needed is the escalation flag the executive
    # Daily Activities page surfaces in its "Needs Attention" section.
    problems_today = Column(Text, nullable=True)
    help_needed = Column(Text, nullable=True)
    # Help-request resolution loop: when a Chairman/HO user answers a
    # help_needed escalation, the resolution is recorded here and the flag
    # clears (needs_help = help text present AND help_resolved_at IS NULL).
    # If the CEO later edits the help text on that day, the resolution is
    # cleared again (the request re-opens) — see submit_report.
    help_resolved_at = Column(DateTime, nullable=True)
    help_resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    help_response = Column(Text, nullable=True)
    # The day's headline result, kept separate from key_decisions (what was
    # decided) — what actually got achieved.
    key_achievement = Column(Text, nullable=True)
    meetings_count = Column(Integer, nullable=True)  # 0-99, validated on submit
    document_url = Column(String(500), nullable=True)
    gym_percent = Column(Integer, nullable=True)  # 0-100
    swimming_percent = Column(Integer, nullable=True)  # 0-100
    cycle_percent = Column(Integer, nullable=True)  # 0-100
    # Free-text catch-all for anything not covered by the fields above.
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit", back_populates="activity_logs")


class HealthStatus(Base):
    __tablename__ = "health_statuses"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_health_bu_date"),
        _pg_check(_VITALS_RANGE, name="ck_health_statuses_vitals_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, default=_today_local, index=True)
    physical_health = Column(String(20), nullable=True)  # Healthy | Tired | Sick (legacy, no longer collected)
    stress_level = Column(String(20), nullable=True)  # Low | Medium | High (collected again as one-tap chips)
    energy_mood = Column(String(20), nullable=True)  # Energetic | Normal | Drained (collected again as one-tap chips)
    sleep_quality = Column(String(20), nullable=True)  # Good | Average | Poor (legacy, no longer collected)
    daily_steps = Column(Integer, nullable=True)  # <3000 red | 3000-6999 yellow | >=7000 green
    heart_rate = Column(Integer, nullable=True)  # bpm
    sleep_hours = Column(Numeric(4, 1), nullable=True)
    bp_systolic = Column(Integer, nullable=True)
    bp_diastolic = Column(Integer, nullable=True)
    weight_kg = Column(Numeric(5, 1), nullable=True)  # 20-400, validated on submit
    is_sick = Column(Boolean, default=False, nullable=False)  # "feeling sick today" toggle
    # Exercise completion moved here from ActivityLog (2026-10-22): the CEO
    # form's Health section collects them now. ActivityLog's percent
    # columns are kept for pre-move history and read paths fall back to
    # them when a day's health row has none.
    gym_percent = Column(Integer, nullable=True)  # 0-100
    swimming_percent = Column(Integer, nullable=True)  # 0-100
    cycle_percent = Column(Integer, nullable=True)  # 0-100
    # Free-text catch-all for anything not covered by the fields above.
    other_notes = Column(EncryptedText, nullable=True)  # health free text — encrypted at rest
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit", back_populates="health_statuses")


class SocialLog(Base):
    __tablename__ = "social_logs"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_social_bu_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, default=_today_local, index=True)
    event_flag = Column(Boolean, default=False, nullable=False)
    meeting_flag = Column(Boolean, default=False, nullable=False)
    golf_flag = Column(Boolean, default=False, nullable=False)
    # Free-text catch-all for anything not covered by the fields above.
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit", back_populates="social_logs")


class HeadOfficeActivityLog(Base):
    """HO Staff's own daily Activity submission — mirrors ActivityLog but for
    Head Office itself rather than a business unit, so it's kept on its own
    table entirely separate from the 23 BUs' data (no business_unit_id at
    all) instead of reusing ActivityLog with a special BU row, which would
    otherwise leak into every "for bu in business_units" admin loop
    (Comparison, Operations, Excel export). One row per calendar day."""

    __tablename__ = "ho_activity_logs"
    __table_args__ = (_pg_check(_PERCENT_RANGE, name="ck_ho_activity_logs_percent_range"),)

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, nullable=False, unique=True, index=True, default=_today_local)
    meetings_attended = Column(Text, nullable=True)
    site_visits = Column(Text, nullable=True)
    key_decisions = Column(Text, nullable=True)
    next_day_schedule = Column(Text, nullable=True)
    document_url = Column(String(500), nullable=True)
    gym_percent = Column(Integer, nullable=True)  # 0-100
    swimming_percent = Column(Integer, nullable=True)  # 0-100
    cycle_percent = Column(Integer, nullable=True)  # 0-100
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class HeadOfficeDailyFinancial(Base):
    """HO Staff's own daily Business Performance submission — mirrors
    DailyFinancial's mandatory fields (Revenue/Cash Flow/Receivable/Payable/
    Other Amount), kept on its own table for the same reason as
    HeadOfficeActivityLog above. Deliberately excludes MonthlyTarget-style
    cumulative-vs-target tracking — HO has no monthly target, just the plain
    daily figures, unlike a business unit's CEO-set benchmark."""

    __tablename__ = "ho_daily_financials"
    __table_args__ = (
        _pg_check(
            _money_range("revenue", "cash_flow", "receivable", "payable", "other_amount"),
            name="ck_ho_daily_financials_amount_range",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, nullable=False, unique=True, index=True, default=_today_local)
    revenue = Column(Numeric(16, 2), nullable=False, default=0)
    # Never collected from the form — build_financial_rows() (shared with
    # DailyFinancial) reads these unconditionally, so they exist here purely
    # for that compatibility, always zero, same as DailyFinancial's own
    # legacy columns.
    expenses = Column(Numeric(16, 2), nullable=False, default=0)
    net_profit = Column(Numeric(16, 2), nullable=False, default=0)
    cash_in_hand = Column(Numeric(16, 2), nullable=False, default=0)
    cash_flow = Column(Numeric(16, 2), nullable=True)
    receivable = Column(Numeric(16, 2), nullable=True)
    payable = Column(Numeric(16, 2), nullable=True)
    other_amount = Column(Numeric(16, 2), nullable=True)
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class HeadOfficeHealthStatus(Base):
    """HO Staff's own daily Health submission — mirrors HealthStatus, kept on
    its own table for the same reason as HeadOfficeActivityLog above."""

    __tablename__ = "ho_health_statuses"
    __table_args__ = (_pg_check(_VITALS_RANGE, name="ck_ho_health_statuses_vitals_range"),)

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, nullable=False, unique=True, index=True, default=_today_local)
    physical_health = Column(String(20), nullable=True)  # legacy, no longer collected
    stress_level = Column(String(20), nullable=True)  # legacy, no longer collected
    energy_mood = Column(String(20), nullable=True)  # legacy, no longer collected
    sleep_quality = Column(String(20), nullable=True)  # legacy, no longer collected
    daily_steps = Column(Integer, nullable=True)
    heart_rate = Column(Integer, nullable=True)
    sleep_hours = Column(Numeric(4, 1), nullable=True)
    bp_systolic = Column(Integer, nullable=True)
    bp_diastolic = Column(Integer, nullable=True)
    other_notes = Column(EncryptedText, nullable=True)  # health free text — encrypted at rest
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class HeadOfficeSocialLog(Base):
    """HO Staff's own daily Social submission — mirrors SocialLog, kept on its
    own table for the same reason as HeadOfficeActivityLog above."""

    __tablename__ = "ho_social_logs"

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, nullable=False, unique=True, index=True, default=_today_local)
    event_flag = Column(Boolean, default=False, nullable=False)
    meeting_flag = Column(Boolean, default=False, nullable=False)
    golf_flag = Column(Boolean, default=False, nullable=False)
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class HeadOfficeMonthlyTarget(Base):
    """HO Staff's own once-a-month target — mirrors MonthlyTarget, kept on its
    own table for the same reason as HeadOfficeActivityLog above. Unique on
    target_month alone (no business_unit_id) since there's only ever one
    Head Office to set a target for."""

    __tablename__ = "ho_monthly_targets"
    __table_args__ = (
        _pg_check(_TARGETS_RANGE, name="ck_ho_monthly_targets_targets_range"),
        _pg_check("extract(day from target_month) = 1", name="ck_ho_monthly_targets_month_first"),
    )

    id = Column(Integer, primary_key=True, index=True)
    target_month = Column(Date, nullable=False, unique=True, index=True)  # always the 1st of the target month
    monthly_revenue_target = Column(Numeric(16, 2), nullable=False)
    monthly_cash_flow_target = Column(Numeric(16, 2), nullable=False)
    monthly_max_receivable = Column(Numeric(16, 2), nullable=False)
    monthly_max_payable = Column(Numeric(16, 2), nullable=False)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class MonthlyTarget(Base):
    __tablename__ = "monthly_targets"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "target_month", name="uq_target_bu_month"),
        _pg_check(_TARGETS_RANGE, name="ck_monthly_targets_targets_range"),
        _pg_check("extract(day from target_month) = 1", name="ck_monthly_targets_month_first"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    target_month = Column(Date, nullable=False, index=True)  # always the 1st of the target month
    monthly_revenue_target = Column(Numeric(16, 2), nullable=False)
    monthly_cash_flow_target = Column(Numeric(16, 2), nullable=False)
    monthly_max_receivable = Column(Numeric(16, 2), nullable=False)
    monthly_max_payable = Column(Numeric(16, 2), nullable=False)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class FinancialNote(Base):
    """Free-text log entries a CEO can add throughout the month (e.g. "big
    client paid early", "power outage cut two days of trading") — shown as a
    running "Record Notes" list on the CEO's own dashboard and every admin
    view of that business unit, giving qualitative context alongside the
    Cumulative Net Revenue figures. Multiple notes per day are allowed since
    this is an append-only log, not a single per-day field like the other
    daily report tables."""

    __tablename__ = "financial_notes"
    __table_args__ = (
        Index("ix_financial_notes_bu_date", "business_unit_id", "note_date"),
        _pg_check("length(btrim(note_text)) > 0", name="ck_financial_notes_text"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    note_date = Column(Date, nullable=False, default=_today_local, index=True)
    note_text = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class CeoProfile(Base):
    __tablename__ = "ceo_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    # full_name lives on User (already existed pre-profile-setup) — this
    # table only holds the fields specific to the mandatory profile.
    executive_title = Column(String(120), nullable=False, default="Chief Executive Officer")
    phone_number = Column(EncryptedText, nullable=False)
    # Optional — added after the original mandatory fields, so it doesn't
    # block profile completion for CEOs who already finished setup before
    # this existed (same reasoning as business_details below).
    email = Column(EncryptedText, nullable=True)
    date_of_birth = Column(EncryptedDate, nullable=False)
    address = Column(EncryptedText, nullable=False)
    profile_pic_url = Column(String(500), nullable=True)
    # Optional free-text "About the Business" — added after the original
    # mandatory fields, so it doesn't block profile completion for CEOs who
    # already finished setup before this existed.
    business_details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User")


class PasswordHistory(Base):
    """Last passwords per user (m-27): the last 5 hashes are kept so a
    password cannot be reused by rotating back to it. Rows older than the
    newest 5 per user are pruned on write (see src/auth.py
    record_password_history)."""
    __tablename__ = "password_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class PasswordResetRequest(Base):
    __tablename__ = "password_reset_requests"
    __table_args__ = (
        # A user has at most one open request (enforced here, not just in code,
        # so two simultaneous "forgot password" calls can't both insert one).
        Index(
            "uq_pwreset_one_pending_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'Pending'"),
            sqlite_where=text("status = 'Pending'"),
        ),
        _pg_check("status IN ('Pending', 'Approved', 'Rejected')", name="ck_pwreset_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    username = Column(String(80), nullable=False)
    status = Column(String(20), default="Pending", nullable=False)  # Pending | Approved | Rejected
    requested_at = Column(DateTime, default=_utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # The sign-in / password-reset throttles count recent rows by
        # (action, username) and (action, ip); the Security Log and
        # per-user history read by (user, action, time).
        Index("ix_audit_action_username_ts", "action", "username", "timestamp"),
        Index("ix_audit_action_ip_ts", "action", "ip_address", "timestamp"),
        Index("ix_audit_user_action_ts", "user_id", "action", "timestamp"),
        # m-12: report_logs filters action IN (...) ordered by timestamp DESC.
        Index("ix_audit_action_ts", "action", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(80), nullable=True)
    action = Column(String(120), nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    timestamp = Column(DateTime, default=_utcnow, nullable=False, index=True)


class ActionItem(Base):
    """Follow-up tasks from meetings/decisions. Assigned to a user, tracked to completion."""
    __tablename__ = "action_items"
    __table_args__ = (
        # m-12: the reminder job filters status IN (...) AND due_date <= today.
        Index("ix_action_items_status_due", "status", "due_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    assigned_to_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    due_date = Column(Date, nullable=True)
    status = Column(String(20), default="pending", nullable=False, index=True)  # pending/in_progress/done
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    reminder_sent_at = Column(DateTime, nullable=True)  # last due/overdue reminder sent
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class Notification(Base):
    """In-app notifications: missing-report reminders, approvals, alerts."""
    __tablename__ = "notifications"
    __table_args__ = (
        # m-12: the bell query filters user_id + is_read ordered by created_at DESC.
        Index("ix_notifications_user_read_created", "user_id", "is_read", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(40), nullable=False, index=True)  # missing_report/help_resolved/action_assigned/action_due/document_expiry/alert
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=True)
    link = Column(String(300), nullable=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)


class StaffRecord(Base):
    """Daily headcount snapshot per BU."""
    __tablename__ = "staff_records"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_staff_bu_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True)
    total_staff = Column(Integer, default=0, nullable=False)
    present = Column(Integer, default=0, nullable=False)
    absent = Column(Integer, default=0, nullable=False)
    new_hires = Column(Integer, default=0, nullable=False)
    resignations = Column(Integer, default=0, nullable=False)
    notes = Column(Text, nullable=True)
    submitted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class CustomerMetric(Base):
    """Daily customer/footfall metrics per BU."""
    __tablename__ = "customer_metrics"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_customer_bu_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True)
    footfall = Column(Integer, nullable=True)
    transactions = Column(Integer, nullable=True)
    complaints = Column(Integer, default=0, nullable=False)
    satisfaction_score = Column(Numeric(3, 1), nullable=True)  # 1.0 - 5.0
    notes = Column(Text, nullable=True)
    submitted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class LicenseDocument(Base):
    """Licenses, contracts and permits per business unit with expiry tracking."""
    __tablename__ = "license_documents"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    document_type = Column(String(32), default="license", nullable=False)  # license / contract / permit / other
    issue_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=False, index=True)
    notes = Column(Text, nullable=True)
    expiry_notified_at = Column(DateTime, nullable=True)  # last expiry alert sent
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")




class JobHeartbeat(Base):
    """Dead-man heartbeat for scheduled jobs (R-6 / A-1).

    One row per job, upserted by the job itself after each run. The
    log-scan job (src/jobs/log_scan.py) alerts the Chairman when a row is
    stale (> 36 h) — that is how a silently-dead cron is noticed instead
    of being discovered months later when the alerts it should have sent
    never arrive.
    """
    __tablename__ = "job_heartbeats"

    job_name = Column(String(80), primary_key=True)
    last_run_at = Column(DateTime, nullable=False)
    last_status = Column(String(20), nullable=False)  # ok / failed


class IdempotencyKey(Base):
    """m-10: replay record for Idempotency-Key'd tracker creates.

    A retried request (double-click, network retry) that re-sends the
    same (user, endpoint, key) gets the stored response back instead of
    creating a second row. One row per (user, endpoint, key) — the
    unique constraint is the race guard: two concurrent replays both
    miss the lookup, but only one insert wins and the loser replays
    the winner's stored response. The stored body is the exact JSON
    the first call returned, so a replay is indistinguishable from
    the original response.
    """
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "endpoint", "idempotency_key",
                         name="uq_idempotency_user_endpoint_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    endpoint = Column(String(200), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    response_status = Column(Integer, nullable=False)
    response_body = Column(Text, nullable=False)  # JSON of the first response
    created_at = Column(DateTime, default=_utcnow, nullable=False)
