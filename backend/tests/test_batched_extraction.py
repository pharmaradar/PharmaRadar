"""Batched extraction — the platform's largest single LLM saving.

Extraction is one call per post: ~23 per target, ~1,160 for a 50-KOL run, and
the dominant consumer of a prepaid Gemini balance. Batching three posts into one
call cuts that threefold.

The design rule everything here pins: batching is a pure optimisation. On any
surprise — unparseable reply, missing post, a result set that does not cover
every post asked for — extract_batch returns None and the caller falls back to
the proven per-post path. It can save calls; it must never lose insights.

Batch size is deliberately 3, not the 15 the AE classifier uses: that emits one
boolean per item, while extraction emits several structured insights per post,
and _call_json documents what an oversized reply does on a thinking model.
"""
import pytest

from app.services import extractor as ex
from app.services.extractor import ExtractorService


def test_the_batch_is_small_enough_to_survive_the_token_budget():
    """A bigger batch truncates the reply mid-JSON and silently yields empty
    summaries — the ZALCMAN/SCHERPEREEL failure _call_json was written for."""
    assert 2 <= ex._BATCH_SIZE <= 5
    assert ex._BATCH_MAX_TOKENS >= 8192


def test_the_prompt_numbers_every_post_and_asks_for_one_entry_each():
    msgs = ex._batch_prompt("RULES", [(1, "GIRARD NICOLAS", "first content"),
                                      (2, "BESSE BENJAMIN", "second content")])
    body = msgs[-1]["content"]
    assert "=== POST [1] ===" in body and "=== POST [2] ===" in body
    assert "GIRARD NICOLAS" in body and "BESSE BENJAMIN" in body
    assert '"results"' in body


def test_the_prompt_tells_the_model_to_keep_posts_independent():
    """Two KOLs in one call must not have their statements attributed to each
    other — the worst possible failure for this feature."""
    msgs = ex._batch_prompt("RULES", [(1, "A", "x"), (2, "B", "y")])
    assert "independently" in msgs[-1]["content"]
    assert "do not merge" in msgs[-1]["content"].lower()


def test_the_analysis_rules_are_carried_through_unchanged():
    msgs = ex._batch_prompt("THE ANALYSIS RULES", [(1, "A", "x"), (2, "B", "y")])
    assert msgs[0]["content"] == "THE ANALYSIS RULES"


# ── Falling back ──────────────────────────────────────────

class _Ctx:
    def increment_llm_calls(self): pass


def _service(monkeypatch, parsed, posts=("a", "b")):
    """Patch the LLM and the DB reads so only the routing logic is exercised."""
    svc = ExtractorService()
    monkeypatch.setattr(ex, "_call_json", lambda *a, **k: parsed)
    monkeypatch.setattr(ex, "_load_prompt", lambda name: "RULES {name}")
    return svc


def test_a_single_post_never_uses_the_batch_path(monkeypatch):
    """One post in a batch wrapper is the same call with extra prompt overhead."""
    svc = _service(monkeypatch, {"results": {"1": {}}})
    assert svc.extract_batch(post_ids=[1], ctx=_Ctx()) is None


@pytest.mark.parametrize("results", [
    None,                                   # unparseable reply
    [],                                     # wrong shape
    {},                                     # empty
    {"1": {"insights": []}},                # covers post 1 but not post 2
    {"1": {}, "2": "not a dict"},           # post 2 malformed
    {"2": {}, "3": {}},                     # right count, wrong numbering
])
def test_a_reply_not_covering_every_post_is_rejected(results):
    """THE dangerous case. A partial reply would persist nothing for the missing
    posts, and they would never be retried — the task only looks for posts with
    NO insights at all, and these would have none for a different reason.

    Tested through batch_analyses directly: extract_batch returns early when a
    post id is not in the database, which made an earlier version of this test
    pass without ever reaching the validation."""
    assert ex.batch_analyses(results, [10, 11]) is None


def test_a_complete_reply_is_paired_with_its_posts():
    paired = ex.batch_analyses({"1": {"insights": ["a"]}, "2": {"insights": []}}, [10, 11])
    assert paired == [(10, {"insights": ["a"]}), (11, {"insights": []})]


def test_integer_keys_are_accepted_as_well_as_strings():
    """json.loads gives string keys, but a model or a future parser may not."""
    assert ex.batch_analyses({1: {}, 2: {}}, [10, 11]) is not None


class _FakePost:
    def __init__(self, pid):
        self.id, self.target_id, self.raw_content = pid, 1, "some real content " * 40


class _FakeTarget:
    name = "GIRARD NICOLAS"


class _FakeSession:
    """Stands in for the DB so this test exercises the LLM path deterministically.

    Written against a real session first, which made it depend on the local
    Postgres: with the database up `sess.get` returned None and the function
    returned at its missing-post guard, so the test passed WITHOUT ever reaching
    the LLM. With the database down the connection error propagated and it
    failed. Either way it was not testing what it claimed.
    """
    async def get(self, model, pid):
        return _FakeTarget() if model.__name__ == "Target" else _FakePost(pid)

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def test_an_llm_exception_falls_back_rather_than_propagating(monkeypatch):
    """An LLM failure must return None so the caller retries per post. A
    DATABASE failure deliberately does NOT — the per-post path would fail the
    same way, and the task's own retry is the right handler; swallowing it would
    look like "nothing to extract" and lose the work silently."""
    import app.database as db_mod

    svc = ExtractorService()
    monkeypatch.setattr(ex, "_load_prompt", lambda name: "RULES {name}")
    monkeypatch.setattr(db_mod, "CelerySessionLocal", lambda: _FakeSession())

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ex, "_call_json", boom)
    assert svc.extract_batch(post_ids=[1, 2], ctx=_Ctx()) is None


def test_an_unparseable_batch_reply_falls_back(monkeypatch):
    """The other half of the same contract, now that the LLM is actually reached."""
    import app.database as db_mod

    svc = ExtractorService()
    monkeypatch.setattr(ex, "_load_prompt", lambda name: "RULES {name}")
    monkeypatch.setattr(db_mod, "CelerySessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(ex, "_call_json", lambda *a, **k: None)
    assert svc.extract_batch(post_ids=[1, 2], ctx=_Ctx()) is None


def test_a_reply_missing_one_post_falls_back(monkeypatch):
    """Reached through the real function now, not just batch_analyses."""
    import app.database as db_mod

    svc = ExtractorService()
    monkeypatch.setattr(ex, "_load_prompt", lambda name: "RULES {name}")
    monkeypatch.setattr(db_mod, "CelerySessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(ex, "_call_json",
                        lambda *a, **k: {"results": {"1": {"insights": []}}})
    assert svc.extract_batch(post_ids=[1, 2], ctx=_Ctx()) is None


def test_both_paths_persist_through_the_same_method():
    """_persist is shared so the batched path cannot drift from the single one —
    the AE flag, metadata write and embedding generation all live there."""
    assert hasattr(ExtractorService, "_persist")
    import inspect
    source = inspect.getsource(ExtractorService._extract_batch_async)
    assert "self._persist" in source
