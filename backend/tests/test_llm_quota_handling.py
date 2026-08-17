"""A 429 that means "out of credits" must not be retried like a rate limit.

Providers reuse HTTP 429 for two unrelated situations and litellm maps both to
RateLimitError:

    transient   "too many requests per minute"    → backing off works
    permanent   "prepayment credits are depleted" → backing off cannot work

Conflating them cost real production time on 2026-08-17: Gemini credits ran out,
every call sat through the full 15+30+60s backoff ladder before failing, the
NVIDIA fallback was skipped because the old code re-raised all RateLimitErrors,
and classify_ae_backfill overran its 720s hard limit and had its worker
SIGKILLed. The task looked like the bug; the billing state was the bug.

These tests never touch the network — `completion` is patched — so they assert
the routing decisions, which is where the defect lived.
"""
import pytest
from litellm import RateLimitError

from app.services import llm_router
from app.services.llm_router import is_quota_exhausted

_DEPLETED = (
    'litellm.RateLimitError: GeminiException - {"error": {"code": 429, '
    '"message": "Your prepayment credits are depleted. Please go to AI Studio '
    'at https://ai.studio/projects to manage your project and billing.", '
    '"status": "RESOURCE_EXHAUSTED"}}'
)


def _rate_limit(msg: str) -> RateLimitError:
    return RateLimitError(message=msg, llm_provider="gemini", model="gemini-2.5-flash")


# ── Classification ────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    _DEPLETED,
    "Your prepayment credits are depleted.",
    '"status": "RESOURCE_EXHAUSTED"',
    "You exceeded your current quota, please check your plan and billing details",
    "insufficient_quota",
    "Insufficient credits to complete this request",
])
def test_billing_exhaustion_is_recognised(msg):
    assert is_quota_exhausted(Exception(msg)) is True


@pytest.mark.parametrize("msg", [
    "429 Too Many Requests",
    "Rate limit reached for requests per minute",
    "Please retry after 20 seconds",
    "overloaded_error: the model is temporarily overloaded",
    "",
])
def test_transient_throttling_is_not_mistaken_for_exhaustion(msg):
    """The expensive direction to get wrong: refusing to retry a recoverable
    error permanently degrades output for no reason."""
    assert is_quota_exhausted(Exception(msg)) is False


# ── Retry wiring ──────────────────────────────────────────

def test_transient_rate_limits_are_still_retried(monkeypatch):
    """Guards the tenacity wiring itself.

    `retry=` must be given a retry_if_exception(...) wrapper — a bare predicate
    receives tenacity's RetryCallState rather than the exception, so the
    isinstance check inside it is False for every error and retries silently
    stop happening everywhere. That failure is invisible without this test.
    """
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _rate_limit("429 Too Many Requests")
        return type("R", (), {"choices": [type("C", (), {
            "message": type("M", (), {"content": "ok"})()})()]})()

    monkeypatch.setattr(llm_router, "completion", flaky)
    # Don't actually sleep through the backoff ladder.
    monkeypatch.setattr(llm_router._call.retry, "sleep", lambda _s: None)

    assert llm_router._call("gemini/x", [], 0.2, 100, {}) == "ok"
    assert calls["n"] == 3, "a transient 429 should have been retried"


def test_credit_exhaustion_is_not_retried(monkeypatch):
    """The whole point: 4 doomed attempts with backoff is what blew the task
    time limits. Exhaustion must fail on the FIRST attempt."""
    calls = {"n": 0}

    def broke(**kwargs):
        calls["n"] += 1
        raise _rate_limit(_DEPLETED)

    monkeypatch.setattr(llm_router, "completion", broke)
    monkeypatch.setattr(llm_router._call.retry, "sleep", lambda _s: None)

    with pytest.raises(RateLimitError):
        llm_router._call("gemini/x", [], 0.2, 100, {})
    assert calls["n"] == 1, f"retried a depleted balance {calls['n']} times"


# ── Fallback routing ──────────────────────────────────────

def _no_settings(monkeypatch):
    monkeypatch.setattr(llm_router, "_load_settings", lambda: None)
    monkeypatch.setattr(llm_router, "_flag_exhausted", lambda *a, **k: None)
    monkeypatch.setattr(llm_router, "_clear_exhausted", lambda *a, **k: None)


def test_exhausted_primary_falls_back_to_the_secondary_provider(monkeypatch):
    """Previously ALL RateLimitErrors re-raised before the fallback could run,
    so a depleted balance took the whole platform down while a working NVIDIA
    key sat unused."""
    _no_settings(monkeypatch)
    monkeypatch.setattr(llm_router._config, "nvidia_api_key", "nv-key", raising=False)
    seen = []

    def route(model_str, *a, **kw):
        seen.append(model_str)
        if model_str.startswith("gemini/"):
            raise _rate_limit(_DEPLETED)
        return "fallback answer"

    monkeypatch.setattr(llm_router, "_call", route)
    assert llm_router._dispatch([], 0.2, 100) == "fallback answer"
    assert any(m.startswith("gemini/") for m in seen)
    assert any("llama" in m for m in seen), "never reached the fallback provider"


def test_transient_rate_limit_does_NOT_switch_providers(monkeypatch):
    """_call already backed off against this provider; swapping models mid-run
    changes the voice of the output for no benefit."""
    _no_settings(monkeypatch)
    monkeypatch.setattr(llm_router._config, "nvidia_api_key", "nv-key", raising=False)
    seen = []

    def route(model_str, *a, **kw):
        seen.append(model_str)
        raise _rate_limit("429 Too Many Requests")

    monkeypatch.setattr(llm_router, "_call", route)
    with pytest.raises(RateLimitError):
        llm_router._dispatch([], 0.2, 100)
    assert all(not m.endswith("llama-3.3-70b-instruct") for m in seen)


def test_exhaustion_without_a_fallback_key_still_raises(monkeypatch):
    """No second provider configured — surface the billing error rather than
    inventing a silent empty answer."""
    _no_settings(monkeypatch)
    monkeypatch.setattr(llm_router._config, "nvidia_api_key", "", raising=False)
    monkeypatch.setattr(llm_router, "_call",
                        lambda *a, **kw: (_ for _ in ()).throw(_rate_limit(_DEPLETED)))
    with pytest.raises(RateLimitError):
        llm_router._dispatch([], 0.2, 100)


def test_exhaustion_is_flagged_for_the_settings_page(monkeypatch):
    """The client should learn about a dead billing account from the Settings
    health panel, not from a 502 traceback in a log."""
    monkeypatch.setattr(llm_router, "_load_settings", lambda: None)
    monkeypatch.setattr(llm_router, "_clear_exhausted", lambda *a, **k: None)
    monkeypatch.setattr(llm_router._config, "nvidia_api_key", "", raising=False)
    flagged = []
    monkeypatch.setattr(llm_router, "_flag_exhausted",
                        lambda p, m: flagged.append((p, m)))
    monkeypatch.setattr(llm_router, "_call",
                        lambda *a, **kw: (_ for _ in ()).throw(_rate_limit(_DEPLETED)))

    with pytest.raises(RateLimitError):
        llm_router._dispatch([], 0.2, 100)
    assert flagged and flagged[0][0] == "gemini"


def test_a_successful_call_clears_a_stale_exhaustion_flag(monkeypatch):
    """Matches the TinyFish convention — the warning must not outlive a top-up."""
    monkeypatch.setattr(llm_router, "_load_settings", lambda: None)
    monkeypatch.setattr(llm_router, "_flag_exhausted", lambda *a, **k: None)
    cleared = []
    monkeypatch.setattr(llm_router, "_clear_exhausted", cleared.append)
    monkeypatch.setattr(llm_router, "_call", lambda *a, **kw: "fine")

    assert llm_router._dispatch([], 0.2, 100) == "fine"
    assert cleared == ["gemini"]
