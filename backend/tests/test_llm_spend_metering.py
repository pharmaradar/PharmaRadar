"""LLM spend metering for the Settings services panel.

Only OpenRouter exposes a balance endpoint. Google AI Studio, Anthropic and
NVIDIA have none — a depleted balance announces itself as a 429 traceback
mid-run, which is exactly how this platform discovered it on 2026-08-17. So
spend is metered the way TinyFish credits already are: count what WE send,
price it per model, aggregate per calendar month.

The number is deliberately labelled "metered" wherever it surfaces. It is our
consumption through our key, not the provider's invoice, and these tests pin the
distinction as much as the arithmetic.

Redis is faked — the assertions are about accounting and labelling, and a test
that needs a broker to check a multiplication would just be slower.
"""
import pytest

from app.services import provider_health as ph


class FakeRedis:
    """Enough of the redis API for the counters, including pipeline batching."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    # -- pipeline -------------------------------------------------
    def pipeline(self):
        return FakePipeline(self)

    # -- direct ops -----------------------------------------------
    def incrbyfloat(self, k, v):
        self.store[k] = str(float(self.store.get(k, 0)) + float(v))

    def incrby(self, k, v):
        self.store[k] = str(int(float(self.store.get(k, 0))) + int(v))

    def expire(self, k, ttl):
        self.expires[k] = ttl

    def mget(self, *keys):
        if len(keys) == 1 and isinstance(keys[0], (list, tuple)):
            keys = keys[0]
        return [self.store.get(k) for k in keys]


class FakePipeline:
    def __init__(self, r):
        self.r = r
        self.ops = []

    def incrbyfloat(self, k, v):
        self.ops.append(("incrbyfloat", k, v)); return self

    def incrby(self, k, v):
        self.ops.append(("incrby", k, v)); return self

    def expire(self, k, ttl):
        self.ops.append(("expire", k, ttl)); return self

    def execute(self):
        for op, k, v in self.ops:
            getattr(self.r, op)(k, v)
        self.ops = []


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(ph, "_redis", lambda: r)
    return r


# ── Pricing ───────────────────────────────────────────────

def test_cost_uses_real_per_model_pricing():
    """1M prompt + 1M completion on Flash must not come back as zero or as some
    flat guess — the whole panel is worthless if the price map is not consulted."""
    cost = ph._llm_cost_usd("gemini/gemini-2.5-flash", 1_000_000, 1_000_000)
    assert cost > 0
    # Input is cheaper than output on every current frontier model; if this flips
    # the arguments have been swapped somewhere.
    cheap = ph._llm_cost_usd("gemini/gemini-2.5-flash", 1_000_000, 0)
    dear = ph._llm_cost_usd("gemini/gemini-2.5-flash", 0, 1_000_000)
    assert cheap < dear


def test_cost_scales_linearly_with_tokens():
    one = ph._llm_cost_usd("gemini/gemini-2.5-flash", 1000, 1000)
    ten = ph._llm_cost_usd("gemini/gemini-2.5-flash", 10_000, 10_000)
    assert ten == pytest.approx(one * 10, rel=1e-6)


def test_free_local_inference_is_priced_at_zero():
    """Ollama runs locally and genuinely costs nothing. litellm KNOWS this
    (returns 0), so 0.0 here is a measurement, not a fallback."""
    assert ph._llm_cost_usd("ollama/llama3", 5000, 5000) == 0.0


@pytest.mark.parametrize("model", [
    "nvidia_nim/meta/llama-3.3-70b-instruct",
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "totally/made-up-model",
])
def test_unpriceable_model_returns_none_not_zero(model):
    """The distinction that matters on a billing panel: None means "we do not
    know what this cost", 0.0 means "this cost nothing". Showing $0.00 beside a
    provider that is actually billing us is the failure being prevented."""
    assert ph._llm_cost_usd(model, 5000, 5000) is None


@pytest.mark.parametrize("p,c", [(None, None), (0, 0), (-5, -5)])
def test_missing_or_nonsense_token_counts_do_not_explode(p, c):
    assert ph._llm_cost_usd("gemini/gemini-2.5-flash", p, c) == 0.0


# ── Accounting ────────────────────────────────────────────

def test_usage_accumulates_across_calls(fake_redis):
    for _ in range(40):
        ph.record_llm_usage("gemini", "gemini/gemini-2.5-flash", 3000, 900)
    spend = ph.llm_spend("gemini")
    assert spend["calls"] == 40
    assert spend["tokens"] == 40 * 3900
    assert spend["usd"] == pytest.approx(
        ph._llm_cost_usd("gemini/gemini-2.5-flash", 3000, 900) * 40, rel=1e-6)


def test_providers_are_metered_separately(fake_redis):
    """A shared counter would make the panel useless for deciding which key to
    top up."""
    ph.record_llm_usage("gemini", "gemini/gemini-2.5-flash", 1000, 1000)
    ph.record_llm_usage("nvidia", "nvidia_nim/meta/llama-3.3-70b-instruct", 1000, 1000)
    assert ph.llm_spend("gemini")["calls"] == 1
    assert ph.llm_spend("nvidia")["calls"] == 1
    assert ph.llm_spend("openai")["calls"] == 0


def test_counters_are_scoped_to_the_calendar_month(fake_redis):
    ph.record_llm_usage("gemini", "gemini/gemini-2.5-flash", 100, 100)
    assert any(ph._month() in k for k in fake_redis.store), \
        "spend keys are not month-scoped, so totals would never reset"


def test_counters_expire_so_redis_does_not_grow_forever(fake_redis):
    ph.record_llm_usage("gemini", "gemini/gemini-2.5-flash", 100, 100)
    assert fake_redis.expires, "no TTL set on spend counters"
    assert all(t > 31 * 24 * 3600 for t in fake_redis.expires.values()), \
        "TTL shorter than a month would discard the current month's own total"


def test_metering_never_raises_when_redis_is_down(monkeypatch):
    """Telemetry must not be able to fail the LLM call it is describing."""
    monkeypatch.setattr(ph, "_redis", lambda: None)
    ph.record_llm_usage("gemini", "gemini/gemini-2.5-flash", 100, 100)  # must not raise
    assert ph.llm_spend("gemini") == {"usd": 0.0, "calls": 0, "tokens": 0, "unpriced": 0}


# ── Presentation ──────────────────────────────────────────

def test_used_provider_is_labelled_as_metered(fake_redis):
    """"metered" is load-bearing wording: without it the client reads this as
    the provider's bill and trusts it for budgeting."""
    ph.record_llm_usage("gemini", "gemini/gemini-2.5-flash", 3000, 900)
    row = ph._base("gemini", "Gemini", True)
    ph._attach_spend(row, "gemini")
    assert "metered" in row["usage_label"]
    assert row["spend_calls"] == 1


def test_unused_provider_shows_nothing_rather_than_zero(fake_redis):
    """A configured-but-unused key showing "$0.00" reads as a measurement.
    It is an absence of data, and the panel should say so by staying quiet."""
    row = ph._base("openai", "OpenAI", True)
    ph._attach_spend(row, "openai")
    assert row["usage_label"] is None
    assert row["spend_usd"] is None
    assert row["usage_usd"] is None


def test_sub_cent_spend_is_not_rounded_away(fake_redis):
    """Early in a month real spend is fractions of a cent; showing $0.00 would
    look like the meter was broken."""
    ph.record_llm_usage("gemini", "gemini/gemini-2.5-flash", 100, 50)
    row = ph._base("gemini", "Gemini", True)
    ph._attach_spend(row, "gemini")
    assert "$0.00 " not in row["usage_label"]


def test_authoritative_balance_is_not_overwritten_by_our_estimate(fake_redis):
    """OpenRouter reports a real balance. Ours is an estimate; the real number
    must win where both exist."""
    ph.record_llm_usage("openrouter", "openrouter/x", 1000, 1000)
    row = ph._base("openrouter", "OpenRouter", True)
    row["usage_usd"] = 12.34          # as _openrouter_usage would have set it
    ph._attach_spend(row, "openrouter")
    assert row["usage_usd"] == 12.34
    assert row["spend_usd"] is not None   # ours still recorded alongside


# ── Router wiring ─────────────────────────────────────────

def test_router_meters_with_the_app_provider_id_not_the_model_prefix(monkeypatch):
    """The trap: _model_string renames providers (nvidia -> "nvidia_nim/",
    vertex -> "vertex_ai/") and gives OpenAI/Anthropic no prefix at all. Deriving
    the id from model_str would file spend under "gpt-4o" and never line up with
    the health row it is meant to annotate.
    """
    from app.services import llm_router

    recorded = {}
    monkeypatch.setattr(
        "app.services.provider_health.record_llm_usage",
        lambda **kw: recorded.update(kw))

    usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()
    resp = type("R", (), {"usage": usage, "choices": [
        type("C", (), {"message": type("M", (), {"content": "hi"})()})()]})()
    monkeypatch.setattr(llm_router, "completion", lambda **kw: resp)

    llm_router._call("nvidia_nim/meta/llama-3.3-70b-instruct", [], 0.2, 50, {}, "nvidia")
    assert recorded["provider"] == "nvidia"
    assert recorded["prompt_tokens"] == 10
    assert recorded["completion_tokens"] == 5


def test_router_does_not_meter_a_response_without_usage(monkeypatch):
    """Some providers omit usage. Recording a zero-token call would understate
    real spend while inflating the call count."""
    from app.services import llm_router

    calls = []
    monkeypatch.setattr("app.services.provider_health.record_llm_usage",
                        lambda **kw: calls.append(kw))
    resp = type("R", (), {"usage": None, "choices": [
        type("C", (), {"message": type("M", (), {"content": "hi"})()})()]})()
    monkeypatch.setattr(llm_router, "completion", lambda **kw: resp)

    llm_router._call("gemini/x", [], 0.2, 50, {}, "gemini")
    assert calls == []


# ── Response contract ─────────────────────────────────────

def test_every_provider_row_declares_the_spend_fields():
    """Present even when unused, so the response shape does not change depending
    on whether a key happened to be called. The frontend types them optional,
    but a row that sometimes omits them makes every consumer defensive."""
    row = ph._base("x", "X", True)
    for field in ("spend_usd", "spend_calls", "spend_tokens"):
        assert field in row, f"{field} missing from the provider row contract"


def test_bundle_cache_key_was_bumped_past_the_pre_spend_version():
    """The bundle is cached 5 min in Redis and survives deploys. Reusing the key
    after changing a row's shape serves the OLD shape to clients running the NEW
    UI — the spend panel rendered empty for minutes after release because of
    exactly that. Any future field change must bump this again.
    """
    assert ph._BUNDLE_CACHE_KEY.endswith(("v4", "v5", "v6", "v7", "v8", "v9")), (
        f"{ph._BUNDLE_CACHE_KEY} looks un-bumped — a row shape change needs a new "
        f"cache version or clients keep the stale payload until it expires"
    )


# ── Priced vs unpriced ────────────────────────────────────

def test_provider_with_no_price_data_reports_volume_not_dollars(fake_redis):
    """NVIDIA NIM and most OpenRouter models cannot be priced. The panel should
    say how much we used and admit it cannot cost it."""
    for _ in range(7):
        ph.record_llm_usage("nvidia", "nvidia_nim/meta/llama-3.3-70b-instruct", 500, 200)
    row = ph._base("nvidia", "NVIDIA NIM", True)
    ph._attach_spend(row, "nvidia")
    assert row["usage_label"] == "7 calls · no price data"
    assert "$" not in row["usage_label"]
    assert row["usage_usd"] is None, "must not imply a measured $0.00"


def test_mixed_priced_and_unpriced_calls_disclose_the_gap(fake_redis):
    """A total that silently covers only some calls reads as complete."""
    ph.record_llm_usage("openrouter", "gemini/gemini-2.5-flash", 1000, 500)
    ph.record_llm_usage("openrouter", "openrouter/meta-llama/llama-3.3-70b-instruct", 1000, 500)
    row = ph._base("openrouter", "OpenRouter", True)
    ph._attach_spend(row, "openrouter")
    assert "unpriced" in row["usage_label"], row["usage_label"]
    assert ph.llm_spend("openrouter")["unpriced"] == 1


def test_free_local_provider_still_shows_a_dollar_total(fake_redis):
    """Ollama is priced (at zero) rather than unpriceable, so it keeps the
    normal label — $0.00 is the honest answer for local inference."""
    ph.record_llm_usage("ollama", "ollama/llama3", 1000, 1000)
    row = ph._base("ollama", "Ollama", True)
    ph._attach_spend(row, "ollama")
    assert "no price data" not in row["usage_label"]
    assert ph.llm_spend("ollama")["unpriced"] == 0


def test_unpriced_calls_do_not_inflate_the_dollar_total(fake_redis):
    ph.record_llm_usage("nvidia", "nvidia_nim/meta/llama-3.3-70b-instruct", 9_000_000, 9_000_000)
    assert ph.llm_spend("nvidia")["usd"] == 0.0
    assert ph.llm_spend("nvidia")["calls"] == 1
