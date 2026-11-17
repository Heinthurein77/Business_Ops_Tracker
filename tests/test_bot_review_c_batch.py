"""Review-C batch (2026-11-17 code quality + security review): tests proving
the fixes in workstream C. Each test class is named for its finding.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENVIRONMENT", "test")

from src.config import settings  # noqa: E402
from src.models import Base  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return engine, session


# ---------------------------------------------------------------- M-08 / m-14 / m-15
# Deploy config is code here: the pool/concurrency balance, Caddy health
# routing and log rotation are all asserted from the checked-in files so a
# later edit cannot silently revert them.

class TestDeployConfigReviewC:
    def test_dockerfile_limit_concurrency_matches_pool(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        assert "--limit-concurrency 20" in dockerfile
        # Pool total is 10 (5+5); 20 is the documented headroom, not 100.
        assert "--limit-concurrency 100" not in dockerfile
        assert "pool is 5+5=10" in dockerfile

    def test_caddy_health_aware_routing(self):
        caddyfile = (ROOT / "Caddyfile").read_text()
        assert "health_uri /readyz" in caddyfile
        assert "health_interval" in caddyfile
        assert "reverse_proxy app:8000" in caddyfile

    def test_compose_caddy_waits_for_healthy_app(self):
        # No PyYAML in requirements.txt, so assert on the text: the caddy
        # service must depend on app with condition service_healthy.
        compose = (ROOT / "docker-compose.prod.yml").read_text()
        caddy = compose[compose.index("  caddy:"):]
        assert "depends_on:\n      app:\n        condition: service_healthy" in caddy

    def test_compose_log_rotation_on_all_services(self):
        compose = (ROOT / "docker-compose.prod.yml").read_text()
        block = ('    logging:\n      driver: json-file\n      options:\n'
                 '        max-size: "10m"\n        max-file: "3"')
        assert compose.count(block) == 3, (
            "expected the 10 MB x 3 logging block on app, caddy and migrate")
