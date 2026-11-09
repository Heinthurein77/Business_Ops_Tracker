"""Daily journal reconciliation (SC-1 / SM-9 / SM-10): list operations whose
journal never posted.

The flag-only flows (POS sales, sales/purchase returns, formal stock
transfers, expense auto-post, CEO/HO daily-report auto-post) commit the
operation first and attempt the journal in a second transaction, so a
failed journal leaves a moved record with no ledger entry and only a
response flag (`journal_posted: false` / `auto_post_failed: true`) — which
the UI now surfaces, but which still needs a daily backstop: an operation
whose books never moved must be visible to HO/Chairman until it is
re-posted (POST /api/accounting/journal/retry).

`find_journal_failures(db)` is the single source of truth, used by both
the admin view (GET /api/admin/journal-failures) and this job's main():
an operation in a posted-adjacent state with no matching *posted,
unreversed* JournalEntry by (source, source_id). Zero-amount operations
are excluded — the posters deliberately skip them (no journal is due),
so they would otherwise false-positive on every run.

Known gaps (recorded, not hidden):
- Quick (single-item) stock transfers post with source="stock_transfer"
  but source_id set to the *inventory item* id, not a transfer id, so they
  cannot be identified or re-posted by (source, source_id) and are not
  listed here. Formal transfers are covered.
- A formal transfer whose send journal posted but whose receive journal
  failed is NOT listed (one posted entry exists for the transfer id);
  the check is per transfer, not per leg. The receive leg needs its own
  source to be distinguishable — a follow-up for the re-post endpoint.
- Old stored URLs are bearer links (SM-8): rotation/re-key of pre-cutover
  objects is a separate follow-up, not part of this job.

Run once daily from cron on the server (the coordinator owns the crontab
line), e.g.:

    30 7 * * * cd ~/business-ops-tracker && docker compose -f docker-compose.prod.yml exec -T app python -m src.jobs.reconcile_journals >> ~/bot-reconcile.log 2>&1

One summary notification per Chairman/HO user per run while failures
persist (not one per failure — a bad month-end must not spam the inbox),
plus a job_heartbeats row for the dead-man check.
"""
import sys
from decimal import Decimal

from sqlalchemy.orm import Session

from src.database import SessionLocal
from src.models import (
    BusinessUnit,
    DailyFinancial,
    ExpenseRequest,
    HeadOfficeDailyFinancial,
    JournalEntry,
    JobHeartbeat,
    Notification,
    PosSale,
    PurchaseReturn,
    SalesReturn,
    StockTransfer,
    User,
)
from src.utils import utcnow

JOB_NAME = "reconcile_journals"

# (source, operation kind, human label) — the retry endpoint takes
# {"source": ..., "source_id": ...}, so every entry carries exactly that.
SOURCES = ("pos_sale", "sales_return", "purchase_return", "stock_transfer", "expense", "daily_financial", "ho_daily_financial")


def _posted_pairs(db: Session) -> set:
    """(source, source_id) pairs with at least one posted, unreversed
    journal entry. Reversed entries do not count — a resubmission that
    reversed the old entry and then failed to repost must still show up."""
    rows = (
        db.query(JournalEntry.source, JournalEntry.source_id)
        .filter(
            JournalEntry.source.in_(SOURCES),
            JournalEntry.status == "posted",
            JournalEntry.reversed_by_id == None,  # noqa: E711
            JournalEntry.source_id != None,  # noqa: E711
        )
        .all()
    )
    return {(source, source_id) for source, source_id in rows}


def _bu_names(db: Session) -> dict:
    return {bu.id: bu for bu in db.query(BusinessUnit).all()}


def _money(value) -> float:
    return float(value or 0)


def find_journal_failures(db: Session, limit: int = 200) -> list[dict]:
    """Operations in a posted-adjacent state with no matching posted
    journal. Most recent first, capped at `limit`."""
    posted = _posted_pairs(db)
    bus = _bu_names(db)
    failures: list[dict] = []

    def bu_label(bu_id):
        bu = bus.get(bu_id)
        return {"business_unit_id": bu_id, "bu_code": bu.code if bu else "-", "bu_name": bu.name if bu else "-"}

    # POS sales: completed, lines total > 0, no pos_sale journal.
    for sale in (
        db.query(PosSale).filter(PosSale.status == "completed").order_by(PosSale.id.desc()).limit(limit).all()
    ):
        total = sum(
            (Decimal(str(line.quantity or 0)) * Decimal(str(line.unit_price or 0)) for line in sale.lines),
            Decimal("0"),
        )
        if total <= 0:
            continue  # no journal is due on a zero sale (poster skips it)
        if ("pos_sale", sale.id) in posted:
            continue
        failures.append({
            "kind": "pos_sale",
            "source": "pos_sale",
            "source_id": sale.id,
            "label": f"POS sale #{sale.id}",
            "detail": f"Sale completed for Ks {total:,.0f} with no journal posted.",
            "amount": _money(total),
            "date": sale.sale_date.isoformat() if sale.sale_date else None,
            **bu_label(sale.business_unit_id),
        })

    # Sales returns: posted credit note, total > 0, no sales_return journal.
    for r in (
        db.query(SalesReturn).filter(SalesReturn.status == "posted").order_by(SalesReturn.id.desc()).limit(limit).all()
    ):
        total = sum(
            (Decimal(str(line.quantity or 0)) * Decimal(str(line.unit_price or 0)) for line in r.lines),
            Decimal("0"),
        )
        if total <= 0:
            continue
        if ("sales_return", r.id) in posted:
            continue
        failures.append({
            "kind": "sales_return",
            "source": "sales_return",
            "source_id": r.id,
            "label": f"Sales return {r.return_no}",
            "detail": f"Credit note for Ks {total:,.0f} with no journal posted.",
            "amount": _money(total),
            "date": r.return_date.isoformat() if r.return_date else None,
            **bu_label(r.business_unit_id),
        })

    # Purchase returns: posted debit note, total > 0, no purchase_return journal.
    for r in (
        db.query(PurchaseReturn).filter(PurchaseReturn.status == "posted").order_by(PurchaseReturn.id.desc()).limit(limit).all()
    ):
        total = sum(
            (Decimal(str(line.quantity or 0)) * Decimal(str(line.unit_cost or 0)) for line in r.lines),
            Decimal("0"),
        )
        if total <= 0:
            continue
        if ("purchase_return", r.id) in posted:
            continue
        failures.append({
            "kind": "purchase_return",
            "source": "purchase_return",
            "source_id": r.id,
            "label": f"Purchase return {r.return_no}",
            "detail": f"Debit note for Ks {total:,.0f} with no journal posted.",
            "amount": _money(total),
            "date": r.return_date.isoformat() if r.return_date else None,
            **bu_label(r.business_unit_id),
        })

    # Formal stock transfers: sent/received, transfer cost > 0, no
    # stock_transfer journal. (Per-transfer, not per-leg — see module
    # docstring: a failed receive leg behind a posted send leg is missed.)
    for t in (
        db.query(StockTransfer)
        .filter(StockTransfer.status.in_(("in_transit", "received")))
        .order_by(StockTransfer.id.desc())
        .limit(limit)
        .all()
    ):
        cost = sum(
            (Decimal(str(line.quantity or 0)) * Decimal(str(line.unit_cost or 0)) for line in t.lines),
            Decimal("0"),
        )
        if cost <= 0:
            continue
        if ("stock_transfer", t.id) in posted:
            continue
        failures.append({
            "kind": "stock_transfer",
            "source": "stock_transfer",
            "source_id": t.id,
            "label": f"Stock transfer {t.transfer_no}",
            "detail": f"Transfer cost Ks {cost:,.0f} with no journal posted.",
            "amount": _money(cost),
            "date": t.sent_at.date().isoformat() if t.sent_at else None,
            "business_unit_id": t.from_business_unit_id,
            "bu_code": bus[t.from_business_unit_id].code if t.from_business_unit_id in bus else "-",
            "bu_name": bus[t.from_business_unit_id].name if t.from_business_unit_id in bus else "-",
        })

    # Approved expenses: amount > 0, no expense journal. (An approved
    # expense whose category has no posting rule also lands here — the
    # books did not move either way, which is exactly what this list is
    # for.)
    for e in (
        db.query(ExpenseRequest).filter(ExpenseRequest.status == "approved").order_by(ExpenseRequest.id.desc()).limit(limit).all()
    ):
        if (e.amount or 0) <= 0:
            continue
        if ("expense", e.id) in posted:
            continue
        failures.append({
            "kind": "expense",
            "source": "expense",
            "source_id": e.id,
            "label": f"Expense: {e.title}",
            "detail": f"Approved expense of Ks {float(e.amount or 0):,.0f} with no journal posted.",
            "amount": _money(e.amount),
            "date": e.reviewed_at.date().isoformat() if e.reviewed_at else None,
            **bu_label(e.business_unit_id),
        })

    # CEO daily reports: revenue > 0, no daily_financial journal. Catches
    # both a failed auto-post and the silent no-op when the BU has no CoA.
    for df in (
        db.query(DailyFinancial).filter(DailyFinancial.revenue > 0).order_by(DailyFinancial.id.desc()).limit(limit).all()
    ):
        if ("daily_financial", df.id) in posted:
            continue
        failures.append({
            "kind": "daily_financial",
            "source": "daily_financial",
            "source_id": df.id,
            "label": f"Daily report {df.report_date.isoformat()}",
            "detail": f"Revenue Ks {float(df.revenue or 0):,.0f} reported but never posted to the journal.",
            "amount": _money(df.revenue),
            "date": df.report_date.isoformat() if df.report_date else None,
            **bu_label(df.business_unit_id),
        })

    # HO daily reports: revenue > 0, no ho_daily_financial journal.
    ho_bu = next((bu for bu in bus.values() if bu.code == "HO"), None)
    for df in (
        db.query(HeadOfficeDailyFinancial)
        .filter(HeadOfficeDailyFinancial.revenue > 0)
        .order_by(HeadOfficeDailyFinancial.id.desc())
        .limit(limit)
        .all()
    ):
        if ("ho_daily_financial", df.id) in posted:
            continue
        failures.append({
            "kind": "ho_daily_financial",
            "source": "ho_daily_financial",
            "source_id": df.id,
            "label": f"HO daily report {df.report_date.isoformat()}",
            "detail": f"HO revenue Ks {float(df.revenue or 0):,.0f} reported but never posted to the journal.",
            "amount": _money(df.revenue),
            "date": df.report_date.isoformat() if df.report_date else None,
            "business_unit_id": ho_bu.id if ho_bu else None,
            "bu_code": "HO",
            "bu_name": ho_bu.name if ho_bu else "Head Office",
        })

    # Most recent first (by date, then by id order within a kind — the
    # per-kind queries above are already id-desc, so a stable sort by date
    # keeps that order inside each day).
    failures.sort(key=lambda f: (f["date"] or "", f["source_id"]), reverse=True)
    return failures[:limit]


def _record_heartbeat(db, status: str) -> None:
    """Upsert this run into job_heartbeats (R-6 dead-man check). Best
    effort by design: a heartbeat write must never mask the job's own
    result, so any failure here is logged and swallowed."""
    try:
        db.merge(JobHeartbeat(job_name=JOB_NAME, last_run_at=utcnow(), last_status=status))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - heartbeat must not fail the job
        db.rollback()
        print(f"heartbeat update failed for {JOB_NAME}: {exc}", file=sys.stderr)


def main() -> int:
    db = SessionLocal()
    try:
        failures = find_journal_failures(db)
        print(f"operations with no posted journal: {len(failures)}")
        for f in failures:
            print(f"  - [{f['kind']}] {f['label']} ({f['bu_code']}) {f['detail']}")
        if failures:
            # One summary notification per Chairman/HO user per run while
            # failures persist — not one per failure.
            for admin in db.query(User).filter(User.role.in_(("Chairman", "HO")), User.is_active == True).all():  # noqa: E712
                db.add(Notification(
                    user_id=admin.id,
                    type="journal_reconciliation",
                    title=f"{len(failures)} operation(s) with no journal posted",
                    message=(
                        f"{len(failures)} operation(s) were completed but their journal never posted "
                        "(POS sales, returns, transfers, expenses or daily reports). Open Journal "
                        "Failures in the admin area to re-post them."
                    ),
                    link="/admin/journal-failures",
                ))
            db.commit()
        _record_heartbeat(db, "ok")
        return 0
    except Exception as exc:
        print(f"{JOB_NAME} failed: {exc}", file=sys.stderr)
        _record_heartbeat(db, "failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
