"""Tests for business-unit management (create with nature + provisioning,
rename, nature assignment, role guard)."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Account, Base, BusinessUnit
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


def test_create_bu_provisions_from_nature(db):
    out = create_business_unit(BUCreateIn(name="New Factory", nature="production"),
                               user=CHAIRMAN, db=db)
    assert out["code"] == "BU2"
    assert out["nature"] == "production"
    assert out["nature_label"] == "Production / Manufacturing"
    assert out["has_books"] is True
    codes = {a.code for a in db.query(Account).filter(Account.business_unit_id == out["id"]).all()}
    assert {"1310", "1320", "1330"} <= codes  # production template accounts


def test_create_bu_validation(db):
    with pytest.raises(HTTPException) as e1:
        create_business_unit(BUCreateIn(name="X", nature="spaceship"), user=CHAIRMAN, db=db)
    assert e1.value.status_code == 400
    with pytest.raises(HTTPException) as e2:
        create_business_unit(BUCreateIn(name="existing shop", nature="retail"), user=CHAIRMAN, db=db)
    assert e2.value.status_code == 400  # duplicate name (case-insensitive)


def test_rename_and_nature_assignment(db):
    bu = db.query(BusinessUnit).first()
    out = update_business_unit(bu.id, BUUpdateIn(name="Renamed Shop"), user=CHAIRMAN, db=db)
    assert out["name"] == "Renamed Shop"
    # assigning a nature to a BU without books provisions the chart
    out = update_business_unit(bu.id, BUUpdateIn(nature="real_estate"), user=CHAIRMAN, db=db)
    assert out["nature"] == "real_estate" and out["has_books"] is True
    assert "created from" in out["note"]
    count = db.query(Account).filter(Account.business_unit_id == bu.id).count()
    # changing nature again keeps the existing chart (label only)
    out = update_business_unit(bu.id, BUUpdateIn(nature="retail"), user=CHAIRMAN, db=db)
    assert db.query(Account).filter(Account.business_unit_id == bu.id).count() == count
    assert "unchanged" in out["note"]


def test_list_includes_natures_catalogue(db):
    out = list_business_units(user=CHAIRMAN, db=db)
    assert len(out["items"]) == 1
    assert len(out["natures"]) == 13
