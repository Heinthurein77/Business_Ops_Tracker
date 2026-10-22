"""Tests for per-BU accountant accounts (in-memory SQLite).

An accountant account belongs to exactly one business unit: creating one
requires a BU, the assignment can be changed from User Management, and
the accounting API pins a BU-assigned accountant to that BU (covered in
test_accountant_scoping.py). Accountants created before this model
existed (business_unit_id NULL) keep group-wide access.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_accountant_assignment.py
"""
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, BusinessUnit, User
from src.routers.users import create_user, list_users, update_user
from src.schemas import CreateUserRequest, UpdateUserRequest


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        BusinessUnit(id=1, code="BU1", name="Shop One"),
        BusinessUnit(id=2, code="BU2", name="Shop Two"),
        User(username="boss", hashed_password="x", role="Chairman", full_name="Boss"),
    ])
    session.commit()
    yield session
    session.close()


def chairman(db):
    return db.query(User).filter(User.username == "boss").first()


def make(db, username, role, bu_id="missing"):
    payload = CreateUserRequest(username=username, full_name=username.title(),
                                role=role,
                                **({} if bu_id == "missing" else {"business_unit_id": bu_id}))
    return create_user(payload, None, Response(), chairman(db), db)


# ---------------------------------------------------------------- creation

def test_create_accountant_requires_bu(db):
    with pytest.raises(HTTPException) as e:
        make(db, "acct_a", "Accountant")
    assert e.value.status_code == 400


def test_create_accountant_invalid_bu_404(db):
    with pytest.raises(HTTPException) as e:
        make(db, "acct_a", "Accountant", bu_id=999)
    assert e.value.status_code == 404


def test_create_accountant_with_bu(db):
    out = make(db, "acct_a", "Accountant", bu_id=1)
    u = db.query(User).filter(User.username == "acct_a").first()
    assert u is not None and u.role == "Accountant" and u.business_unit_id == 1
    assert out["user_id"] == u.id
    # …and the account shows up in User Management with its BU attached.
    entries = list_users(user=chairman(db), db=db)["users"]
    entry = next(e for e in entries if e["username"] == "acct_a")
    assert entry["business_unit_id"] == 1 and entry["business_unit_name"] == "Shop One"


def test_create_ceo_and_ho_unchanged(db):
    with pytest.raises(HTTPException) as e:
        make(db, "ceo_a", "CEO")
    assert e.value.status_code == 400  # CEO still requires a BU
    out = make(db, "ho_a", "HO")
    ho = db.query(User).filter(User.username == "ho_a").first()
    assert ho.business_unit_id is None and out["ok"] is True  # HO stays group-level


# ------------------------------------------------------------------- edits

def test_update_user_moves_accountant(db):
    make(db, "acct_a", "Accountant", bu_id=1)
    u = db.query(User).filter(User.username == "acct_a").first()
    out = update_user(u.id, UpdateUserRequest(business_unit_id=2), None, chairman(db), db)
    assert out["business_unit_id"] == 2
    db.refresh(u)
    assert u.business_unit_id == 2


def test_update_user_accountant_cannot_be_unassigned(db):
    make(db, "acct_a", "Accountant", bu_id=1)
    u = db.query(User).filter(User.username == "acct_a").first()
    with pytest.raises(HTTPException) as e:
        update_user(u.id, UpdateUserRequest(business_unit_id=None), None, chairman(db), db)
    assert e.value.status_code == 400


def test_update_user_invalid_bu_404(db):
    make(db, "acct_a", "Accountant", bu_id=1)
    u = db.query(User).filter(User.username == "acct_a").first()
    with pytest.raises(HTTPException) as e:
        update_user(u.id, UpdateUserRequest(business_unit_id=999), None, chairman(db), db)
    assert e.value.status_code == 404


def test_update_user_group_role_cannot_take_bu(db):
    make(db, "ho_a", "HO")
    ho = db.query(User).filter(User.username == "ho_a").first()
    with pytest.raises(HTTPException) as e:
        update_user(ho.id, UpdateUserRequest(business_unit_id=1), None, chairman(db), db)
    assert e.value.status_code == 400


def test_update_user_unknown_user_404(db):
    with pytest.raises(HTTPException) as e:
        update_user(9999, UpdateUserRequest(business_unit_id=1), None, chairman(db), db)
    assert e.value.status_code == 404


def test_update_user_revokes_sessions(db):
    make(db, "acct_a", "Accountant", bu_id=1)
    u = db.query(User).filter(User.username == "acct_a").first()
    before = u.token_version
    update_user(u.id, UpdateUserRequest(business_unit_id=2), None, chairman(db), db)
    db.refresh(u)
    assert u.token_version > before
