"""Tests for business-unit management (create, rename, list, role guard).

The nature/provisioning tests were deleted with the ERP removal
(2026-11-17): BUCreateIn/BUUpdateIn no longer take a nature and no
chart of accounts is provisioned — a business unit is just a name
and a code.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, BusinessUnit
from src.routers.admin import (
    BUCreateIn, BUUpdateIn, create_business_unit, list_business_units,
    update_business_unit,
)

CHAIRMAN = SimpleNamespace(id=None, role="Chairman", business_unit_id=None)
CEO = SimpleNamespace(id=None, role="CEO", business_unit_id=1)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(BusinessUnit(code="BU1", name="Existing Shop"))
    session.commit()
    yield session
    session.close()


def test_create_bu_validation(db):
    with pytest.raises(HTTPException) as e1:
        create_business_unit(BUCreateIn(name="   "), user=CHAIRMAN, db=db)
    assert e1.value.status_code == 400
    with pytest.raises(HTTPException) as e2:
        create_business_unit(BUCreateIn(name="existing shop"), user=CHAIRMAN, db=db)
    assert e2.value.status_code == 400  # duplicate name (case-insensitive)
    out = create_business_unit(BUCreateIn(name="New Factory"), user=CHAIRMAN, db=db)
    assert out["code"] == "BU2"
    assert out["name"] == "New Factory"


def test_rename_bu(db):
    bu = db.query(BusinessUnit).first()
    out = update_business_unit(bu.id, BUUpdateIn(name="Renamed Shop"), user=CHAIRMAN, db=db)
    assert out["name"] == "Renamed Shop"
    with pytest.raises(HTTPException) as e1:
        update_business_unit(bu.id, BUUpdateIn(name="  "), user=CHAIRMAN, db=db)
    assert e1.value.status_code == 400
    db.add(BusinessUnit(code="BU2", name="Other Shop"))
    db.commit()
    with pytest.raises(HTTPException) as e2:
        update_business_unit(bu.id, BUUpdateIn(name="other shop"), user=CHAIRMAN, db=db)
    assert e2.value.status_code == 400  # duplicate name (case-insensitive)
    with pytest.raises(HTTPException) as e3:
        update_business_unit(999, BUUpdateIn(name="Ghost"), user=CHAIRMAN, db=db)
    assert e3.value.status_code == 404


def test_list_business_units(db):
    out = list_business_units(user=CHAIRMAN, db=db)
    assert len(out["items"]) == 1
    assert out["items"][0]["name"] == "Existing Shop"
