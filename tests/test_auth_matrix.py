"""Role-permission matrix tests (R-1, BOT security review 2026-11-09).

The auth model is documented in docs/auth-model.md. This test enumerates
every /api route on the live FastAPI app and asserts the role gate on each
one against that matrix, so a new route that forgets its gate — or a
router that re-implements the rule differently (the prior review's C1
bug) — fails the suite instead of shipping silently.

How it reads the gates (no HTTP calls; the venv has no httpx/TestClient):
``require_roles(...)`` returns a closure that closes over the ``roles``
tuple, so the test walks each route's dependency tree and reads that
closure. Routes that take ``get_current_user`` instead (any authenticated
user, with BU scoping enforced inside the handler via ``bu_scope`` /
``check_bu_access``) are recorded as AUTHENTICATED, not as ungated.

Run with: ENVIRONMENT=test DATABASE_URL=sqlite:// \\
    .venv/bin/python -m pytest tests/test_auth_matrix.py -q
"""

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest

from src.auth import get_current_user, get_optional_user
from src.main import app

ALL_ROLES = {"Chairman", "HO", "CEO"}

# The only routes that may be reached with no session at all.
PUBLIC_ROUTES = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/forgot-password"),
}

# Spot-check matrix: (method, path) -> exact roles allowed, from
# docs/auth-model.md. Paths use the route template (e.g. {bu_id}).
MATRIX = {
    # CEO-only group
    ("GET", "/api/ceo/dashboard"): {"CEO"},
    ("GET", "/api/ceo/monthly-target"): {"CEO"},
    ("POST", "/api/ceo/monthly-target"): {"CEO"},
    ("GET", "/api/ceo/profile"): {"CEO"},
    ("POST", "/api/ceo/submit-report"): {"CEO"},
    # HO-only group
    ("GET", "/api/ho/dashboard"): {"HO"},
    ("GET", "/api/ho/monthly-target"): {"HO"},
    ("POST", "/api/ho/monthly-target"): {"HO"},
    ("POST", "/api/ho/submit-report"): {"HO"},
    # Chairman-only: the HO monthly-target override, security log, user
    # management, password-reset approval.
    ("POST", "/api/admin/head-office/monthly-target"): {"Chairman"},
    ("GET", "/api/admin/security-log"): {"Chairman"},
    ("GET", "/api/admin/users"): {"Chairman", "HO"},
    ("POST", "/api/admin/users"): {"Chairman"},
    ("POST", "/api/admin/users/{user_id}/reset-password"): {"Chairman"},
    ("POST", "/api/admin/users/{user_id}/active"): {"Chairman"},
    ("PATCH", "/api/admin/users/{user_id}"): {"Chairman"},
    ("GET", "/api/admin/password-resets"): {"Chairman"},
    ("POST", "/api/admin/password-resets/{request_id}/approve"): {"Chairman"},
    ("POST", "/api/admin/password-resets/{request_id}/reject"): {"Chairman"},
    # Chairman + HO (shared admin, approvals)
    ("GET", "/api/admin/overview"): {"Chairman", "HO"},
    ("GET", "/api/admin/business-units"): {"Chairman", "HO"},
    ("GET", "/api/admin/report-logs"): {"Chairman", "HO"},
    # Unlock-login is the one users route HO may use (it lives on the
    # /api/admin router, prefix /api/admin).
    ("POST", "/api/admin/users/{user_id}/unlock-login"): {"Chairman", "HO"},
    # M-01: TOTP enrollment is reachable with a live session OR a
    # short-lived setup token (get_optional_user), self-disable needs a
    # session, and the admin reset is Chairman-only.
    ("POST", "/api/auth/totp/enroll"): {"OPTIONAL"},
    ("POST", "/api/auth/totp/confirm"): {"OPTIONAL"},
    ("POST", "/api/auth/totp/disable"): {"AUTHENTICATED"},
    ("POST", "/api/admin/users/{user_id}/totp-disable"): {"Chairman"},
}


def _closure_roles(fn):
    """Roles closed over by a require_roles(...) dependency, else None."""
    code = getattr(fn, "__code__", None)
    closure = getattr(fn, "__closure__", None)
    if code is None or closure is None:
        return None
    if "roles" not in code.co_freevars:
        return None
    return dict(zip(code.co_freevars, [c.cell_contents for c in closure])).get("roles")


def _walk_dependencies(dependant, roles, auth):
    for dep in dependant.dependencies or []:
        call = getattr(dep, "call", None)
        if call is not None:
            found = _closure_roles(call)
            if found is not None:
                roles.add(tuple(found))
                # A require_roles gate wraps get_current_user as its own
                # sub-dependency; do not recurse into it, or every gated
                # route would also read as AUTHENTICATED.
                continue
            if call is get_current_user:
                auth.add("AUTHENTICATED")
            elif call is get_optional_user:
                auth.add("OPTIONAL")
        _walk_dependencies(dep, roles, auth)


def _iter_api_routes(routes):
    """Yield (path, methods, route), unwrapping FastAPI's lazy
    _IncludedRouter entries (fastapi>=0.141 includes routers lazily)."""
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _iter_api_routes(original.routes)
            continue
        path = getattr(route, "path", None)
        if path and path.startswith("/api/"):
            yield path, sorted(getattr(route, "methods", []) or []), route


def collect_route_gates():
    """{(method, path): frozenset(roles) | {'AUTHENTICATED'} | {'PUBLIC'}}"""
    gates = {}
    for path, methods, route in _iter_api_routes(app.routes):
        roles, auth = set(), set()
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            _walk_dependencies(dependant, roles, auth)
        gate = set()
        for role_tuple in roles:
            gate.update(role_tuple)
        gate.update(auth)
        for method in methods:
            gates[(method, path)] = frozenset(gate)
    return gates


@pytest.fixture(scope="module")
def gates():
    return collect_route_gates()


def test_every_api_route_is_enumerated(gates):
    # Guard against the enumeration itself silently breaking (e.g. a
    # FastAPI upgrade changing _IncludedRouter): the tracker-only app has
    # 65 API routes (ERP removal 2026-11-17 cut it from 200+).
    assert len(gates) > 60, f"only {len(gates)} routes enumerated — is the unwrap broken?"


def test_matrix_spot_checks(gates):
    missing = [k for k in MATRIX if k not in gates]
    assert not missing, f"matrix routes not found on the app: {missing}"
    mismatched = {
        k: (sorted(MATRIX[k]), sorted(gates[k]))
        for k in MATRIX
        if set(gates[k]) != set(MATRIX[k])
    }
    assert not mismatched, (
        "role gate differs from docs/auth-model.md "
        "(expected -> actual): "
        + "; ".join(f"{k}: {v[0]} -> {v[1]}" for k, v in sorted(mismatched.items()))
    )


def test_no_unauthenticated_route_except_public(gates):
    public = {(m, p) for (m, p), g in gates.items() if not g}
    assert public == PUBLIC_ROUTES, (
        "routes reachable with no session (expected only login + "
        f"forgot-password): {sorted(public)}"
    )


def test_all_gates_use_known_roles(gates):
    bad = {
        (k, sorted(v - ALL_ROLES - {"AUTHENTICATED", "OPTIONAL"}))
        for k, v in gates.items()
        if v - ALL_ROLES - {"AUTHENTICATED", "OPTIONAL"}
    }
    assert not bad, f"unknown roles in gates: {bad}"


def test_ceo_routes_are_ceo_only(gates):
    offenders = {
        k: sorted(v) for k, v in gates.items()
        if k[1].startswith("/api/ceo/") and set(v) != {"CEO"}
    }
    assert not offenders, f"non-CEO-only /api/ceo routes: {offenders}"


def test_ho_routes_are_ho_only(gates):
    offenders = {
        k: sorted(v) for k, v in gates.items()
        if k[1].startswith("/api/ho/") and set(v) != {"HO"}
    }
    assert not offenders, f"non-HO-only /api/ho routes: {offenders}"


def test_ho_never_on_chairman_only_routes(gates):
    chairman_only = {
        k: sorted(v) for k, v in gates.items()
        if k[1].startswith("/api/admin/") and set(v) == {"Chairman"}
    }
    assert chairman_only, "expected at least one Chairman-only admin route"
    assert all("HO" not in v for v in chairman_only.values())
