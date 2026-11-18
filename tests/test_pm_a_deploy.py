"""PM review 2026-11-18, workstream A (deploy & delivery) — regression tests.

Covers the deploy-side fixes in this pass: C-1 (migrations boot with only
the DB URL), PM-11 (build SHA on /healthz), PM-12 (no deploy-on-push),
PM-m12 (seeding lives in src.seed), PM-m26 (CI validates deploy artifacts,
pytest in requirements-dev.txt), PM-m27 (parameterised db_roles.sql),
PM-13/PM-15 (backup verification, deploy pre-flight), PM-14 (MAILTO),
PM-m8/PM-m30 (.env.example completeness), PM-9/PM-10/PM-m25/PM-m29
(runbook + backup-doc corrections).

Run with: ENVIRONMENT=test DATABASE_URL=<redacted> \\
    .venv/bin/python -m pytest tests/test_pm_a_deploy.py -q
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CURRENT_HEAD = "9c4d2e7a1f08"


def _scrubbed_env(**extra):
    """Env with only what a migrate-only job carries: no JWT secret, no
    encryption key, no ALLOWED_* — exactly the compose `migrate` service /
    DO PRE_DEPLOY job's env (C-1)."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    env = {k: v for k, v in env.items() if v}
    env.update(extra)
    return env


def _run_alembic(args, env, timeout=180):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


# --------------------------------------------------------------------- C-1

class TestMigrationNeedsOnlyDatabaseUrl:
    def test_migration_database_url_prefers_migration_url(self, monkeypatch):
        from src.config import migration_database_url

        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/pm-a-a.db")
        monkeypatch.setenv("MIGRATION_DATABASE_URL", "sqlite:////tmp/pm-a-b.db")
        assert migration_database_url() == "sqlite:////tmp/pm-a-b.db"

    def test_migration_database_url_falls_back_to_database_url(self, monkeypatch):
        from src.config import migration_database_url

        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/pm-a-a.db")
        monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
        assert migration_database_url() == "sqlite:////tmp/pm-a-a.db"

    def test_migration_database_url_missing_raises_naming_database_url(self, monkeypatch):
        from src.config import migration_database_url

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            migration_database_url()

    def test_migration_database_url_upgrades_postgres_sslmode_in_production(self, monkeypatch):
        from src.config import migration_database_url

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://user:pass@db.example.com:5432/app"
        )
        monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_SSLMODE", raising=False)
        monkeypatch.delenv("DATABASE_CA_CERT", raising=False)
        assert "sslmode=require" in migration_database_url()

    def test_alembic_heads_with_only_database_url_in_production(self, tmp_path):
        # C-1 repro, production env, migrate-service env only: no
        # JWT_SECRET_KEY, no DATA_ENCRYPTION_KEY, no ALLOWED_*.
        url = f"sqlite:///{tmp_path / 'heads.db'}"
        proc = _run_alembic(
            ["heads"],
            _scrubbed_env(ENVIRONMENT="production", DATABASE_URL=url),
        )
        assert proc.returncode == 0, proc.stderr
        assert CURRENT_HEAD in proc.stdout

    def test_alembic_upgrade_head_with_only_database_url_in_production(self, tmp_path):
        # The full chain, production env, migrate-service env only — this
        # is what failed before the fix (RuntimeError: JWT_SECRET_KEY...).
        url = f"sqlite:///{tmp_path / 'up.db'}"
        proc = _run_alembic(
            ["upgrade", "head"],
            _scrubbed_env(ENVIRONMENT="production", DATABASE_URL=url),
        )
        assert proc.returncode == 0, proc.stderr
        current = _run_alembic(
            ["current"],
            _scrubbed_env(ENVIRONMENT="production", DATABASE_URL=url),
        )
        assert CURRENT_HEAD in current.stdout

    def test_alembic_upgrade_without_database_url_still_fails_loud(self):
        proc = _run_alembic(["upgrade", "head"], _scrubbed_env())
        assert proc.returncode != 0
        assert "DATABASE_URL" in proc.stderr

    def test_alembic_env_does_not_import_full_settings(self):
        # The structural guarantee: env.py's only config import is the
        # migration-only resolver, never the Settings singleton.
        text = (ROOT / "alembic" / "env.py").read_text()
        assert "migration_database_url" in text
        assert "from src.config import settings" not in text
        assert "src.config import settings" not in text


# ------------------------------------------------------------------- PM-m12

class TestSeedLivesOutsideMain:
    def test_seed_module_owns_business_units_and_seeders(self):
        import src.seed as seed

        assert seed.BUSINESS_UNIT_NAMES[1] == "ABC Convenience Store"
        assert len(seed.BUSINESS_UNIT_NAMES) == 17
        assert callable(seed.seed_business_units)
        assert callable(seed.seed_users)

    def test_main_reexports_seeders_for_existing_callers(self):
        # Tests and src/routers/auth.py import these names from src.main;
        # the move keeps them importable (same function objects).
        import src.main as main_mod
        import src.seed as seed

        assert main_mod.seed_business_units is seed.seed_business_units
        assert main_mod.seed_users is seed.seed_users
        assert main_mod.BUSINESS_UNIT_NAMES is seed.BUSINESS_UNIT_NAMES
        assert main_mod._delete_bootstrap_password_file is seed._delete_bootstrap_password_file

    def test_main_is_assembly_only(self):
        text = (ROOT / "src" / "main.py").read_text()
        assert "BUSINESS_UNIT_NAMES = {" not in text
        assert "def seed_business_units" not in text
        assert "def seed_users" not in text


# -------------------------------------------------------------------- PM-11

class TestHealthzExposesBuildIdentity:
    def test_healthz_reports_status_and_build(self, client, monkeypatch):
        monkeypatch.setenv("GIT_SHA", "abc1234")
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["commit"] == "abc1234"
        assert isinstance(body["version"], str) and body["version"]

    def test_healthz_commit_defaults_without_git_sha(self, client, monkeypatch):
        monkeypatch.delenv("GIT_SHA", raising=False)
        r = client.get("/healthz")
        assert r.json()["commit"] == "dev"


# ------------------------------------------------------- PM-12 / PM-16 / PM-m26

class TestDeployArtifacts:
    def test_do_spec_has_no_deploy_on_push(self):
        import yaml

        spec = yaml.safe_load((ROOT / ".do" / "app.yaml").read_text())
        assert spec["services"][0]["github"]["deploy_on_push"] is False

    def test_do_spec_sets_allowed_hosts_and_origins(self):
        import yaml

        spec = yaml.safe_load((ROOT / ".do" / "app.yaml").read_text())
        envs = {e["key"]: e["value"] for e in spec["services"][0]["envs"]}
        assert envs["ALLOWED_HOSTS"]
        assert envs["ALLOWED_ORIGINS"]
        raw = (ROOT / ".do" / "app.yaml").read_text()
        assert "ALLOWED_HOSTS" in raw and "ALLOWED_ORIGINS" in raw

    def test_do_database_is_flagged_dev_tier(self):
        import yaml

        spec = yaml.safe_load((ROOT / ".do" / "app.yaml").read_text())
        assert spec["databases"][0]["production"] is False

    def test_pytest_is_in_requirements_dev(self):
        text = (ROOT / "requirements-dev.txt").read_text()
        assert "pytest==" in text

    def test_ci_validates_deploy_artifacts(self):
        tests_yml = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        assert "docker compose -f docker-compose.prod.yml config -q" in tests_yml
        assert ".do/app.yaml" in tests_yml
        assert "pip install pytest==9.1.1" not in tests_yml
        assert "requirements-dev.txt" in tests_yml

    def test_postgres_check_uses_requirements_dev(self):
        text = (ROOT / ".github" / "workflows" / "postgres-check.yml").read_text()
        assert "pip install pytest==9.1.1" not in text
        assert "requirements-dev.txt" in text

    def test_tests_yml_comment_assumes_no_deploy_path(self):
        text = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        assert "via deploy_on_push, DigitalOcean" not in text

    def test_codeowners_exists(self):
        text = (ROOT / ".github" / "CODEOWNERS").read_text()
        assert text.strip()


# ------------------------------------------------------------------- PM-m27

class TestDbRolesParameterised:
    def test_db_roles_uses_psql_variables_and_is_idempotent(self):
        text = (ROOT / "deploy" / "db_roles.sql").read_text()
        # No hard-coded database name in any statement (the word survives
        # only in the comment explaining that it is gone).
        assert "ON DATABASE defaultdb" not in text
        assert "ON DATABASE :\"db_name\"" in text
        assert "migrator_password" in text and "app_password" in text
        # CREATE ROLE is guarded by an existence check (Postgres has no
        # CREATE ROLE IF NOT EXISTS), so re-running is safe.
        assert "pg_roles" in text
        assert "ON_ERROR_STOP" in text


# ------------------------------------------------------------ PM-13 / PM-15

class TestDeployAndBackupScripts:
    def test_backup_verifies_the_dump(self):
        text = (ROOT / "scripts" / "backup.sh").read_text()
        assert "pg_restore -l" in text
        assert "verify" in text.lower()

    def test_backup_script_syntax(self):
        proc = subprocess.run(
            ["sh", "-n", str(ROOT / "scripts" / "backup.sh")],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr

    def test_deploy_preflight_runs_before_stop(self):
        text = (ROOT / "scripts" / "deploy.sh").read_text()
        preflight = text.index("docker compose -f docker-compose.prod.yml config -q")
        # The executed stop command (not the header comment's example).
        stop = text.index("$COMPOSE stop app caddy")
        assert preflight < stop
        assert "PRE_DEPLOY_SNAPSHOT" in text
        assert "pg_isready" in text

    def test_deploy_script_syntax(self):
        proc = subprocess.run(
            ["sh", "-n", str(ROOT / "scripts" / "deploy.sh")],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr


# ------------------------------------------------------------ PM-14 / PM-m8

class TestCrontabAndEnvExample:
    def test_crontab_has_mailto(self):
        text = (ROOT / "deploy" / "crontab").read_text()
        assert "MAILTO" in text
        # Only the MAILTO line is this workstream's: the scheduled_alerts
        # cron line itself is owned by workstream B (PM-2 moved it from
        # 08:30 to 16:00, when a missing report is actually a miss).
        assert "0 16 * * * flock" in text

    def test_env_example_has_all_eight_vars(self):
        text = (ROOT / ".env.example").read_text()
        for var in (
            "REPORT_EDIT_WINDOW_DAYS",
            "EDGE_EXPORT_RATE_LIMIT",
            "EDGE_EXPORT_RATE_WINDOW",
            "EDGE_WRITE_RATE_LIMIT",
            "EDGE_WRITE_RATE_WINDOW",
            "EDGE_UPLOAD_DAILY_COUNT_LIMIT",
            "EDGE_UPLOAD_DAILY_BYTES_LIMIT",
            "ALLOW_DEV_SEED",
        ):
            assert var in text, var
        assert "Render / DigitalOcean" not in text
        assert "DATABASE_CA_CERT" in text

    def test_env_example_warns_about_dev_seed(self):
        text = (ROOT / ".env.example").read_text()
        assert "ALLOW_DEV_SEED" in text
        assert "never" in text.lower()


# ------------------------------------------------- PM-9 / PM-10 / PM-m25 / PM-m29

class TestRunbookCorrections:
    def test_runbook_has_no_r2_public_base_url_instruction(self):
        text = (ROOT / "docs" / "BOT_GoLive_Runbook_2026-10-21.md").read_text()
        assert "R2_PUBLIC_BASE_URL='<public base URL" not in text
        assert "session-gated" in text

    def test_runbook_head_citation_is_not_hardcoded(self):
        text = (ROOT / "docs" / "BOT_GoLive_Runbook_2026-10-21.md").read_text()
        assert "single head a720b6b7c616" not in text
        assert "exactly one head" in text

    def test_backup_doc_head_citation_is_not_hardcoded(self):
        text = (ROOT / "docs" / "backup-recovery.md").read_text()
        assert "single head a720b6b7c616" not in text
        assert "exactly one head" in text

    def test_backup_doc_citation_points_at_go_live_runbook(self):
        text = (ROOT / "docs" / "backup-recovery.md").read_text()
        assert "§2D of the incident runbook" not in text
        assert "go-live runbook" in text

    def test_runbook_pool_and_bootstrap_facts(self):
        text = (ROOT / "docs" / "BOT_GoLive_Runbook_2026-10-21.md").read_text()
        assert "(4+4" not in text
        assert "(5+5)" in text
        assert "Chairman123!" not in text
        assert "/app/.bootstrap_chairman_password" in text
        assert "docker compose exec web" not in text
        assert "docker compose exec app" in text

    def test_backup_doc_documents_off_server_copy_check(self):
        text = (ROOT / "docs" / "backup-recovery.md").read_text()
        assert "off-server" in text
        assert "pg_restore -l" in text
