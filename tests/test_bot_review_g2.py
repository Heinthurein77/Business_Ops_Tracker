"""Regression tests for the 2026-11-17 review finding G-2 (Major).

Settle / reverse had no claim, no row lock, no idempotency — two
concurrent settles/reverses double-posted journals. The fix mirrors
the CM-3 claim-guard pattern the neighbouring money endpoints already
use: a conditional UPDATE claim (loser 409) + with_for_update() load,
Idempotency-Key on settle (reverse already had a key via postJson), and
a UNIQUE constraint on JournalEntry.reversed_by_id as the DB backstop.

Honest limit (as in the review): SQLite serialises writes, so the
threaded tests below prove the application logic (exactly one winner,
loser 409, nothing extra posted) — not true multi-worker Postgres
behaviour. No Postgres exists in this VM.

Run with: ENVIRONMENT=test DATABASE_URL=<redacted> .venv/bin/python -m pytest tests/test_bot_review_g2.py
"""
import threading
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.accounting_templates import template_accounts
from src.models import (
    Account, Base, BusinessUnit, IntercompanyTransaction, JournalEntry, User,
)
from src.routers.accounting import (
    IntercompanyIn, JournalIn, JournalLineIn, create_intercompany,
    create_journal, post_journal, provision_bu, reverse_journal,
    settle_intercompany,
)

CHAIRMAN = SimpleNamespace(id=1, role="Chairman", business_unit_id=None, username="chairman")


def _provision(session, bu, template):
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts(template):
        acc = Account(business_unit_id=bu.id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        session.add(acc)
        session.flush()
        id_by_code[code] = acc.id
    session.commit()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu1 = BusinessUnit(name="Shop", code="S1")
    bu2 = BusinessUnit(name="Warehouse", code="W1")
    session.add_all([bu1, bu2])
    session.commit()
    _provision(session, bu1, "retail")
    _provision(session, bu2, "distribution")
    session.test_bu = bu1
    session.test_bu2 = bu2
    yield session
    session.close()


@pytest.fixture()
def file_db(tmp_path):
    """File-backed SQLite DB with WAL so two threads can write in turn
    (tests/test_rt_concurrency.py pattern)."""
    db_path = tmp_path / "g2.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, username="chairman", hashed_password="<redacted>", role="Chairman", is_active=True))
    bu = BusinessUnit(id=1, code="BU1", name="Shop One", nature="retail")
    bu2 = BusinessUnit(id=2, code="BU2", name="Shop Two", nature="distribution")
    session.add_all([bu, bu2])
    session.commit()
    provision_bu(1, "retail", CHAIRMAN, session)
    provision_bu(2, "distribution", CHAIRMAN, session)
    session.commit()
    session.test_engine = engine
    yield session
    session.close()
    engine.dispose()


def _run_two(engine, fn):
    """Run fn(session) in two threads at the same time; returns the two
    outcomes as ("ok", value) or ("error", exception)."""
    barrier = threading.Barrier(2, timeout=30)
    outcomes = [None, None]

    def _worker(slot):
        session = sessionmaker(bind=engine)()
        try:
            barrier.wait(timeout=30)
            outcomes[slot] = ("ok", fn(session))
        except Exception as exc:  # noqa: BLE001 - the outcome IS the assertion
            outcomes[slot] = ("error", exc)
        finally:
            session.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "concurrency worker hung"
    return outcomes


def _fresh(engine):
    return sessionmaker(bind=engine)()


def _expect_409(fn):
    with pytest.raises(HTTPException) as exc:
        fn()
    assert exc.value.status_code == 409, f"expected 409, got {exc.value.status_code}: {exc.value.detail}"
    return exc.value


def _account_id(db, bu_id, code):
    return db.query(Account).filter(Account.business_unit_id == bu_id,
                                    Account.code == code).first().id


def _make_intercompany(db, seller_id, buyer_id, amount=300000):
    return create_intercompany(IntercompanyIn(
        from_business_unit_id=seller_id, to_business_unit_id=buyer_id,
        amount=amount, description="Shared logistics", txn_date=date(2026, 10, 10),
        buyer_account_code="6400",
    ), user=CHAIRMAN, db=db)


def _intercompany_journals(db, txn_id):
    return db.query(JournalEntry).filter(
        JournalEntry.source == "intercompany", JournalEntry.source_id == txn_id,
        JournalEntry.status == "posted").all()


def _make_posted_journal(db, bu_id):
    e = create_journal(JournalIn(
        business_unit_id=bu_id, entry_date=date(2026, 10, 20),
        description="manual", lines=[
            JournalLineIn(account_id=_account_id(db, bu_id, "1110"), debit=1000, credit=0),
            JournalLineIn(account_id=_account_id(db, bu_id, "4100"), debit=0, credit=1000),
        ]), user=CHAIRMAN, db=db)
    post_journal(e["id"], user=CHAIRMAN, db=db)
    return e


def _reversals_of(db, entry_id):
    return db.query(JournalEntry).filter(
        JournalEntry.source == "reversal",
        JournalEntry.reversal_of_id == entry_id,
        JournalEntry.status == "posted").all()


# ------------------------------------------------------------------ G-2: settle

class TestG2SettleClaim:
    def test_second_settle_sequential_is_409_and_posts_nothing_extra(self, db):
        seller, buyer = db.test_bu, db.test_bu2
        t = _make_intercompany(db, seller.id, buyer.id)
        # create posts 2 journals (one per BU); settle posts 2 more.
        assert len(_intercompany_journals(db, t["id"])) == 2
        out = settle_intercompany(t["id"], user=CHAIRMAN, db=db)
        assert out["status"] == "settled"
        assert len(_intercompany_journals(db, t["id"])) == 4
        _expect_409(lambda: settle_intercompany(t["id"], user=CHAIRMAN, db=db))
        db.rollback()
        assert len(_intercompany_journals(db, t["id"])) == 4
        check = db.query(IntercompanyTransaction).filter(
            IntercompanyTransaction.id == t["id"]).first()
        assert check.status == "settled"

    def test_two_simultaneous_settles_post_exactly_one_settlement(self, file_db):
        db, engine = file_db, file_db.test_engine
        t = _make_intercompany(db, 1, 2)
        txn_id = t["id"]
        assert len(_intercompany_journals(db, txn_id)) == 2
        outcomes = _run_two(engine, lambda s: settle_intercompany(txn_id, user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code == 409
        check = _fresh(engine)
        assert len(_intercompany_journals(check, txn_id)) == 4
        assert check.query(IntercompanyTransaction).filter(
            IntercompanyTransaction.id == txn_id).first().status == "settled"
        check.close()

    def test_settle_same_key_replay_returns_original_no_extra_journals(self, db):
        seller, buyer = db.test_bu, db.test_bu2
        t = _make_intercompany(db, seller.id, buyer.id)
        out1 = settle_intercompany(t["id"], user=CHAIRMAN, db=db, idempotency_key="settle-1")
        out2 = settle_intercompany(t["id"], user=CHAIRMAN, db=db, idempotency_key="settle-1")
        assert out1["id"] == out2["id"] == t["id"]
        assert out1["status"] == out2["status"] == "settled"
        assert len(_intercompany_journals(db, t["id"])) == 4


# ------------------------------------------------------------------ G-2: reverse

class TestG2ReverseClaim:
    def test_second_reverse_sequential_is_409_and_creates_no_second_reversal(self, db):
        bu = db.test_bu
        e = _make_posted_journal(db, bu.id)
        rev = reverse_journal(e["id"], user=CHAIRMAN, db=db)
        assert rev["reversal_of_id"] == e["id"]
        assert len(_reversals_of(db, e["id"])) == 1
        _expect_409(lambda: reverse_journal(e["id"], user=CHAIRMAN, db=db))
        db.rollback()
        assert len(_reversals_of(db, e["id"])) == 1
        orig = db.query(JournalEntry).filter(JournalEntry.id == e["id"]).first()
        assert orig.reversed_by_id == rev["id"]

    def test_reverse_of_draft_is_409(self, db):
        # G-2 deviation (documented): the CM-3 claim is the arbiter, so
        # the old 400 pre-checks are gone — reversing a draft is now a
        # 409 naming the status, like every other claim loser.
        bu = db.test_bu
        e = create_journal(JournalIn(
            business_unit_id=bu.id, entry_date=date(2026, 10, 20),
            description="draft", lines=[
                JournalLineIn(account_id=_account_id(db, bu.id, "1110"), debit=1000, credit=0),
                JournalLineIn(account_id=_account_id(db, bu.id, "4100"), debit=0, credit=1000),
            ]), user=CHAIRMAN, db=db)
        _expect_409(lambda: reverse_journal(e["id"], user=CHAIRMAN, db=db))
        db.rollback()
        assert len(_reversals_of(db, e["id"])) == 0

    def test_two_simultaneous_reverses_create_exactly_one_reversal(self, file_db):
        db, engine = file_db, file_db.test_engine
        e = _make_posted_journal(db, 1)
        entry_id = e["id"]
        outcomes = _run_two(engine, lambda s: reverse_journal(entry_id, user=CHAIRMAN, db=s))
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "error"]
        assert len(oks) == 1 and len(errs) == 1
        assert isinstance(errs[0][1], HTTPException) and errs[0][1].status_code == 409
        check = _fresh(engine)
        assert len(_reversals_of(check, entry_id)) == 1
        orig = check.query(JournalEntry).filter(JournalEntry.id == entry_id).first()
        revs = _reversals_of(check, entry_id)
        assert orig.reversed_by_id == revs[0].id
        check.close()

    def test_reverse_same_key_replay_returns_original_no_second_reversal(self, db):
        bu = db.test_bu
        e = _make_posted_journal(db, bu.id)
        out1 = reverse_journal(e["id"], user=CHAIRMAN, db=db, idempotency_key="rev-1")
        out2 = reverse_journal(e["id"], user=CHAIRMAN, db=db, idempotency_key="rev-1")
        assert out1["id"] == out2["id"]
        assert len(_reversals_of(db, e["id"])) == 1

    def test_reversed_by_id_unique_constraint_rejects_second_pointer(self, db):
        # DB backstop for G-2: two originals cannot point at the same
        # reversal row. NULLs stay distinct, so unreversed entries pass.
        bu = db.test_bu
        e = _make_posted_journal(db, bu.id)
        rev = reverse_journal(e["id"], user=CHAIRMAN, db=db)
        other = _make_posted_journal(db, bu.id)
        other_row = db.query(JournalEntry).filter(JournalEntry.id == other["id"]).first()
        other_row.reversed_by_id = rev["id"]
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # ...and a second posted entry with reversed_by_id NULL is fine.
        assert db.query(JournalEntry).filter(
            JournalEntry.id == other["id"]).first().reversed_by_id is None
