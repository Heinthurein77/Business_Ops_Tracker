"""BOT security-review minors, workstream 2 (tests): T-m1 / T-m3 / T-m5.

- T-m1: the caller-level R2-failure tests themselves (receipt -> 503,
  CEO/HO submit -> 503 with nothing saved, profile photo -> warning)
  already exist in tests/test_r2_reliability.py (Major-pass D-4 work) and
  pass; this file only adds the complementary boundary: a failing R2
  client must NOT fail a report submit that carries no attachment.
- T-m3: the three ops scripts (rotate_data_key, reset_chairman,
  audit_retention) had zero tests; covered here against scratch SQLite DBs.
- T-m5: the ee078d0716ad pre-flight abort on duplicate document numbers,
  mirroring test_b1c5_duplicate_active_ceo_aborts_upgrade.

Run with: ENVIRONMENT=test DATABASE_URL=<redacted> .venv/bin/python -m pytest tests/test_bot_review_minors_tests.py -q
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

import src.crypto as crypto
from src import r2_storage as r2_mod
from src.auth import hash_password, verify_password
from src.config import settings
from src.models import AuditLog, Base, BusinessUnit, DailyFinancial, User
from src.routers.ceo import submit_report as ceo_submit_report
from src.routers.ho import submit_report as ho_submit_report
from src.utils import today_local

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "alembic" / "versions"

PASSWORD = "TestPass123!"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF_BYTES = b"%PDF-1.4 fake pdf content"


# ------------------------------------------------------------------ helpers

def _alembic_env(db_url, extra=None):
    env = {
        **os.environ,
        "ENVIRONMENT": "test",
        "DATABASE_URL": db_url,
        "MIGRATION_DATABASE_URL": db_url,
    }
    if extra:
        env.update(extra)
    return env


def _run_alembic(db_url, *args, extra=None):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT, env=_alembic_env(db_url, extra),
        capture_output=True, text=True, timeout=600,
    )


def _stage_db(tmp_path, revision, extra=None):
    """Scratch DB upgraded to `revision`; returns (path, url)."""
    db_path = tmp_path / f"stage_{revision[:8]}.db"
    url = f"sqlite:///{db_path}"
    result = _run_alembic(url, "upgrade", revision, extra=extra)
    assert result.returncode == 0, (
        f"alembic upgrade {revision} failed:\n{result.stdout}\n{result.stderr}"
    )
    return db_path, url


def _raw(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class _FakeS3:
    def __init__(self, fail=False):
        self.fail = fail

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        if self.fail:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchBucket", "Message": "boom"}},
                              "UploadPart")
        fileobj.read()

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        return "https://presigned.example/object"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
    ])
    ceo = User(id=801, username="ceo_one", hashed_password=hash_password(PASSWORD),
               role="CEO", business_unit_id=1, is_active=True)
    ho = User(id=803, username="ho_user", hashed_password=hash_password(PASSWORD),
              role="HO", is_active=True)
    session.add_all([ceo, ho])
    session.commit()
    session.test_users = {"ceo": ceo, "ho": ho}
    yield session
    session.close()
    engine.dispose()


def _ceo_submit(db, user, **overrides):
    import src.routers.ceo as ceo_router
    ceo_router._rate_limiter.reset()
    payload = dict(
        report_date="", revenue="100000", cash_flow="0", receivable="0", payable="0",
        other_amount="", financial_other_notes="", meetings_attended="Standup",
        meetings_count="", site_visits="", key_decisions="", key_achievement="",
        next_day_schedule="", problems_today="", help_needed="", gym_percent="",
        swimming_percent="", cycle_percent="", activity_other_notes="",
        daily_steps="", heart_rate="", sleep_hours="", bp_systolic="", bp_diastolic="",
        stress_level="", energy_mood="", weight_kg="", is_sick="false",
        health_gym_percent=None, health_swimming_percent=None, health_cycle_percent=None,
        health_other_notes="", event_flag="false", meeting_flag="false",
        golf_flag="false", social_other_notes="", attachment=None,
    )
    payload.update(overrides)
    request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
    return ceo_submit_report(request=request, user=user, db=db, **payload)


def _ho_submit(db, user, **overrides):
    import src.routers.ho as ho_router
    ho_router._rate_limiter.reset()
    payload = dict(
        revenue="100000", cash_flow="20000", receivable="5000", payable="3000",
        other_amount="1000", financial_other_notes="", meetings_attended="Standup",
        site_visits="", key_decisions="", next_day_schedule="", gym_percent="50",
        swimming_percent="", cycle_percent="", activity_other_notes="",
        daily_steps="8000", heart_rate="72", sleep_hours="7.5", bp_systolic="120",
        bp_diastolic="80", health_other_notes="", event_flag="true",
        meeting_flag="false", golf_flag="false", social_other_notes="",
        attachment=None,
    )
    payload.update(overrides)
    request = Request({"type": "http", "headers": [], "client": ("8.8.8.8", 5555)})
    return ho_submit_report(request, user=user, db=db, **payload)


# ------------------------------------------------------- T-m1 (complement)
# The failing-R2 caller tests live in tests/test_r2_reliability.py
# (receipt -> 503, CEO/HO submit with attachment -> 503 and nothing saved,
# profile photo -> warning). The boundary those tests do not pin: a submit
# with NO attachment must not fail just because R2 is down.

def test_ceo_submit_without_attachment_succeeds_when_r2_down(db, monkeypatch):
    monkeypatch.setattr(r2_mod, "_get_client", lambda: _FakeS3(fail=True))
    out = _ceo_submit(db, db.test_users["ceo"])
    assert out["ok"] is True
    assert db.query(DailyFinancial).filter(
        DailyFinancial.business_unit_id == 1,
        DailyFinancial.report_date == today_local()).count() == 1


def test_ho_submit_without_attachment_succeeds_when_r2_down(db, monkeypatch):
    monkeypatch.setattr(r2_mod, "_get_client", lambda: _FakeS3(fail=True))
    out = _ho_submit(db, db.test_users["ho"])
    assert out["ok"] is True


# ------------------------------------------------------- T-m3: rotate_data_key

@pytest.fixture()
def rotated_keys(monkeypatch):
    """Swap the crypto module onto a fresh (new, old) key pair.

    Returns (old_key, new_key) as decoded strings. Restores the module
    globals afterwards, exactly like the d3e7 backfill test does.
    """
    import src.crypto as crypto_mod
    old_key = Fernet.generate_key()
    new_key = Fernet.generate_key()
    old_fernet, old_primary = crypto_mod._FERNET, crypto_mod._PRIMARY
    crypto_mod._FERNET = MultiFernet([Fernet(new_key), Fernet(old_key)])
    crypto_mod._PRIMARY = Fernet(new_key)
    yield old_key.decode(), new_key.decode()
    crypto_mod._FERNET, crypto_mod._PRIMARY = old_fernet, old_primary


def _enc_under(key, value):
    return crypto.PREFIX + Fernet(key.encode()).encrypt(value.encode()).decode()


@pytest.fixture()
def scratch_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_rotate_data_key_plaintext_old_key_and_newest(db, scratch_engine, rotated_keys, monkeypatch):
    import src.scripts.rotate_data_key as rotate_mod
    old_key, new_key = rotated_keys
    monkeypatch.setattr(rotate_mod, "engine", scratch_engine)
    # Fernet tokens are salted: two encryptions of the same plaintext
    # differ, so the already-newest value must be compared against the
    # exact token that was inserted, not a fresh encryption.
    addr_token = _enc_under(new_key, "Yangon")
    with scratch_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ceo_profiles (id, user_id, executive_title, phone_number, email, "
            "address, date_of_birth, created_at, updated_at) VALUES "
            "(1, 801, 'Chief Executive Officer', :phone, :email, :addr, :dob, "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"),
            {"phone": "+959111111",  # legacy plaintext
             "email": _enc_under(old_key, "ceo@example.com"),  # old-key-only
             "addr": addr_token,  # already newest: untouched
             "dob": "2001-11-23"})  # legacy plaintext date
        conn.execute(text(
            "INSERT INTO health_statuses (id, business_unit_id, report_date, other_notes, "
            "is_sick, created_at, updated_at) VALUES "
            "(1, 1, '2026-09-01', 'feeling fine', 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
        conn.execute(text(
            "INSERT INTO ho_health_statuses (id, report_date, other_notes, "
            "created_at, updated_at) VALUES "
            "(1, '2026-09-01', :notes, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"),
            {"notes": _enc_under(old_key, "ho note")})

    changed = rotate_mod.rotate_all()
    assert changed["ceo_profiles.phone_number"] == 1
    assert changed["ceo_profiles.email"] == 1
    assert changed["ceo_profiles.address"] == 0
    assert changed["ceo_profiles.date_of_birth"] == 1
    assert changed["health_statuses.other_notes"] == 1
    assert changed["ho_health_statuses.other_notes"] == 1

    with scratch_engine.begin() as conn:
        row = conn.execute(text(
            "SELECT phone_number, email, address, date_of_birth FROM ceo_profiles WHERE id = 1"
        )).fetchone()
        phone, email, address, dob = row
        # Plaintext got encrypted under the NEW key; the old-key value was
        # re-keyed (decrypts to the same plaintext, now under the primary);
        # the already-newest value is byte-identical.
        assert phone.startswith(crypto.PREFIX) and crypto.decrypt_text(phone) == "+959111111"
        assert crypto.is_under_primary_key(phone)
        assert email.startswith(crypto.PREFIX) and crypto.decrypt_text(email) == "ceo@example.com"
        assert crypto.is_under_primary_key(email)
        assert address == addr_token  # byte-identical: left untouched
        assert crypto.decrypt_text(dob) == "2001-11-23"
        assert crypto.is_under_primary_key(dob)
        note = conn.execute(text(
            "SELECT other_notes FROM health_statuses WHERE id = 1")).scalar()
        assert crypto.decrypt_text(note) == "feeling fine"
        assert crypto.is_under_primary_key(note)
        ho_note = conn.execute(text(
            "SELECT other_notes FROM ho_health_statuses WHERE id = 1")).scalar()
        assert crypto.decrypt_text(ho_note) == "ho note"
        assert crypto.is_under_primary_key(ho_note)

    # Second run is a no-op: everything is already under the newest key.
    assert rotate_mod.rotate_all() == {k: 0 for k in changed}


def test_rotate_data_key_main_refuses_without_key(monkeypatch):
    import src.scripts.rotate_data_key as rotate_mod
    monkeypatch.setattr(rotate_mod, "encryption_enabled", lambda: False)
    assert rotate_mod.main() == 1


def test_rotate_data_key_main_reports_counts(scratch_engine, rotated_keys, monkeypatch, capsys):
    import src.scripts.rotate_data_key as rotate_mod
    old_key, new_key = rotated_keys
    monkeypatch.setattr(rotate_mod, "engine", scratch_engine)
    with scratch_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ceo_profiles (id, user_id, executive_title, phone_number, "
            "date_of_birth, address, created_at, updated_at) VALUES "
            "(1, 801, 'Chief Executive Officer', 'plaintext-phone', '2001-11-23', 'Yangon', "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
    assert rotate_mod.main() == 0
    assert "ceo_profiles.phone_number: 1 value(s) re-encrypted" in capsys.readouterr().out


# ------------------------------------------------------- T-m3: reset_chairman

def _chairman_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return engine, session


def test_reset_chairman_revokes_sessions_and_sets_temp_password():
    from src.scripts.reset_chairman import reset_chairman_password
    engine, session = _chairman_session()
    try:
        chairman = User(id=1, username="chairman", hashed_password=hash_password("Chairman123!"),
                        role="Chairman", is_active=True, token_version=0)
        session.add(chairman)
        session.commit()
        username, temp_password = reset_chairman_password(session)
        assert username == "chairman"
        assert temp_password != "Chairman123!"
        session.expire_all()
        chairman = session.query(User).filter(User.username == "chairman").one()
        assert verify_password(temp_password, chairman.hashed_password)
        # Old sessions are revoked: token_version bumped exactly once.
        assert chairman.token_version == 1
        assert chairman.require_password_change is True
        assert chairman.temp_password_expires_at is not None
        # Pm-4: naive UTC — temp_password_expires_at is stored naive.
        assert chairman.temp_password_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
        assert chairman.is_active is True
        # The recovery itself is audited (log_audit writes on its own
        # session, so the row is visible here on the same engine).
        assert session.query(AuditLog).filter(
            AuditLog.action == "CHAIRMAN_RECOVERY_SCRIPT").count() == 1
    finally:
        session.close()
        engine.dispose()


def test_reset_chairman_refuses_without_chairman():
    from src.scripts.reset_chairman import reset_chairman_password
    engine, session = _chairman_session()
    try:
        with pytest.raises(RuntimeError, match="No account with role 'Chairman'"):
            reset_chairman_password(session)
    finally:
        session.close()
        engine.dispose()


def test_reset_chairman_refuses_two_chairmen():
    from src.scripts.reset_chairman import reset_chairman_password
    engine, session = _chairman_session()
    try:
        session.add_all([
            User(id=1, username="chairman", hashed_password=hash_password("x"),
                 role="Chairman", is_active=True, token_version=0),
            User(id=2, username="chairman2", hashed_password=hash_password("x"),
                 role="Chairman", is_active=True, token_version=0),
        ])
        session.commit()
        with pytest.raises(RuntimeError, match="Expected exactly one Chairman"):
            reset_chairman_password(session)
    finally:
        session.close()
        engine.dispose()


# ------------------------------------------------------- T-m3: audit_retention

def _seed_audit_logs(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # Pm-4: naive UTC — AuditLog.timestamp is a naive column.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add_all([
        # Old sign-in/out rows: deleted by the 90-day rule.
        AuditLog(action="LOGIN_SUCCESS", username="ceo_one", ip_address="1.2.3.4",
                 timestamp=now - timedelta(days=100)),
        AuditLog(action="LOGIN_FAILED", username="nobody", ip_address="1.2.3.5",
                 timestamp=now - timedelta(days=100)),
        # Recent sign-in row: kept, IP kept (younger than 30 days).
        AuditLog(action="LOGIN_SUCCESS", username="ceo_one", ip_address="1.2.3.6",
                 timestamp=now - timedelta(days=10)),
        # Old financial row: KEPT as tamper evidence, but its IP is cleared.
        AuditLog(action="REPORT_SUBMITTED", username="ceo_one", ip_address="1.2.3.7",
                 timestamp=now - timedelta(days=100)),
        # 40-day-old LOGOUT: too young to delete, old enough to clear its IP.
        AuditLog(action="LOGOUT", username="ceo_one", ip_address="1.2.3.8",
                 timestamp=now - timedelta(days=40)),
        # Recent financial row: kept with its IP.
        AuditLog(action="REPORT_SUBMITTED", username="ho_user", ip_address="1.2.3.9",
                 timestamp=now - timedelta(days=10)),
    ])
    session.commit()
    session.close()
    engine.dispose()
    return f"sqlite:///{db_path}"


def test_audit_retention_dry_run_reports_without_changing(tmp_path, monkeypatch):
    from src.scripts.audit_retention import run
    url = _seed_audit_logs(tmp_path / "audit.db")
    monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", url)
    deleted, cleared = run(90, 30, dry_run=True)
    assert (deleted, cleared) == (2, 2)
    # Nothing actually changed.
    engine = create_engine(url)
    try:
        session = sessionmaker(bind=engine)()
        assert session.query(AuditLog).count() == 6
        assert session.query(AuditLog).filter(AuditLog.ip_address.isnot(None)).count() == 6
        session.close()
    finally:
        engine.dispose()


def test_audit_retention_real_run_deletes_and_clears(tmp_path, monkeypatch):
    from src.scripts.audit_retention import run
    url = _seed_audit_logs(tmp_path / "audit.db")
    monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", url)
    deleted, cleared = run(90, 30, dry_run=False)
    assert (deleted, cleared) == (2, 2)
    engine = create_engine(url)
    try:
        session = sessionmaker(bind=engine)()
        rows = session.query(AuditLog).order_by(AuditLog.id).all()
        assert len(rows) == 4
        by_action = [(r.action, r.ip_address) for r in rows]
        # Old sign-in/out rows are gone; the old financial row survives
        # with its IP cleared; the 40-day LOGOUT survives with IP cleared;
        # recent rows survive untouched.
        assert ("LOGIN_SUCCESS", "1.2.3.6") in by_action
        assert ("REPORT_SUBMITTED", None) in by_action
        assert ("LOGOUT", None) in by_action
        assert ("REPORT_SUBMITTED", "1.2.3.9") in by_action
        assert not any(a in ("LOGIN_FAILED",) for a, _ in by_action)
        assert session.query(AuditLog).filter(
            AuditLog.action == "LOGIN_FAILED").count() == 0
        session.close()
    finally:
        engine.dispose()


# ------------------------------------------------------- T-m5: ee078d0716ad abort


def test_ee078d0716ad_unique_numbers_upgrade_cleanly(tmp_path):
    db_path, url = _stage_db(tmp_path, "c9ec96d6b1e5")
    _raw(db_path, "INSERT INTO business_units (id, code, name, created_at) "
                  "VALUES (1, 'B1', 'BU 1', '2026-01-01 00:00:00')")
    _raw(db_path, "INSERT INTO suppliers (id, business_unit_id, name, is_active, created_at) "
                  "VALUES (1, 1, 'Supplier One', 1, '2026-01-01 00:00:00')")
    _raw(db_path, "INSERT INTO customers (id, business_unit_id, name, is_active, created_at) "
                  "VALUES (1, 1, 'Customer One', 1, '2026-01-01 00:00:00')")
    _raw(db_path,
         "INSERT INTO purchase_orders (id, business_unit_id, supplier_id, po_no, "
         "status, order_date, created_at) "
         "VALUES (1, 1, 1, 'PO-B1-0001', 'draft', '2026-09-01', '2026-01-01 00:00:00')")
    _raw(db_path,
         "INSERT INTO sales_invoices (id, business_unit_id, customer_id, invoice_no, "
         "status, invoice_date, tax_rate, created_at) "
         "VALUES (1, 1, 1, 'INV-B1-0001', 'draft', '2026-09-01', 5, '2026-01-01 00:00:00')")
    result = _run_alembic(url, "upgrade", "ee078d0716ad")
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"
    assert _query(db_path, "SELECT version_num FROM alembic_version") == [("ee078d0716ad",)]
    # SQLite's batch mode rebuilds the table, so the unique constraints
    # live in the table DDL (as autoindexes), not as named index rows.
    po_ddl = _query(db_path, "SELECT sql FROM sqlite_master WHERE type='table' "
                             "AND name='purchase_orders'")[0][0]
    inv_ddl = _query(db_path, "SELECT sql FROM sqlite_master WHERE type='table' "
                              "AND name='sales_invoices'")[0][0]
    assert "uq_purchase_orders_bu_po_no" in po_ddl
    assert "uq_sales_invoices_bu_invoice_no" in inv_ddl
    # The new constraints actually reject a duplicate on the migrated DB.
    with pytest.raises(sqlite3.IntegrityError):
        _raw(db_path,
             "INSERT INTO purchase_orders (id, business_unit_id, supplier_id, po_no, "
             "status, order_date, created_at) "
             "VALUES (2, 1, 1, 'PO-B1-0001', 'draft', '2026-09-02', '2026-01-01 00:00:00')")
