"""Production config guards (T-3): prod boot must refuse bad config.

Settings are module-global, so each case runs `Settings()` in a fresh
subprocess with a scrubbed environment (no repo .env is loaded: the
subprocess runs with cwd=tmp_path, and every variable Settings() reads
for these branches is set explicitly or removed).

Covers every fail-loud branch in src/config.py: unknown ENVIRONMENT,
missing DATABASE_URL in production, weak/placeholder JWT_SECRET_KEY,
missing DATA_ENCRYPTION_KEY, weak DATABASE_SSLMODE, and verify-full
without a CA certificate — plus the good-config and non-prod cases
that must still boot.
"""
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Everything Settings() consults for the branches under test. Stripped
# from the inherited environment so the pytest invocation's own
# ENVIRONMENT=test / DATABASE_URL=sqlite:// can never leak in.
_SCRUBBED = (
    "ENVIRONMENT", "DATABASE_URL", "MIGRATION_DATABASE_URL",
    "JWT_SECRET_KEY", "DATA_ENCRYPTION_KEY", "DATABASE_SSLMODE",
    "DATABASE_CA_CERT", "ALLOW_INSECURE_DATABASE",
)

_GOOD_PROD = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql://bot_app:secret@db.example.com:5432/app",
    # 40 chars, not a known placeholder.
    "JWT_SECRET_KEY": "prod-jwt-secret-that-is-long-enough-0123",
    # Any non-empty value passes the presence check (Fernet validity is
    # checked where the key is used, not at boot).
    "DATA_ENCRYPTION_KEY": "test-only-not-a-real-fernet-key",
}


def _run_settings(extra_env, tmp_path):
    """Run Settings() in a subprocess; returns (returncode, stdout, stderr)."""
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED}
    env.update(extra_env)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c",
         "from src.config import Settings; s = Settings(); print('OK', s.ENVIRONMENT, s.DATABASE_URL)"],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _prod(**overrides):
    env = dict(_GOOD_PROD)
    env.update(overrides)
    return env


def _assert_refused(extra_env, tmp_path, needle):
    code, out, err = _run_settings(extra_env, tmp_path)
    assert code != 0, f"prod boot unexpectedly accepted {extra_env}: {out}"
    assert needle in err, f"expected {needle!r} in stderr, got: {err}"


class TestProdRefusals:
    def test_missing_database_url_refused(self, tmp_path):
        env = _prod()
        del env["DATABASE_URL"]
        _assert_refused(env, tmp_path, "DATABASE_URL must be set in production")

    def test_weak_jwt_secret_refused(self, tmp_path):
        _assert_refused(_prod(JWT_SECRET_KEY="too-short"), tmp_path, "JWT_SECRET_KEY")

    def test_placeholder_jwt_secret_refused(self, tmp_path):
        _assert_refused(
            _prod(JWT_SECRET_KEY="insecure-dev-secret-change-me-in-render-env-vars"),
            tmp_path, "JWT_SECRET_KEY")

    def test_missing_jwt_secret_refused(self, tmp_path):
        # No JWT_SECRET_KEY at all: the built-in insecure default is a
        # known placeholder, so prod still refuses.
        env = _prod()
        del env["JWT_SECRET_KEY"]
        _assert_refused(env, tmp_path, "JWT_SECRET_KEY")

    def test_missing_data_encryption_key_refused(self, tmp_path):
        env = _prod()
        del env["DATA_ENCRYPTION_KEY"]
        _assert_refused(env, tmp_path, "DATA_ENCRYPTION_KEY")

    def test_weak_database_sslmode_refused(self, tmp_path):
        _assert_refused(_prod(DATABASE_SSLMODE="prefer"), tmp_path, "DATABASE_SSLMODE")
        _assert_refused(_prod(DATABASE_SSLMODE="disable"), tmp_path, "DATABASE_SSLMODE")

    def test_weak_sslmode_allowed_only_with_explicit_opt_out(self, tmp_path):
        # The documented escape hatch (private link only) still boots.
        code, out, _ = _run_settings(
            _prod(DATABASE_SSLMODE="prefer", ALLOW_INSECURE_DATABASE="true"), tmp_path)
        assert code == 0, out

    def test_verify_full_without_ca_cert_refused(self, tmp_path):
        _assert_refused(_prod(DATABASE_SSLMODE="verify-full"), tmp_path, "DATABASE_CA_CERT")

    def test_unknown_environment_refused(self, tmp_path):
        _assert_refused(_prod(ENVIRONMENT="staging"), tmp_path, "ENVIRONMENT")
        _assert_refused(_prod(ENVIRONMENT="prod"), tmp_path, "ENVIRONMENT")

    # m-07 (minor): a typo'd integer env var used to crash startup with a
    # bare ValueError; _int() names the variable instead.
    def test_non_integer_port_refused_with_named_error(self, tmp_path):
        _assert_refused(_prod(PORT="eight-thousand"), tmp_path, "PORT must be an integer")

    def test_non_integer_total_business_units_refused(self, tmp_path):
        _assert_refused(
            _prod(TOTAL_BUSINESS_UNITS="twenty-three"), tmp_path,
            "TOTAL_BUSINESS_UNITS must be an integer")


class TestGoodConfigsBoot:
    def test_good_production_config_boots(self, tmp_path):
        code, out, err = _run_settings(_prod(), tmp_path)
        assert code == 0, err
        assert out.startswith("OK production")
        # No sslmode was requested: production defaults to require.
        assert "sslmode=require" in out

    def test_strong_sslmode_boots(self, tmp_path):
        code, out, err = _run_settings(
            _prod(DATABASE_SSLMODE="require"), tmp_path)
        assert code == 0, err
        assert "sslmode=require" in out

    def test_test_environment_boots_without_database_url(self, tmp_path):
        # The SQLite fallback is dev/test only; production refuses it.
        code, out, err = _run_settings({"ENVIRONMENT": "test"}, tmp_path)
        assert code == 0, err
        assert "sqlite" in out
