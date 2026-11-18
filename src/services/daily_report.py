"""Shared daily-report submission machinery for the CEO and HO routers
(B-M1). The two submit-report handlers used to be 544/328-line
god-functions that each did rate-limiting, field parsing, the R2 upload,
a 4-table row-locked upsert, anomaly/backdated notifications and the
resubmit audit — with the upsert and upload logic copied between them.
The split now is:

- src/report_forms.py — parses/validates the FormData into a typed form
  (one dependency per report), including the attachment upload;
- this module — the attachment upload itself and the one shared
  4-table upsert (`upsert_daily_report`), driven by per-report row specs
  plus a small `apply` callback that assigns the report's own fields;
- the routers — thin handlers that wire the form to the upsert and do
  only their own post-submit work (CEO anomaly/backdated alerts, audit).

Nothing about the API contract changes: same FormData field names, same
validation errors, same 409 race behaviour, same Idempotency-Key-free
resubmit semantics (a resubmit overwrites the day's rows).
"""
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import assert_token_current
from src.models import User
from src.r2_storage import delete_object, extract_r2_key, upload_attachment_or_503
from src.upload_validation import content_matches_extension
from src.utils import log_audit

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "docx", "xlsx", "csv", "txt"}


def upload_report_attachment(db: Session, attachment) -> Optional[str]:
    """Validate and upload one report attachment, or None when no file was
    sent. Runs BEFORE any row is locked or written, and with NO database
    session held (m-13): get_db checks a pooled connection out before the
    handler runs, and a slow upload (up to ~90 s) used to hold that pooled
    connection AND the day's row locks. The write phase re-takes the same
    row locks, a first-submission race still surfaces as the 409 at commit
    time (whose handler deletes the loser's upload), and a resubmission's
    post-commit cleanup deletes the previous attachment in every
    interleaving — so the pre-upload locks are not needed for
    orphan-safety. The session is reused afterwards; it checks a fresh
    connection out on its next query (a new SessionLocal() here would hit
    a different database in tests that pass their own session)."""
    if attachment is None or not attachment.filename:
        return None
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
    return upload_attachment_or_503(attachment.file, attachment.filename)


@dataclass
class DailyReportRowSpec:
    """One of the four per-day rows (financial / activity / health /
    social). `filters`/`create_kwargs` are passed to filter_by / the
    constructor; `snapshot_fields` maps (row attribute, previous-dict key)
    for the resubmit-diff audit — the previous row's values, captured
    before `apply` overwrites them."""

    key: str
    cls: type
    filters: dict
    create_kwargs: dict
    snapshot_fields: tuple = ()


@dataclass
class DailyReportUpsert:
    rows: dict
    previous: dict


def upsert_daily_report(
    *,
    db: Session,
    request: Optional[Request],
    user: User,
    report_date: date,
    specs: list,
    document_url: Optional[str],
    apply: Callable[[dict, dict], Optional[str]],
    conflict_audit_action: str,
) -> DailyReportUpsert:
    """Look up (row-locked) or create the four per-day rows and let `apply`
    assign the report's fields, then commit. `apply(rows, previous)` returns
    the previous attachment URL (or None) so a replaced attachment can be
    deleted after the commit.

    M-14: no_autoflush for the lookup-or-create blocks (and the
    assert_token_current check, which queries too). Each block adds a new
    per-day row and the NEXT block's lookup would autoflush that pending
    INSERT — on a concurrent double submit the flush hits the per-day
    unique constraint OUTSIDE the try/except around db.commit() below and
    500s instead of 409ing. With autoflush off, nothing is sent to the DB
    until the commit, whose IntegrityError handler is the 409 guard. The
    lookups only ever read already-committed rows (each block queries a
    different table), so deferring the flush changes no result.
    """
    with db.no_autoflush:
        rows: dict[str, Any] = {}
        previous: dict[str, Optional[dict]] = {}
        for spec in specs:
            row = db.query(spec.cls).filter_by(**spec.filters).with_for_update().first()
            previous[spec.key] = (
                {key: getattr(row, attr) for attr, key in spec.snapshot_fields}
                if row is not None
                else None
            )
            if row is None:
                row = spec.cls(**spec.create_kwargs)
                db.add(row)
            rows[spec.key] = row
        previous_document_url = apply(rows, previous)

        # m-05 (SM-1): re-check, immediately before the commit, that the
        # session that started this (possibly slow) request is still
        # current — a logout/password change landing mid-request must not
        # still commit afterwards. No-op for callers with no token stashed
        # on the user (direct/test callers).
        assert_token_current(db, user)

    try:
        db.commit()
    except IntegrityError:
        # Two concurrent submissions (double-click, retried request) can
        # both miss each other's row on the initial lookup above and then
        # race on the same per-day unique constraint — surface a clean
        # "try again" rather than a raw 500, since a genuine resubmit is
        # otherwise allowed.
        db.rollback()
        if document_url:
            # SM-7: the loser's upload is already in R2 with no DB row
            # pointing at it — delete it instead of orphaning it (and its
            # upload-budget slot) on every double-click.
            delete_object(extract_r2_key(document_url))
        log_audit(db, user, conflict_audit_action, f"date={report_date.isoformat()}", request)
        raise HTTPException(
            status_code=409,
            detail="Another submission for today was saved at the same moment — please try again.",
        )

    if document_url and previous_document_url and previous_document_url != document_url:
        # SM-7: this resubmission replaced the previous attachment — delete
        # the orphaned object instead of leaving it in R2 forever.
        delete_object(extract_r2_key(previous_document_url))

    return DailyReportUpsert(rows=rows, previous=previous)
