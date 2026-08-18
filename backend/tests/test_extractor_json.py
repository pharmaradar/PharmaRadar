"""Extractor JSON handling — the layer where a truncated model reply used to
pass as success.

The failure this guards is documented in `_call_json`'s own docstring and was
observed in production on 2026-08-08: gemini-2.5-flash is a thinking model, so
reasoning tokens come out of the same max_tokens budget, and a long prompt
leaves too little room for the JSON. It got cut mid-structure, the caller logged
and returned, the Celery task still reported success, and the PDF shipped with
an empty summary. ZALCMAN (40 insights) and SCHERPEREEL (29) failed that way
while PUJOL (10) was fine — so it looked like a content problem, not a bug.

Retry-then-give-up is therefore the contract: one bad parse is worth another
attempt, two means the caller must be told, and `None` is the signal that the
model produced nothing usable.
"""
import pytest

from app.services import extractor as ex


# ── Fence stripping ───────────────────────────────────────

def test_a_fenced_json_block_is_unwrapped():
    assert ex._strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_an_unlabelled_fence_is_unwrapped_too():
    assert ex._strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_bare_json_is_returned_unchanged():
    assert ex._strip_fences('{"a": 1}') == '{"a": 1}'


def test_prose_around_a_fence_is_discarded():
    """Models routinely add "Here is the JSON:" before the block."""
    raw = 'Here is the analysis:\n```json\n{"a": 1}\n```\nHope that helps.'
    assert ex._strip_fences(raw) == '{"a": 1}'


def test_surrounding_whitespace_is_trimmed():
    assert ex._strip_fences('  \n {"a": 1}  \n ') == '{"a": 1}'


def test_a_multiline_json_body_survives_intact():
    raw = '```json\n{\n  "insights": [\n    {"topic": "x"}\n  ]\n}\n```'
    assert '"insights"' in ex._strip_fences(raw)
    assert "```" not in ex._strip_fences(raw)


# ── Retry contract ────────────────────────────────────────

def test_valid_json_parses_on_the_first_attempt(monkeypatch):
    calls = {"n": 0}

    def once(messages, max_tokens):
        calls["n"] += 1
        return '{"insights": [{"topic": "immunotherapy"}]}'

    monkeypatch.setattr(ex, "call_pro", once)
    out = ex._call_json([], max_tokens=100, log_event="t")
    assert out["insights"][0]["topic"] == "immunotherapy"
    assert calls["n"] == 1, "a good response must not be retried"


def test_a_truncated_reply_is_retried_once(monkeypatch):
    """THE production failure: JSON cut mid-structure by the token budget."""
    calls = {"n": 0}

    def flaky(messages, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"insights": [{"topic": "immunothera'   # cut off
        return '{"insights": [{"topic": "immunotherapy"}]}'

    monkeypatch.setattr(ex, "call_pro", flaky)
    assert ex._call_json([], max_tokens=100, log_event="t") is not None
    assert calls["n"] == 2


def test_two_bad_parses_return_none_rather_than_a_partial(monkeypatch):
    """None is the caller's signal that nothing usable came back. Returning a
    half-parsed structure is what shipped an empty summary as a success."""
    monkeypatch.setattr(ex, "call_pro", lambda messages, max_tokens: "not json at all")
    assert ex._call_json([], max_tokens=100, log_event="t") is None


def test_an_llm_error_on_the_first_attempt_is_retried(monkeypatch):
    calls = {"n": 0}

    def flaky(messages, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient upstream error")
        return '{"ok": true}'

    monkeypatch.setattr(ex, "call_pro", flaky)
    assert ex._call_json([], max_tokens=100, log_event="t") == {"ok": True}
    assert calls["n"] == 2


def test_an_llm_error_on_the_second_attempt_propagates(monkeypatch):
    """The caller distinguishes "the model said nothing usable" (None) from
    "the provider is broken" (raises) — a billing outage must not be recorded
    as a KOL with no insights."""
    def always(messages, max_tokens):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ex, "call_pro", always)
    with pytest.raises(RuntimeError):
        ex._call_json([], max_tokens=100, log_event="t")


def test_the_configured_token_budget_reaches_the_model(monkeypatch):
    """max_tokens is the whole reason truncation happened; it must not be
    silently dropped between the caller and the provider."""
    seen = {}
    monkeypatch.setattr(ex, "call_pro",
                        lambda messages, max_tokens: seen.update(mt=max_tokens) or '{"a":1}')
    ex._call_json([], max_tokens=8192, log_event="t")
    assert seen["mt"] == 8192


# ── Prompt loading ────────────────────────────────────────

def test_the_extraction_prompt_exists_and_is_substantive():
    """A missing prompt file would make every extraction silently useless."""
    prompt = ex._load_prompt("extract.txt")
    assert len(prompt) > 200


def test_the_extraction_prompt_still_has_its_name_placeholder():
    """The caller substitutes {name} so the model knows whose statements it is
    reading; losing the placeholder would attribute everything to nobody."""
    assert "{name}" in ex._load_prompt("extract.txt")
