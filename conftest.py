"""Test-suite environment guard.

The suite must never depend on ambient CI/local env: Settings() defaults
ENVIRONMENT to "production" and refuses to start without DATABASE_URL, so a
pytest run without those vars dies at collection with exit 2 (seen on CI
run 44033833796: the pytest step failed in ~3s with exactly that
RuntimeError). pytest loads this conftest before collecting any test
module, so the defaults are in place before any test imports src.

Existing values are never overridden (setdefault), so the postgres-check
workflow keeps its Postgres DATABASE_URL and normal local/CI runs are
unchanged. The config-guard tests are unaffected: they run Settings() in a
subprocess with a scrubbed environment of their own.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")
