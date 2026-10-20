from datetime import datetime, date

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
        _pg_check("role IN ('Chairman', 'HO', 'CEO', 'Accountant')", name="ck_users_role"),
        _pg_check("(role = 'CEO') = (business_unit_id IS NOT NULL)", name="ck_users_ceo_has_bu"),
        _pg_check("username = lower(username)", name="ck_users_username_lower"),
    )

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(150), nullable=True)
    role = Column(String(20), nullable=False)  # Chairman | HO | CEO | Accountant
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=True)
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
    # Set only while the account's password is a Chairman-issued temporary
    # one; login treats an expired temporary password like a wrong one, so an
    # unclaimed credential can't stay live indefinitely. Cleared as soon as
    # the owner sets their own password.
    temp_password_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
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
    report_date = Column(Date, nullable=False, default=date.today, index=True)
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
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit", back_populates="financials")


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_activity_bu_date"),
        _pg_check(_PERCENT_RANGE, name="ck_activity_logs_percent_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, default=date.today, index=True)
    meetings_attended = Column(Text, nullable=True)
    site_visits = Column(Text, nullable=True)
    key_decisions = Column(Text, nullable=True)
    next_day_schedule = Column(Text, nullable=True)
    document_url = Column(String(500), nullable=True)
    gym_percent = Column(Integer, nullable=True)  # 0-100
    swimming_percent = Column(Integer, nullable=True)  # 0-100
    cycle_percent = Column(Integer, nullable=True)  # 0-100
    # Free-text catch-all for anything not covered by the fields above.
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit", back_populates="activity_logs")


class HealthStatus(Base):
    __tablename__ = "health_statuses"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_health_bu_date"),
        _pg_check(_VITALS_RANGE, name="ck_health_statuses_vitals_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, default=date.today, index=True)
    physical_health = Column(String(20), nullable=True)  # Healthy | Tired | Sick (legacy, no longer collected)
    stress_level = Column(String(20), nullable=True)  # Low | Medium | High (legacy, no longer collected)
    energy_mood = Column(String(20), nullable=True)  # Energetic | Normal | Drained (legacy, no longer collected)
    sleep_quality = Column(String(20), nullable=True)  # Good | Average | Poor (legacy, no longer collected)
    daily_steps = Column(Integer, nullable=True)  # <3000 red | 3000-6999 yellow | >=7000 green
    heart_rate = Column(Integer, nullable=True)  # bpm
    sleep_hours = Column(Numeric(4, 1), nullable=True)
    bp_systolic = Column(Integer, nullable=True)
    bp_diastolic = Column(Integer, nullable=True)
    # Free-text catch-all for anything not covered by the fields above.
    other_notes = Column(EncryptedText, nullable=True)  # health free text — encrypted at rest
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit", back_populates="health_statuses")


class SocialLog(Base):
    __tablename__ = "social_logs"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "report_date", name="uq_social_bu_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, default=date.today, index=True)
    event_flag = Column(Boolean, default=False, nullable=False)
    meeting_flag = Column(Boolean, default=False, nullable=False)
    golf_flag = Column(Boolean, default=False, nullable=False)
    # Free-text catch-all for anything not covered by the fields above.
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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
    report_date = Column(Date, nullable=False, unique=True, index=True, default=date.today)
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


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
    report_date = Column(Date, nullable=False, unique=True, index=True, default=date.today)
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class HeadOfficeHealthStatus(Base):
    """HO Staff's own daily Health submission — mirrors HealthStatus, kept on
    its own table for the same reason as HeadOfficeActivityLog above."""

    __tablename__ = "ho_health_statuses"
    __table_args__ = (_pg_check(_VITALS_RANGE, name="ck_ho_health_statuses_vitals_range"),)

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, nullable=False, unique=True, index=True, default=date.today)
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class HeadOfficeSocialLog(Base):
    """HO Staff's own daily Social submission — mirrors SocialLog, kept on its
    own table for the same reason as HeadOfficeActivityLog above."""

    __tablename__ = "ho_social_logs"

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, nullable=False, unique=True, index=True, default=date.today)
    event_flag = Column(Boolean, default=False, nullable=False)
    meeting_flag = Column(Boolean, default=False, nullable=False)
    golf_flag = Column(Boolean, default=False, nullable=False)
    other_notes = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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
    note_date = Column(Date, nullable=False, default=date.today, index=True)
    note_text = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User")


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
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
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
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(80), nullable=True)
    action = Column(String(120), nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class ActionItem(Base):
    """Follow-up tasks from meetings/decisions. Assigned to a user, tracked to completion."""
    __tablename__ = "action_items"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    assigned_to_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    due_date = Column(Date, nullable=True)
    status = Column(String(20), default="pending", nullable=False, index=True)  # pending/in_progress/done
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class Notification(Base):
    """In-app notifications: missing-report reminders, approvals, alerts."""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(40), nullable=False, index=True)  # missing_report/expense_approval/action_due/alert
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=True)
    link = Column(String(300), nullable=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class InventoryItem(Base):
    """Stock items per business unit (retail/convenience stores)."""
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    sku = Column(String(60), nullable=True, index=True)
    category = Column(String(80), nullable=True, index=True)
    unit = Column(String(20), default="pcs", nullable=False)
    current_stock = Column(Numeric(14, 2), default=0, nullable=False)
    reorder_level = Column(Numeric(14, 2), default=0, nullable=False)
    unit_cost = Column(Numeric(14, 2), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    low_notified_at = Column(DateTime, nullable=True)  # last low-stock alert sent
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")
    movements = relationship("StockMovement", back_populates="item", cascade="all, delete-orphan")


class StockMovement(Base):
    """Every stock in/out/adjustment — the audit trail for inventory."""
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False, index=True)
    movement_type = Column(String(20), nullable=False, index=True)  # in/out/adjustment
    quantity = Column(Numeric(14, 2), nullable=False)
    reason = Column(String(200), nullable=True)
    recorded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    item = relationship("InventoryItem", back_populates="movements")


class ExpenseRequest(Base):
    """CEO-submitted expenses needing HO/Chairman approval."""
    __tablename__ = "expense_requests"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    submitted_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    category = Column(String(80), nullable=True, index=True)
    description = Column(Text, nullable=True)
    receipt_url = Column(String(500), nullable=True)
    status = Column(String(20), default="pending", nullable=False, index=True)  # pending/approved/rejected
    reviewed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    business_unit = relationship("BusinessUnit")


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


# ------------------------------------------------------------------ accounting (ERP Phase 1)

class Account(Base):
    """Chart of Accounts entry (per business unit).

    Codes follow one group standard so consolidation can sum by code.
    Header accounts group children and cannot be posted to.
    """
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "code", name="uq_account_bu_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    code = Column(String(20), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    account_type = Column(String(20), nullable=False)  # asset/liability/equity/income/expense
    parent_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    is_header = Column(Boolean, default=False, nullable=False)
    is_system = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")
    parent = relationship("Account", remote_side=[id])


class JournalEntry(Base):
    """Double-entry journal header. Posted entries are immutable; corrections
    are made with a reversal entry (reversal_of_id points back)."""
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    entry_no = Column(String(30), nullable=False, index=True)  # JV-YYYY-MM-NNN per BU
    entry_date = Column(Date, nullable=False, index=True)
    description = Column(String(500), nullable=True)
    reference = Column(String(100), nullable=True)
    source = Column(String(30), default="manual", nullable=False, index=True)
    # manual / daily_financial / expense / opening / reversal
    source_id = Column(Integer, nullable=True)
    status = Column(String(20), default="draft", nullable=False, index=True)  # draft / posted
    reversal_of_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    reversed_by_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    posted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    posted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")
    lines = relationship("JournalLine", back_populates="entry", cascade="all, delete-orphan")


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    debit = Column(Numeric(16, 2), default=0, nullable=False)
    credit = Column(Numeric(16, 2), default=0, nullable=False)
    memo = Column(String(255), nullable=True)

    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account")


class FiscalPeriod(Base):
    """One row per BU per month; posting into a closed period is rejected."""
    __tablename__ = "fiscal_periods"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "period", name="uq_fiscal_bu_period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    period = Column(String(7), nullable=False, index=True)  # YYYY-MM
    status = Column(String(10), default="open", nullable=False)  # open / closed
    closed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    closed_at = Column(DateTime, nullable=True)

    business_unit = relationship("BusinessUnit")


class PostingRule(Base):
    """Maps an auto-posting source field to debit/credit account codes.

    business_unit_id NULL = group default; a BU-specific row wins.
    """
    __tablename__ = "posting_rules"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "source_field", name="uq_posting_rule"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=True, index=True)
    source_field = Column(String(60), nullable=False, index=True)
    debit_account_code = Column(String(20), nullable=False)
    credit_account_code = Column(String(20), nullable=False)


# ------------------------------------------------------------------ procurement (ERP Phase 3)

class Supplier(Base):
    """Vendors. business_unit_id NULL = group-shared supplier."""
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=True, index=True)
    name = Column(String(150), nullable=False)
    contact_person = Column(String(120), nullable=True)
    phone = Column(String(40), nullable=True)
    email = Column(String(150), nullable=True)
    address = Column(String(300), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class PurchaseOrder(Base):
    """draft -> ordered -> received -> paid (or cancelled).

    Receiving posts Dr Inventory / Cr Accounts Payable; paying posts
    Dr Accounts Payable / Cr Cash (when the BU has a chart of accounts).
    """
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)
    po_no = Column(String(30), nullable=False, index=True)
    status = Column(String(20), default="draft", nullable=False, index=True)
    order_date = Column(Date, nullable=False)
    notes = Column(String(500), nullable=True)
    received_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")
    supplier = relationship("Supplier")
    lines = relationship("PurchaseOrderLine", back_populates="po", cascade="all, delete-orphan")


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)
    item_name = Column(String(150), nullable=False)
    quantity = Column(Numeric(14, 2), nullable=False)
    unit_cost = Column(Numeric(16, 2), nullable=False)

    po = relationship("PurchaseOrder", back_populates="lines")


# ------------------------------------------------------------------ sales & receivables (ERP Phase 4)

class Customer(Base):
    """Sales customers per BU (distinct from CustomerMetric daily counts)."""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    contact_person = Column(String(120), nullable=True)
    phone = Column(String(40), nullable=True)
    email = Column(String(150), nullable=True)
    address = Column(String(300), nullable=True)
    credit_limit = Column(Numeric(16, 2), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class SalesInvoice(Base):
    """draft -> sent -> partial -> paid (overdue is computed from due_date).

    Sending posts Dr 1130 AR (gross) / Cr 4100 Sales (net) / Cr 2130 Tax.
    Payments post Dr 1110 Cash / Cr 1130 AR.
    """
    __tablename__ = "sales_invoices"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    invoice_no = Column(String(30), nullable=False, index=True)
    status = Column(String(20), default="draft", nullable=False, index=True)
    invoice_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    tax_rate = Column(Numeric(5, 2), default=5, nullable=False)  # % commercial tax
    notes = Column(String(500), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")
    customer = relationship("Customer")
    lines = relationship("SalesInvoiceLine", back_populates="invoice", cascade="all, delete-orphan")
    payments = relationship("InvoicePayment", back_populates="invoice", cascade="all, delete-orphan")


class SalesInvoiceLine(Base):
    __tablename__ = "sales_invoice_lines"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("sales_invoices.id"), nullable=False, index=True)
    description = Column(String(200), nullable=False)
    quantity = Column(Numeric(14, 2), default=1, nullable=False)
    unit_price = Column(Numeric(16, 2), nullable=False)

    invoice = relationship("SalesInvoice", back_populates="lines")


class InvoicePayment(Base):
    __tablename__ = "invoice_payments"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("sales_invoices.id"), nullable=False, index=True)
    amount = Column(Numeric(16, 2), nullable=False)
    payment_date = Column(Date, nullable=False)
    method = Column(String(20), default="cash", nullable=False)  # cash/bank/mobile
    notes = Column(String(255), nullable=True)
    received_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    invoice = relationship("SalesInvoice", back_populates="payments")


# ------------------------------------------------------------------ HR & payroll (ERP Phase 5)

class Employee(Base):
    """Staff master per BU (people, distinct from login Users)."""
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    position = Column(String(120), nullable=True)
    phone = Column(String(40), nullable=True)
    base_salary = Column(Numeric(16, 2), default=0, nullable=False)  # monthly
    joined_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    leave_type = Column(String(30), default="annual", nullable=False)  # annual/sick/casual/unpaid
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    reason = Column(String(300), nullable=True)
    status = Column(String(20), default="pending", nullable=False, index=True)
    reviewed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")
    employee = relationship("Employee")


class PayrollRun(Base):
    """One run per BU per month: draft -> approved -> paid.

    Paying posts Dr 6200 Salaries (gross) / Cr 1110 Cash (net) /
    Cr 2120 Accrued (deductions) when the BU has a chart of accounts.
    """
    __tablename__ = "payroll_runs"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "period", name="uq_payroll_bu_period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    period = Column(String(7), nullable=False, index=True)  # YYYY-MM
    status = Column(String(20), default="draft", nullable=False, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")
    lines = relationship("PayrollLine", back_populates="run", cascade="all, delete-orphan")


class PayrollLine(Base):
    __tablename__ = "payroll_lines"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("payroll_runs.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    base_salary = Column(Numeric(16, 2), default=0, nullable=False)
    allowances = Column(Numeric(16, 2), default=0, nullable=False)
    deductions = Column(Numeric(16, 2), default=0, nullable=False)

    run = relationship("PayrollRun", back_populates="lines")
    employee = relationship("Employee")


# ------------------------------------------------------------------ fixed assets & banking (ERP Phase 6)

class FixedAsset(Base):
    """Asset register. Straight-line depreciation:
    monthly = (cost - salvage) / useful_life_months, posted by run to
    Dr 6900 Depreciation Expense / Cr 1290 Accumulated Depreciation."""
    __tablename__ = "fixed_assets"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    category = Column(String(80), nullable=True)
    purchase_date = Column(Date, nullable=False)
    cost = Column(Numeric(16, 2), nullable=False)
    salvage_value = Column(Numeric(16, 2), default=0, nullable=False)
    useful_life_months = Column(Integer, nullable=False)
    accumulated_depreciation = Column(Numeric(16, 2), default=0, nullable=False)
    last_depreciated_period = Column(String(7), nullable=True)  # YYYY-MM
    status = Column(String(20), default="active", nullable=False)  # active/disposed
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")


class BankAccount(Base):
    """A real bank account, linked to the CoA account it reconciles against."""
    __tablename__ = "bank_accounts"

    id = Column(Integer, primary_key=True, index=True)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False, index=True)
    bank_name = Column(String(120), nullable=False)
    account_name = Column(String(150), nullable=True)
    account_number = Column(String(60), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    business_unit = relationship("BusinessUnit")
    account = relationship("Account")
    transactions = relationship("BankTransaction", back_populates="bank_account", cascade="all, delete-orphan")


class BankTransaction(Base):
    """One line of a bank statement, entered/imported for reconciliation."""
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True, index=True)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False, index=True)
    txn_date = Column(Date, nullable=False)
    description = Column(String(300), nullable=True)
    deposit = Column(Numeric(16, 2), default=0, nullable=False)
    withdrawal = Column(Numeric(16, 2), default=0, nullable=False)
    is_reconciled = Column(Boolean, default=False, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    bank_account = relationship("BankAccount", back_populates="transactions")
