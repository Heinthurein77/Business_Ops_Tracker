"""Kept from the BOT review ops/modules batch (workstream B).

All ERP tests from this batch (PO/invoice/payroll/advances/POS/FEFO/
transfers/leases/production/supplier/leave) were deleted with the ERP
removal (2026-11-17). One generic test remains: Excel cell sanitising
in src/utils.py, used by the tracker exports.
"""
from src.utils import _sanitize_excel_cell


def test_m3_excel_cell_sanitised():
    assert _sanitize_excel_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert _sanitize_excel_cell("+2+2") == "'+2+2"
    assert _sanitize_excel_cell("plain text") == "plain text"
    assert _sanitize_excel_cell(None) is None
