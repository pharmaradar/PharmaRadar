"""extract_target_posts must stop on the clock, not on the post list.

The failure this guards, observed in production on 2026-08-17 against the
sibling task classify_ae_backfill: a loop of LLM calls under a Celery time limit
with no clock check. One "call" is not one round trip — extractor._call_json
retries twice, and each attempt goes through llm_router's tenacity retry (4
attempts, 15-120s exponential backoff on 429), so a single post can consume
200s+. Twenty-five of those against a 600s soft limit meant the task overran, the
worker was SIGKILLed at the hard limit, and the message was requeued.

Stopping early is safe precisely because extraction commits per post: the next
sweep re-queries for unextracted posts and picks up where this one stopped. So
the property worth pinning is "stops early and reports honestly", not "processes
everything".
"""
import pytest

from app.tasks.llm import _EXTRACT_BUDGET_SECONDS, _extract_within_budget


class FakeClock:
    """Advances a fixed amount on every reading after the first."""

    def __init__(self, step: float):
        self.step = step
        self.now = 0.0
        self.readings = 0

    def __call__(self) -> float:
        # First reading is the loop's start stamp; don't charge time for it.
        if self.readings:
            self.now += self.step
        self.readings += 1
        return self.now


def test_all_posts_extracted_when_calls_are_fast():
    calls, saved = [], []
    done, insights, capped = _extract_within_budget(
        [1, 2, 3, 4, 5],
        lambda pid: (calls.append(pid), 2)[1],
        saved.append,
        budget=100, clock=FakeClock(1),
    )
    assert calls == [1, 2, 3, 4, 5]
    assert (done, insights, capped) == (5, 10, False)
    assert saved == [2, 2, 2, 2, 2]


def test_stops_once_the_budget_is_spent():
    """The whole point: a slow LLM must not be allowed to run the list out."""
    calls = []
    done, insights, capped = _extract_within_budget(
        list(range(25)),
        lambda pid: (calls.append(pid), 1)[1],
        lambda saved: None,
        budget=100, clock=FakeClock(30),   # 30s per post → budget gone after ~4
    )
    assert capped is True
    assert len(calls) < 25, "the loop ran the whole list despite the budget"
    assert done == len(calls)
    assert insights == len(calls)


def test_budget_is_checked_before_the_call_not_after():
    """Checking after the call would still pay for the expensive round trip that
    breaks the budget — the saving only exists if the check comes first."""
    calls = []
    _extract_within_budget(
        [1, 2, 3],
        lambda pid: (calls.append(pid), 0)[1],
        lambda saved: None,
        # Already over budget at the first check.
        budget=10, clock=FakeClock(1000),
    )
    assert calls == [], "an LLM call was made after the budget was already spent"


def test_empty_post_list_is_not_reported_as_capped():
    assert _extract_within_budget([], lambda pid: 0, lambda s: None) == (0, 0, False)


def test_counters_only_reflect_posts_actually_processed():
    """A capped run must not report progress it did not make — the run's
    insight/LLM counters are what the client sees on the dashboard."""
    seen = []
    done, insights, capped = _extract_within_budget(
        list(range(10)),
        lambda pid: 3,
        seen.append,
        budget=50, clock=FakeClock(20),
    )
    assert capped is True
    assert len(seen) == done
    assert insights == 3 * done


def test_budget_leaves_headroom_under_the_soft_time_limit():
    """The budget is only useful if it expires before Celery kills the task.

    Read from the live task registry rather than hardcoded, so raising the
    decorator's limit without revisiting the budget fails here.
    """
    from app.tasks.celery_app import celery_app

    task = celery_app.tasks["app.tasks.llm.extract_target_posts"]
    ann = (celery_app.conf.task_annotations or {}).get(
        "app.tasks.llm.extract_target_posts", {})
    soft = (ann.get("soft_time_limit") or task.soft_time_limit
            or celery_app.conf.task_soft_time_limit)
    assert _EXTRACT_BUDGET_SECONDS < soft, (
        f"budget {_EXTRACT_BUDGET_SECONDS}s must expire before the {soft}s soft "
        f"limit, or the guard never gets a chance to fire"
    )
    # Enough room for the in-flight call to land plus the final counter update.
    assert soft - _EXTRACT_BUDGET_SECONDS >= 60


@pytest.mark.parametrize("exc", [RuntimeError("llm exploded"), ValueError("bad json")])
def test_extraction_failures_still_propagate(exc):
    """The budget guard must not accidentally swallow errors — the task's own
    retry logic depends on seeing them."""
    def boom(pid):
        raise exc

    with pytest.raises(type(exc)):
        _extract_within_budget([1], boom, lambda s: None)
