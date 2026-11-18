"""Monthly-report PDF and Daily Ops Excel builders (PM-m5).

Extracted verbatim from src/routers/ops.py (monthly_report / export_excel)
into this module so the router stays thin HTTP wiring: the builders take
plain data (a session, a resolved BU id) and return (bytes, filename);
the router does auth, scoping, audit and the StreamingResponse. Behaviour
is identical — the code below is the old router bodies, only the
FastAPI/StreamingResponse/audit lines removed.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import (
    ActionItem,
    BusinessUnit,
    CustomerMetric,
    DailyFinancial,
    LicenseDocument,
    MonthlyTarget,
    StaffRecord,
    User,
)
from src.routers.month_params import parse_month_or_422
from src.utils import _sanitize_excel_cell, clamp_days, today_local

# PM-4: ops exports are bounded. Unbounded tabs used to load every row
# into one workbook; now each tab is capped and the date-bearing tabs
# default to the last 90 days (pass from_date/to_date for more).
OPS_EXPORT_MAX_ROWS = 5000
OPS_EXPORT_DEFAULT_DAYS = 90


def build_monthly_report_pdf(db: Session, month: Optional[str] = None):
    """One-page-per-BU monthly summary PDF for the Chairman.

    `month` format YYYY-MM; defaults to the current month.
    Returns (pdf_bytes, filename).
    """
    from io import BytesIO

    from sqlalchemy import func

    y, m = parse_month_or_422(month)
    today = today_local()
    month_start = date(y, m, 1)
    import calendar as _cal
    month_end = date(month_start.year, month_start.month, _cal.monthrange(month_start.year, month_start.month)[1])
    days_in_month = (month_end - month_start).days + 1


    fin = (db.query(DailyFinancial.business_unit_id,
                    func.coalesce(func.sum(DailyFinancial.revenue), 0),
                    func.coalesce(func.sum(DailyFinancial.cash_flow), 0),
                    func.count(func.distinct(DailyFinancial.report_date)))
           .filter(DailyFinancial.report_date >= month_start, DailyFinancial.report_date <= month_end)
           .group_by(DailyFinancial.business_unit_id).all())
    fin_map = {row[0]: (float(row[1]), float(row[2]), int(row[3])) for row in fin}
    targets = {t.business_unit_id: t for t in db.query(MonthlyTarget).filter(MonthlyTarget.target_month == month_start).all()}
    bus = db.query(BusinessUnit).order_by(BusinessUnit.name).all()

    rows = []
    for bu in bus:
        rev, cf, rep_days = fin_map.get(bu.id, (0.0, 0.0, 0))
        t = targets.get(bu.id)
        trev = float(t.monthly_revenue_target) if t and t.monthly_revenue_target else 0.0
        ach = (rev / trev * 100) if trev else None
        rows.append({"name": bu.name, "revenue": rev, "cash_flow": cf,
                     "reports": rep_days, "target": trev, "ach": ach})

    totals = {
        "revenue": sum(r["revenue"] for r in rows),
        "cash_flow": sum(r["cash_flow"] for r in rows),
        "target": sum(r["target"] for r in rows),
    }
    totals["ach"] = (totals["revenue"] / totals["target"] * 100) if totals["target"] else None

    # Alert data
    expiring_docs = [d for d in db.query(LicenseDocument).all()
                     if d.expiry_date and 0 <= (d.expiry_date - today).days <= 30]

    from fpdf import FPDF, XPos, YPos

    def t(text):
        """Strip characters the core latin-1 PDF font can't render."""
        return (text or "").encode("latin-1", errors="replace").decode("latin-1")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    W = pdf.w - 20

    pdf.set_font("helvetica", "B", 18)
    pdf.cell(W, 10, "Business Operations - Monthly Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    label = month_start.strftime("%B %Y")
    pdf.cell(W, 6, f"{label}  |  Generated {today.isoformat()}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    def money(v):
        return f"Ks {v:,.0f}"

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Portfolio Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "", 11)
    summary = [
        ("Total revenue", money(totals["revenue"])),
        ("Total cash flow", money(totals["cash_flow"])),
        ("Revenue target", money(totals["target"]) if totals["target"] else "-"),
        ("Overall achievement", f"{totals['ach']:.1f}%" if totals["ach"] is not None else "-"),
        ("Business units", str(len(rows))),
    ]
    for k, v in summary:
        pdf.cell(60, 6.5, k)
        pdf.cell(0, 6.5, v, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Business Unit Performance", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    headers = [("BU", 52), ("Revenue", 30), ("Cash Flow", 30), ("Target", 30), ("Ach. %", 18), ("Reports", 20)]
    pdf.set_font("helvetica", "B", 10)
    for h, w in headers:
        pdf.cell(w, 7, h, border=1)
    pdf.ln()
    pdf.set_font("helvetica", "", 10)
    for r in sorted(rows, key=lambda x: -(x["ach"] or 0)):
        vals = [t(r["name"][:26]), f"{r['revenue']:,.0f}", f"{r['cash_flow']:,.0f}",
                f"{r['target']:,.0f}" if r["target"] else "-",
                f"{r['ach']:.0f}%" if r["ach"] is not None else "-",
                f"{r['reports']}/{days_in_month}"]
        for (_, w), v in zip(headers, vals):
            pdf.cell(w, 6.5, v, border=1)
        pdf.ln()
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 13)
    pdf.cell(W, 8, "Alerts", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "", 11)
    alerts = []
    no_report = [r["name"] for r in rows if r["reports"] == 0]
    if no_report:
        alerts.append(f"No reports this month: {', '.join(no_report)}")
    for d in expiring_docs:
        alerts.append(f"Document expiring soon: {d.title} - {d.expiry_date.isoformat()}")
    if not alerts:
        alerts.append("No alerts.")
    for a in alerts:
        pdf.multi_cell(W, 6, t(f"- {a}"))

    filename = f"monthly_report_{month_start.strftime('%Y-%m')}.pdf"
    return bytes(pdf.output()), filename


def build_ops_excel(db: Session, bu_id, tab: str = "actions",
                    from_date: Optional[date] = None, to_date: Optional[date] = None,
                    limit: int = OPS_EXPORT_MAX_ROWS):
    """One Daily Ops tab as an .xlsx workbook. Returns (xlsx_bytes, filename).

    `bu_id` is already resolved by the caller (bu_scope in the router):
    None means all business units. Row-capped at `limit` (max 5000);
    the actions tab defaults to the last 90 days unless from_date/to_date
    are given. Staff and customers were already capped at 500 and stay
    that way.
    """
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    bu_names = {bu.id: bu.name for bu in db.query(BusinessUnit).all()}
    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    limit = max(1, min(limit, OPS_EXPORT_MAX_ROWS))

    def date_bounds(default_days=None):
        """(start, end) datetimes for a created_at filter, or (None, None).

        default_days set → from_date defaults to today minus that many
        days; to_date always defaults to open-ended.
        """
        frm = from_date
        if frm is None and default_days is not None:
            frm = today_local() - timedelta(days=default_days)
        start = datetime.combine(frm, datetime.min.time()) if frm else None
        end = datetime.combine(to_date, datetime.max.time()) if to_date else None
        return start, end

    def apply_date_filter(q, column, default_days=None):
        start, end = date_bounds(default_days)
        if start is not None:
            q = q.filter(column >= start)
        if end is not None:
            q = q.filter(column <= end)
        return q

    def build(title, headers, rows):
        wb = Workbook()
        ws = wb.active
        ws.title = title[:31]
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        for r in rows:
            ws.append([_sanitize_excel_cell(v) if isinstance(v, str) else v for v in r])
        for col in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col) + 3
            ws.column_dimensions[col[0].column_letter].width = min(max(width, 12), 42)
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue(), f"ops_{tab}.xlsx"

    def scoped(q, model, limit):
        # The BU filter must be applied BEFORE .limit(): SQLAlchemy
        # forbids .filter() on a query that already has LIMIT applied, so
        # filtering here (after the caller already limited) 500d the CEO
        # path while the Chairman path — which never filters — worked fine
        # and hid the bug.
        if bu_id is not None:
            q = q.filter(model.business_unit_id == bu_id)
        return q.limit(limit).all()

    def user_names(ids):
        """PM-3: one IN query for many user names (was: one query per row)."""
        ids = [i for i in set(ids) if i]
        if not ids:
            return {}
        return {u.id: (u.full_name or "") for u in
                db.query(User).filter(User.id.in_(ids)).all()}

    if tab == "actions":
        q = apply_date_filter(
            db.query(ActionItem).order_by(ActionItem.created_at.desc()),
            ActionItem.created_at, OPS_EXPORT_DEFAULT_DAYS)
        rows = scoped(q, ActionItem, limit)
        names = user_names([i.assigned_to_id for i in rows])
        return build("Action Items",
                     ["Title", "BU", "Status", "Due Date", "Assigned To", "Created"],
                     [[i.title, bu_names.get(i.business_unit_id, ""), i.status,
                       i.due_date.isoformat() if i.due_date else "",
                       names.get(i.assigned_to_id, "") if i.assigned_to_id else "",
                       i.created_at.strftime("%Y-%m-%d %H:%M") if i.created_at else ""] for i in rows])

    if tab == "documents":
        q = apply_date_filter(
            db.query(LicenseDocument).order_by(LicenseDocument.expiry_date.asc()),
            LicenseDocument.expiry_date)
        rows = scoped(q, LicenseDocument, limit)
        return build("Documents",
                     ["Title", "Type", "BU", "Issue Date", "Expiry Date", "Days Left", "Notes"],
                     [[d.title, d.document_type, bu_names.get(d.business_unit_id, ""),
                       d.issue_date.isoformat() if d.issue_date else "",
                       d.expiry_date.isoformat() if d.expiry_date else "",
                       (d.expiry_date - today_local()).days if d.expiry_date else "",
                       d.notes or ""] for d in rows])

    if tab == "staff":
        q = db.query(StaffRecord).order_by(StaffRecord.report_date.desc())
        rows = scoped(q, StaffRecord, 500)
        return build("Staff",
                     ["Date", "BU", "Total", "Present", "Absent", "New Hires", "Resignations"],
                     [[s.report_date.isoformat() if s.report_date else "", bu_names.get(s.business_unit_id, ""),
                       s.total_staff, s.present, s.absent, s.new_hires, s.resignations] for s in rows])

    if tab == "customers":
        q = db.query(CustomerMetric).order_by(CustomerMetric.report_date.desc())
        rows = scoped(q, CustomerMetric, 500)
        return build("Customers",
                     ["Date", "BU", "Footfall", "Transactions", "Complaints", "Satisfaction (1-5)"],
                     [[c.report_date.isoformat() if c.report_date else "", bu_names.get(c.business_unit_id, ""),
                       c.footfall, c.transactions, c.complaints,
                       float(c.satisfaction_score) if c.satisfaction_score is not None else ""] for c in rows])

    raise HTTPException(status_code=400, detail="tab must be actions/documents/staff/customers")
