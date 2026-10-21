"""Migration-chain regression test.

Guards the bug class found on 2026-10-21: new columns existed in the
SQLAlchemy models but were missing from the Alembic migration — the suite
passed 89/89 because tests build the schema with ``create_all`` and never
run migrations, and the bug only surfaced as a 500 against a real database.

This test runs the REAL migration chain on an empty scratch DB (subprocess
``alembic upgrade head`` — no ``create_all`` anywhere) and asserts:
1. exactly one migration head;
2. every model table and every model column exists in the migrated schema;
3. a journal entry can be inserted and read back through the models
   against the migrated schema.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \
    .venv/bin/python -m pytest tests/test_migrations_chain.py
"""
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def migrated_engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("migchain") / "scratch.db"
    url = f"sqlite:///{db_path}"
    env = {
        **os.environ,
        "ENVIRONMENT": "test",
        "DATABASE_URL": url,
        "MIGRATION_DATABASE_URL": url,
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed on an empty DB:\n{result.stdout}\n{result.stderr}"
    )
    engine = create_engine(url)
    yield engine
    engine.dispose()


def test_single_migration_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"expected exactly one migration head, got {heads}"


def test_migrated_schema_matches_models(migrated_engine):
    from src.models import Base

    inspector = inspect(migrated_engine)
    db_tables = set(inspector.get_table_names())
    missing_tables, missing_columns = [], []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_tables:
            missing_tables.append(table_name)
            continue
        db_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name not in db_cols:
                missing_columns.append(f"{table_name}.{col.name}")
    assert not missing_tables, f"tables missing after migrations: {missing_tables}"
    assert not missing_columns, (
        f"columns missing after migrations (in models, not in migration chain): "
        f"{missing_columns}"
    )


def test_smoke_journal_insert(migrated_engine):
    from src.models import Account, BusinessUnit, JournalEntry, JournalLine

    db = sessionmaker(bind=migrated_engine)()
    try:
        bu = BusinessUnit(name="MigChain Smoke BU", code="MIGSMOKE")
        db.add(bu)
        db.flush()
        cash = Account(business_unit_id=bu.id, code="1110",
                       name="Cash on Hand", account_type="asset")
        sales = Account(business_unit_id=bu.id, code="4100",
                        name="Sales Revenue", account_type="income")
        db.add_all([cash, sales])
        db.flush()
        entry = JournalEntry(
            business_unit_id=bu.id, entry_no="JV-SMOKE-001",
            entry_date=date(2026, 10, 21),
            description="migration chain smoke", source="manual",
            status="posted",
        )
        db.add(entry)
        db.flush()
        db.add_all([
            JournalLine(entry_id=entry.id, account_id=cash.id,
                        debit=Decimal("1000"), credit=Decimal("0")),
            JournalLine(entry_id=entry.id, account_id=sales.id,
                        debit=Decimal("0"), credit=Decimal("1000")),
        ])
        db.commit()
        saved = db.query(JournalEntry).filter(
            JournalEntry.entry_no == "JV-SMOKE-001").one()
        assert len(saved.lines) == 2
        assert sum(l.debit for l in saved.lines) == Decimal("1000")
        assert sum(l.credit for l in saved.lines) == Decimal("1000")
    finally:
        db.close()
