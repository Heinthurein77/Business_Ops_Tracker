import os
import tempfile

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv()

_KNOWN_ENVIRONMENTS = {"production", "development", "test"}
_STRONG_SSLMODES = {"require", "verify-ca", "verify-full"}


def _csv(name: str) -> list[str]:
    return [item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip()]


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _enforce_db_tls(url: str, production: bool) -> str:
    """Normalize a Postgres connection string for SQLAlchemy + psycopg2 and
    make sure production connections are encrypted.

    In production the sslmode must be require / verify-ca / verify-full —
    anything weaker (or unset) is upgraded to `require`, and an explicit weak
    value refuses to start unless ALLOW_INSECURE_DATABASE is set (for a
    genuinely private, non-TLS link such as a docker network — never for a
    provider-hosted database). Managed providers (DigitalOcean, Neon) supply a
    CA certificate: pass its PEM text in DATABASE_CA_CERT and set
    DATABASE_SSLMODE=verify-full so the server's identity is verified too,
    not just the channel encrypted. Outside production the mode defaults to
    `prefer`, except Neon hosts, which always require it."""
    if not url:
        return url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        return url

    host = parsed.host or ""
    requested = (os.getenv("DATABASE_SSLMODE") or parsed.query.get("sslmode") or "").strip().lower()
    if production:
        if requested and requested not in _STRONG_SSLMODES:
            if not _bool("ALLOW_INSECURE_DATABASE", False):
                raise RuntimeError(
                    f"DATABASE_SSLMODE={requested!r} is not allowed in production — use require, "
                    "verify-ca or verify-full (set ALLOW_INSECURE_DATABASE=true only for a "
                    "private link you fully control)."
                )
            mode = requested
        else:
            mode = requested or "require"
    else:
        mode = requested or ("require" if "neon.tech" in host else "prefer")

    query = {"sslmode": mode}
    ca_pem = os.getenv("DATABASE_CA_CERT", "").strip()
    if ca_pem:
        path = os.path.join(tempfile.gettempdir(), "db-ca.pem")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(ca_pem + "\n")
        query["sslrootcert"] = path
    elif mode in {"verify-ca", "verify-full"} and not parsed.query.get("sslrootcert"):
        raise RuntimeError(
            f"DATABASE_SSLMODE={mode} needs the provider's CA certificate — set DATABASE_CA_CERT "
            "(PEM text) or put sslrootcert in the connection string."
        )
    return parsed.update_query_dict(query).render_as_string(hide_password=False)


class Settings:
    def __init__(self) -> None:
        # --- Core / Render ---
        # Default is "production" so a missing variable fails safe. Anything
        # other than a known value refuses to start: a typo like "prod" used
        # to silently disable every production-only safeguard (Secure
        # cookies, the JWT-secret guard, production-only seeding).
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production").strip().lower()
        if self.ENVIRONMENT not in _KNOWN_ENVIRONMENTS:
            raise RuntimeError(
                f"ENVIRONMENT={self.ENVIRONMENT!r} is not recognised — use one of "
                f"{sorted(_KNOWN_ENVIRONMENTS)}."
            )
        self.IS_PRODUCTION: bool = self.ENVIRONMENT == "production"
        self.PORT: int = int(os.getenv("PORT", "8000"))
        self.APP_TIMEZONE: str = os.getenv("APP_TIMEZONE", "Asia/Yangon")
        self.APP_NAME: str = os.getenv("APP_NAME", "Business Operations & CEO Tracking System")

        # --- Database (Neon.tech / DigitalOcean Postgres) ---
        raw_db_url = os.getenv("DATABASE_URL", "").strip()
        if not raw_db_url:
            # Local development fallback only. Production deployments on Render
            # must set DATABASE_URL to the Neon.tech Postgres connection string.
            if self.IS_PRODUCTION:
                # Silently booting a "production" deploy against local SQLite
                # would look like it's working — until the next redeploy wipes
                # the (ephemeral) filesystem and every row written that
                # session is gone. Same fail-loud principle as the
                # JWT_SECRET_KEY check below.
                raise RuntimeError(
                    "DATABASE_URL must be set in production — refusing to start "
                    "against the local SQLite fallback, whose data doesn't "
                    "survive a redeploy."
                )
            raw_db_url = "sqlite:///./local_dev.db"
        self.DATABASE_URL: str = _enforce_db_tls(raw_db_url, self.IS_PRODUCTION)
        # Schema migrations (DDL) can run as a more privileged role than the
        # web process: set MIGRATION_DATABASE_URL to the owner role's URL and
        # DATABASE_URL to a least-privilege runtime role (see
        # deploy/db_roles.sql). Unset = migrations use DATABASE_URL.
        raw_migration_url = os.getenv("MIGRATION_DATABASE_URL", "").strip()
        self.MIGRATION_DATABASE_URL: str = (
            _enforce_db_tls(raw_migration_url, self.IS_PRODUCTION) if raw_migration_url else self.DATABASE_URL
        )
        # When migrations run as a separate release step (a PRE_DEPLOY job),
        # set this to false so the web container never needs owner credentials.
        self.RUN_MIGRATIONS_ON_STARTUP: bool = _bool("RUN_MIGRATIONS_ON_STARTUP", True)

        # --- Auth / JWT ---
        _insecure_default_secret = "insecure-dev-secret-change-me-in-render-env-vars"
        # Known placeholder values that ship in this repo's own templates
        # (docker-compose.yml, .env.example, .do/app.yaml) — anyone who reads
        # this public source can sign a valid JWT with these if a deployer
        # forgets to override them, so they're rejected by name as well as
        # by the general length check below.
        _known_placeholder_secrets = {
            _insecure_default_secret,
            "local-dev-secret-not-for-production",
            "replace-with-a-long-random-secret",
        }
        self.JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", _insecure_default_secret)
        if self.IS_PRODUCTION and (
            not self.JWT_SECRET_KEY
            or self.JWT_SECRET_KEY in _known_placeholder_secrets
            or len(self.JWT_SECRET_KEY) < 32
        ):
            raise RuntimeError(
                "JWT_SECRET_KEY must be set to a unique, random value of at least 32 "
                "characters in production — refusing to start with a placeholder or "
                "weak secret."
            )
        # Pinned: a configurable algorithm is only a way to misconfigure into
        # "none" or an asymmetric algorithm confusable with the HMAC secret.
        self.JWT_ALGORITHM: str = "HS256"
        self.JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
        self.SECURE_COOKIES: bool = self.IS_PRODUCTION
        # The __Host- prefix makes browsers refuse the cookie unless it is
        # Secure, Path=/ and has no Domain — so a sibling subdomain or a plain
        # http:// response can't plant or overwrite the session cookie. Needs
        # HTTPS, hence production only.
        self.COOKIE_NAME: str = "__Host-access_token" if self.IS_PRODUCTION else "access_token"
        # How long a temporary password handed out by the Chairman stays
        # usable, so an unclaimed one can't linger as a live credential.
        self.TEMP_PASSWORD_TTL_HOURS_NEW_ACCOUNT: int = int(os.getenv("TEMP_PASSWORD_TTL_HOURS_NEW_ACCOUNT", "72"))
        self.TEMP_PASSWORD_TTL_HOURS_RESET: int = int(os.getenv("TEMP_PASSWORD_TTL_HOURS_RESET", "24"))
        # Optional: supply the first Chairman password from a secret instead
        # of having the app generate one and print it to the logs.
        self.BOOTSTRAP_CHAIRMAN_PASSWORD: str = os.getenv("BOOTSTRAP_CHAIRMAN_PASSWORD", "")

        # --- Data protection ---
        # Comma-separated Fernet keys, newest first (older ones only decrypt —
        # that is how a key rotation works). Encrypts personal profile data at
        # rest inside the database (see src/crypto.py). REQUIRED in
        # production; losing every key makes those fields unrecoverable, so
        # keep a copy outside the platform.
        self.DATA_ENCRYPTION_KEY: str = os.getenv("DATA_ENCRYPTION_KEY", "").strip()
        if self.IS_PRODUCTION and not self.DATA_ENCRYPTION_KEY:
            raise RuntimeError(
                "DATA_ENCRYPTION_KEY must be set in production — generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )

        # --- Network edge ---
        # Which header carries the real client IP. Set it to the platform's
        # authoritative header (DigitalOcean App Platform: do-connecting-ip,
        # Cloudflare: cf-connecting-ip). Unset, the client IP is taken from
        # X-Forwarded-For counting TRUSTED_PROXY_HOPS entries in from the
        # right — never the left-most entry, which the client controls.
        self.CLIENT_IP_HEADER: str = os.getenv("CLIENT_IP_HEADER", "").strip().lower()
        self.TRUSTED_PROXY_HOPS: int = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
        # Hostnames this app answers to (unset = not enforced; include
        # localhost/127.0.0.1 if you set it, the container healthcheck uses
        # 127.0.0.1) and the origins allowed to make state-changing browser
        # requests, e.g. https://app.example.com.
        self.ALLOWED_HOSTS: list[str] = _csv("ALLOWED_HOSTS")
        self.ALLOWED_ORIGINS: list[str] = _csv("ALLOWED_ORIGINS")
        # enforce | report-only | off — report-only lets you click through the
        # app watching the browser console for violations before enforcing.
        self.CSP_MODE: str = os.getenv("CSP_MODE", "enforce").strip().lower()
        # Per-caller budget for the expensive report/export endpoints,
        # enforced by the edge middleware (src/edge.py). The auth endpoints
        # have their own database-backed throttles; this one exists so a
        # single signed-in account can't hammer PDF/Excel generation.
        self.EDGE_EXPORT_RATE_LIMIT: int = int(os.getenv("EDGE_EXPORT_RATE_LIMIT", "60"))
        self.EDGE_EXPORT_RATE_WINDOW: int = int(os.getenv("EDGE_EXPORT_RATE_WINDOW", "300"))

        # --- Cloudflare R2 ---
        self.R2_ACCOUNT_ID: str = os.getenv("R2_ACCOUNT_ID", "")
        self.R2_ACCESS_KEY_ID: str = os.getenv("R2_ACCESS_KEY_ID", "")
        self.R2_SECRET_ACCESS_KEY: str = os.getenv("R2_SECRET_ACCESS_KEY", "")
        self.R2_BUCKET_NAME: str = os.getenv("R2_BUCKET_NAME", "business-ops-tracker")
        self.R2_ENDPOINT_URL: str = os.getenv("R2_ENDPOINT_URL") or (
            f"https://{self.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
            if self.R2_ACCOUNT_ID
            else ""
        )
        self.R2_PUBLIC_BASE_URL: str = os.getenv("R2_PUBLIC_BASE_URL", "")

        # --- Local file storage fallback (attachments/profile photos) ---
        # Used only when R2 isn't configured AND this isn't production — a
        # local disk write can't survive a Render redeploy (ephemeral
        # filesystem), so in production a missing R2 config should stay a
        # loud, visible failure rather than silently "working" until the
        # next deploy wipes every uploaded file. Local dev/Docker has no
        # such redeploy churn, so falling back there is safe and means
        # uploads actually work out of the box without any cloud account.
        self.LOCAL_UPLOAD_DIR: str = os.getenv("LOCAL_UPLOAD_DIR", "/app/uploads")

        # --- Business Rules ---
        self.TOTAL_BUSINESS_UNITS: int = int(os.getenv("TOTAL_BUSINESS_UNITS", "23"))


settings = Settings()
