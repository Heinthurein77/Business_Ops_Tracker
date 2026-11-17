"""F-2 / F-7 regression tests (BOT review 2026-11-16).

F-2: find_journal_failures() must detect
  (a) a failed quick-transfer journal (flag-only path in
      transfer_stock: stock moved, no journal posted), and
  (b) a half-posted formal transfer (send leg posted, receive leg
      failed — or vice versa), while
  (c) a fully-posted transfer (quick or formal) is not flagged.

G-1: quick transfers now post with source="quick_stock_transfer"
    (ops.py) while formal transfers keep "stock_transfer", so a
    formal transfer id can no longer mask a quick transfer of the
    same-numbered item, or vice versa — proved in both directions
    below (the review's Case A and Case B).

F-7: POST /api/accounting/journal/retry is Chairman/HO-only
    (require_admin), matching the Journal Failures page gate — an
    Accountant must not be able to re-post journals.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_journal_reconcile_f2_f7.py -q
"""
import os
from datetime import date, datetime
from decimal import Decimal

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.jobs.reconcile_journals import find_journal_failures
from src.models import (
    Account,
    Base,
    BusinessUnit,
    InventoryItem,
    JournalEntry,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    User,
)
from src.routers.ops import TransferIn, transfer_stock


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
    ])
    session.add_all([
        User(id=801, username="chairman", hashed_password="x", role="Chairman",
             business_unit_id=None, is_active=True),
        User(id=802, username="acct", hashed_password="x", role="Accountant",
             business_unit_id=1, is_active=True),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _item(db, bu_id=1, name="Widget", cost=100, stock=10):
    item = InventoryItem(business_unit_id=bu_id, name=name, sku=f"SKU-{name}",
                         unit="pcs", current_stock=stock, unit_cost=cost)
    db.add(item)
    db.flush()
    return item


def _posted(db, bu_id, source, source_id, entry_no):
    db.add(JournalEntry(business_unit_id=bu_id, entry_no=entry_no,
                        entry_date=date(2026, 11, 16), source=source,
                        source_id=source_id, status="posted"))
    db.flush()


def _quick_move(db, item, qty=3, to_bu=2, when=None):
    # Exactly what transfer_stock writes (ops.py): an "out" movement on
    # the source item with reason "Transfer to BU <id>".
    db.add(StockMovement(item_id=item.id, movement_type="out", quantity=qty,
                         reason=f"Transfer to BU {to_bu}", recorded_by_id=801,
                         created_at=when or datetime(2026, 11, 16, 10, 0, 0)))
    db.flush()


def _formal_transfer(db, status="received", cost=100, transfer_no="TR-1-0001", name="Gadget"):
    t = StockTransfer(from_business_unit_id=1, to_business_unit_id=2,
                      transfer_no=transfer_no, status=status,
                      sent_at=datetime(2026, 11, 16, 9, 0, 0))
    db.add(t)
    db.flush()
    src = _item(db, bu_id=1, name=name, cost=cost)
    db.add(StockTransferLine(transfer_id=t.id, inventory_item_id=src.id,
                             quantity=Decimal("2"), unit_cost=Decimal(str(cost))))
    db.commit()
    return t


def _provision_template(db, bu_id):
    """Retail chart of accounts for one BU (1140 inventory etc.), so
    transfer_stock can post its journals instead of taking the
    flag-only path."""
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts("retail"):
        acc = Account(business_unit_id=bu_id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        db.add(acc)
        db.flush()
        id_by_code[code] = acc.id
    db.commit()


def _by_kind(failures, kind):
    return [f for f in failures if f["kind"] == kind]


class TestQuickTransferDetection:
    def test_failed_quick_transfer_is_detected(self, db):
        item = _item(db)
        _quick_move(db, item, qty=3, to_bu=2)
        db.commit()

        failures = find_journal_failures(db)
        quick = _by_kind(failures, "quick_stock_transfer")
        assert len(quick) == 1
        f = quick[0]
        assert f["source"] == "quick_stock_transfer"
        assert f["source_id"] == item.id
        assert f["amount"] == pytest.approx(300.0)
        assert f["business_unit_id"] == 1
        # UI-visible recovery: manual journal, never re-enter the transfer.
        assert "MANUAL journal" in f["detail"]
        assert "do NOT re-enter the transfer" in f["detail"]
        assert f.get("retryable") is False
        assert f.get("recovery") == "manual_journal"

    def test_posted_quick_transfer_is_not_flagged(self, db):
        item = _item(db)
        _quick_move(db, item, qty=3, to_bu=2)
        # Quick transfers post one entry per BU side, both with
        # source_id = the source item id and source =
        # "quick_stock_transfer" (G-1 — distinct from formal transfers).
        _posted(db, 1, "quick_stock_transfer", item.id, "JV-2026-11-001")
        _posted(db, 2, "quick_stock_transfer", item.id, "JV-2026-11-002")
        db.commit()

        assert find_journal_failures(db) == []

    def test_zero_cost_quick_transfer_is_not_flagged(self, db):
        item = _item(db, cost=0)
        _quick_move(db, item, qty=3, to_bu=2)
        db.commit()

        assert find_journal_failures(db) == []

    def test_formal_transfer_movements_are_not_quick_transfers(self, db):
        # Formal send/receive write "Stock transfer <no> to/from BU …"
        # — that wording must never match the quick-transfer scan.
        t = _formal_transfer(db, status="received")
        src_item = t.lines[0].inventory_item
        db.add(StockMovement(item_id=src_item.id, movement_type="out", quantity=2,
                             reason=f"Stock transfer {t.transfer_no} to BU 2",
                             recorded_by_id=801,
                             created_at=datetime(2026, 11, 16, 9, 0, 0)))
        db.commit()

        failures = find_journal_failures(db)
        assert _by_kind(failures, "quick_stock_transfer") == []
        # The formal transfer itself IS flagged (no journal at all).
        assert [(f["source"], f["source_id"]) for f in failures] == [("stock_transfer", t.id)]


class TestFormalTransferPerLeg:
    def test_half_posted_receive_leg_missing_is_detected(self, db):
        t = _formal_transfer(db, status="received")
        _posted(db, 1, "stock_transfer", t.id, "JV-2026-11-001")  # send leg only
        db.commit()

        failures = find_journal_failures(db)
        assert len(failures) == 1
        f = failures[0]
        assert (f["source"], f["source_id"]) == ("stock_transfer", t.id)
        assert f["missing_legs"] == ["receive"]
        assert "receive" in f["detail"] and "send" in f["detail"]

    def test_half_posted_send_leg_missing_is_detected(self, db):
        t = _formal_transfer(db, status="received")
        _posted(db, 2, "stock_transfer", t.id, "JV-2026-11-002")  # receive leg only
        db.commit()

        failures = find_journal_failures(db)
        assert len(failures) == 1
        assert failures[0]["missing_legs"] == ["send"]

    def test_fully_posted_formal_transfer_is_not_flagged(self, db):
        t = _formal_transfer(db, status="received")
        _posted(db, 1, "stock_transfer", t.id, "JV-2026-11-001")
        _posted(db, 2, "stock_transfer", t.id, "JV-2026-11-002")
        db.commit()

        assert find_journal_failures(db) == []

    def test_in_transit_transfer_with_send_posted_is_not_flagged(self, db):
        # The receive leg is not due until the transfer is received.
        t = _formal_transfer(db, status="in_transit")
        _posted(db, 1, "stock_transfer", t.id, "JV-2026-11-001")
        db.commit()

        assert find_journal_failures(db) == []

    def test_in_transit_transfer_with_no_journal_is_flagged(self, db):
        t = _formal_transfer(db, status="in_transit")
        db.commit()

        failures = find_journal_failures(db)
        assert len(failures) == 1
        assert failures[0]["missing_legs"] == ["send"]


class TestQuickFormalNamespaceCollision:
    """G-1: quick transfers (source="quick_stock_transfer",
    source_id=item id) and formal transfers (source="stock_transfer",
    source_id=transfer id) live in separate id namespaces, so an id
    coincidence must not mask a failure in either direction. Both
    cases below fail on the pre-fix code (shared "stock_transfer"
    source) and pass with the fix."""

    def test_case_a_posted_formal_transfer_does_not_mask_failed_quick_transfer(self, db):
        # Item #1 has an unposted quick transfer; formal transfer #1
        # posted its send journal. The quick failure must be reported.
        item = _item(db)  # id 1
        _quick_move(db, item, qty=3, to_bu=2)
        t = _formal_transfer(db, status="in_transit")  # id 1
        assert item.id == 1 and t.id == 1
        _posted(db, 1, "stock_transfer", t.id, "JV-2026-11-001")  # send leg
        db.commit()

        failures = find_journal_failures(db)
        quick = _by_kind(failures, "quick_stock_transfer")
        assert len(quick) == 1
        assert (quick[0]["source"], quick[0]["source_id"]) == ("quick_stock_transfer", item.id)
        # The formal transfer itself is not a failure (in_transit,
        # send leg posted) — only the quick transfer is listed.
        assert _by_kind(failures, "stock_transfer") == []

    def test_case_b_posted_quick_transfer_does_not_mask_failed_formal_send_leg(self, db):
        # Formal transfer #1 fully posted (control). Formal transfer
        # #2's send leg never posted, but a quick transfer of item #2
        # posted in BU1. The send leg must be reported missing — not
        # just the receive leg.
        t1 = _formal_transfer(db, status="received")  # id 1
        _posted(db, 1, "stock_transfer", t1.id, "JV-2026-11-001")
        _posted(db, 2, "stock_transfer", t1.id, "JV-2026-11-002")
        t2 = _formal_transfer(db, status="received",
                              transfer_no="TR-1-0002", name="Gadget2")  # id 2
        t2_item_id = t2.lines[0].inventory_item_id
        assert t2.id == 2 and t2_item_id == 2
        _posted(db, 1, "quick_stock_transfer", t2_item_id, "JV-2026-11-003")
        _posted(db, 2, "stock_transfer", t2.id, "JV-2026-11-004")  # receive leg only
        db.commit()

        failures = find_journal_failures(db)
        formal = _by_kind(failures, "stock_transfer")
        assert len(formal) == 1
        assert formal[0]["source_id"] == t2.id
        assert formal[0]["missing_legs"] == ["send"]
        assert _by_kind(failures, "quick_stock_transfer") == []

    def test_quick_transfer_endpoint_posts_quick_stock_transfer_source(self, db):
        """G-1 (endpoint level): transfer_stock must post its two
        journals with source="quick_stock_transfer" — a revert to
        "stock_transfer" in ops.py fails here even though the
        reconcile-level tests above post journals directly."""
        for bu_id in (1, 2):
            _provision_template(db, bu_id)
        item = _item(db, cost=100, stock=10)
        chairman = db.query(User).filter(User.id == 801).first()
        out = transfer_stock(item.id, TransferIn(to_business_unit_id=2, quantity=3),
                             user=chairman, db=db)
        assert out["journal_posted"] is True
        entries = db.query(JournalEntry).filter(JournalEntry.source_id == item.id).all()
        assert len(entries) == 2  # one per BU side
        assert {e.source for e in entries} == {"quick_stock_transfer"}
        assert {e.business_unit_id for e in entries} == {1, 2}
        assert all(e.status == "posted" for e in entries)
        assert find_journal_failures(db) == []


class TestJournalRetryRoleGate:
    def test_retry_endpoint_is_chairman_ho_only(self):
        """F-7: the route gate on POST /api/accounting/journal/retry
        must be exactly {Chairman, HO} — the same gate as the Journal
        Failures page. Reads the require_roles closure off the live
        app, mirroring tests/test_auth_matrix.py."""
        from src.main import app

        def closure_roles(fn):
            code = getattr(fn, "__code__", None)
            closure = getattr(fn, "__closure__", None)
            if code is None or closure is None or "roles" not in code.co_freevars:
                return None
            return dict(zip(code.co_freevars,
                            [c.cell_contents for c in closure])).get("roles")

        def walk(dependant, roles):
            for dep in dependant.dependencies or []:
                call = getattr(dep, "call", None)
                if call is not None:
                    found = closure_roles(call)
                    if found is not None:
                        roles.update(found)
                        continue
                walk(dep, roles)

        def iter_routes(routes):
            for route in routes:
                original = getattr(route, "original_router", None)
                if original is not None:
                    yield from iter_routes(original.routes)
                    continue
                if getattr(route, "path", None):
                    yield route

        gates = {}
        for route in iter_routes(app.routes):
            roles = set()
            dependant = getattr(route, "dependant", None)
            if dependant is not None:
                walk(dependant, roles)
            for method in sorted(getattr(route, "methods", []) or []):
                gates[(method, route.path)] = roles

        assert gates[("POST", "/api/accounting/journal/retry")] == {"Chairman", "HO"}
