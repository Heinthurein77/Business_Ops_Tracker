"""Tests for ERP Phase 5 HR & payroll (in-memory SQLite).

Run with: ENVIRONMENT=test .venv/bin/python -m pytest tests/test_hr_payroll.py
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.accounting_templates import template_accounts
from src.models import Account, Base, BusinessUnit, JournalEntry
from src.routers.ops import (
    EmployeeIn, LeaveIn, PayrollCreate, PayrollLineUpdate,
    approve_payroll, create_employee, create_leave, create_payroll,
    pay_payroll, review_leave, update_payroll_line, LeaveReview,
)

CHAIRMAN = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    bu = BusinessUnit(name="Store", code="ST1")
    session.add(bu)
    session.commit()
    id_by_code = {}
    for code, name, acc_type, parent_code, is_header in template_accounts("retail"):
        acc = Account(business_unit_id=bu.id, code=code, name=name,
                      account_type=acc_type, is_header=is_header, is_system=True,
                      parent_id=id_by_code.get(parent_code) if parent_code else None)
        session.add(acc)
        session.flush()
        id_by_code[code] = acc.id
    session.commit()
    session.test_bu = bu
    yield session
    session.close()


def balance(db, code):
    acc = db.query(Account).filter(Account.code == code).first()
    total = Decimal("0")
    for e in db.query(JournalEntry).filter(JournalEntry.status == "posted").all():
        for l in e.lines:
            if l.account_id == acc.id:
                total += (l.debit or 0) - (l.credit or 0)
    return total


def add_emp(db, name, salary):
    return create_employee(EmployeeIn(business_unit_id=db.test_bu.id, name=name,
                                      base_salary=salary), user=CHAIRMAN, db=db)


def test_payroll_lifecycle_posts_journal(db):
    add_emp(db, "Aung Aung", 500000)
    add_emp(db, "Su Su", 300000)
    run = create_payroll(PayrollCreate(business_unit_id=db.test_bu.id, period="2026-10"),
                         user=CHAIRMAN, db=db)
    assert run["total_gross"] == 800000
    assert run["total_net"] == 800000
    line = run["lines"][0]
    run = update_payroll_line(run["id"], line["id"],
                              PayrollLineUpdate(allowances=50000, deductions=20000),
                              user=CHAIRMAN, db=db)
    assert run["total_gross"] == 850000
    assert run["total_deductions"] == 20000
    assert run["total_net"] == 830000
    approve_payroll(run["id"], user=CHAIRMAN, db=db)
    pay_payroll(run["id"], user=CHAIRMAN, db=db)
    assert balance(db, "6200") == 850000   # Dr salaries (gross)
    assert balance(db, "1110") == -830000  # Cr cash (net)
    assert balance(db, "2120") == -20000   # Cr accrued deductions


def test_leave_flow(db):
    emp = add_emp(db, "May May", 400000)
    lv = create_leave(LeaveIn(employee_id=emp["id"], leave_type="annual",
                              start_date="2026-11-02", end_date="2026-11-04"),
                      user=CHAIRMAN, db=db)
    assert lv["days"] == 3
    assert lv["status"] == "pending"
    out = review_leave(lv["id"], LeaveReview(decision="approved"), user=CHAIRMAN, db=db)
    assert out["status"] == "approved"
