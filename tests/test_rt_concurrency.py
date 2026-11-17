"""Kept from the BOT real-time risk review, Workstream A (concurrency).

All 40 ERP concurrency tests from this file (PO/invoice/payment/
journal/payroll/serial/transfer/period-lock/idempotency claims) were
deleted with the ERP removal (2026-11-17): the endpoints and the claim
machinery they exercised no longer exist. Two generic helper tests
remain: the Yangon-date helper and the LIKE-escape helper.
"""
from src.models import _today_local
from src.routers.ops import _escape_like
from src.utils import today_local


def test_today_local_is_yangon_date():
    assert _today_local() == today_local()


def test_escape_like_escapes_wildcards():
    assert _escape_like("a_b%c\\d") == "a\\_b\\%c\\\\d"
    assert _escape_like("plain") == "plain"
