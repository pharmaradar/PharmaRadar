"""Route-level authorization must match what the UI claims to restrict.

The gap this guards: Run History was locked to superadmins in the frontend
(SuperadminRoute in App.tsx plus a superadmin-flagged nav entry), but
`GET /api/runs/` carried no role dependency. Hiding a page only hides the link —
the data stayed readable by any signed-in user with curl. Run history exposes
operational internals (error messages, credit spend, per-target counts) that the
client's own users have no reason to see.

Asserted against the mounted app rather than by reading the source, so a
decorator that gets dropped in a refactor is caught. Dependencies are inspected
statically — no HTTP, no DB, no tokens — because the question is "is the guard
attached", not "does the guard work" (auth.py owns that).
"""
import pytest

from app.auth import require_admin, require_superadmin
from app.main import app

# Endpoints whose exposure is a deliberate decision, and the guard each must
# carry. Kept explicit: a route appearing here is a claim about who may read it.
_EXPECTED = {
    ("GET", "/api/runs/"): require_superadmin,
    ("POST", "/api/runs/stop"): require_admin,
    ("POST", "/api/runs/generate-pdfs"): require_admin,
    ("POST", "/api/runs/reset-all"): require_admin,
}


def _routes():
    out = {}
    for r in app.routes:
        for m in getattr(r, "methods", ()) or ():
            out[(m, getattr(r, "path", None))] = r
    return out


def _dependency_callables(route) -> set:
    """Every callable attached to the route's dependency tree."""
    found = set()
    for dep in getattr(route, "dependant", None).dependencies if getattr(route, "dependant", None) else []:
        if dep.call is not None:
            found.add(dep.call)
        for sub in dep.dependencies:
            if sub.call is not None:
                found.add(sub.call)
    return found


@pytest.mark.parametrize("key,guard", list(_EXPECTED.items()),
                         ids=[f"{m} {p}" for m, p in _EXPECTED])
def test_protected_route_carries_its_guard(key, guard):
    method, path = key
    route = _routes().get(key)
    assert route is not None, f"{method} {path} is not mounted — was it renamed?"
    assert guard in _dependency_callables(route), (
        f"{method} {path} lost its {guard.__name__} dependency. If this was "
        f"deliberate, the frontend gate for it must change in the same commit — "
        f"otherwise the UI hides data the API still serves."
    )


def test_runs_current_stays_readable_by_any_signed_in_user():
    """The counterpart to the fix: /current feeds the Dashboard and the Settings
    pipeline bar for EVERY user, so locking it down would break the app for
    non-superadmins. Pinned so the next person tightening this router does not
    over-apply the guard."""
    route = _routes().get(("GET", "/api/runs/current"))
    assert route is not None
    attached = _dependency_callables(route)
    assert require_superadmin not in attached
    assert require_admin not in attached


def test_every_api_route_is_behind_the_blanket_auth_gate():
    """Roles are per-endpoint, but authentication is global middleware. If the
    public allowlist ever grows, that is a decision worth failing a test over."""
    from app.main import _PUBLIC_PREFIXES

    assert _PUBLIC_PREFIXES == (
        "/api/auth/login", "/api/docs", "/api/redoc", "/api/openapi",
    ), ("the set of unauthenticated /api paths changed — confirm the new entry "
        "exposes nothing user- or client-specific")
