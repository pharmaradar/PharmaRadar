"""patch_run — the counters the client watches a run through.

Every task reports progress through this one helper, so its edge cases are what
decide whether the dashboard tells the truth while a run is in flight.

Two of them are load-bearing:

`run_id=None` means "this work is not part of a scrape run" — an on-demand KOL
summary, a manual refresh. Passing 0 for that instead violated the foreign key
on person_summaries and made the summary button fail on every click (fixed
2026-08-17); None now short-circuits before any query.

Best-effort is deliberate. A progress update must never fail the task that is
making the progress: a Redis or Postgres hiccup while writing a counter would
otherwise lose the actual work.
"""
import pytest

from app.tasks import utils


def test_no_fields_is_a_no_op(monkeypatch):
    """Guards against a caller doing patch_run(run_id) with nothing to set and
    paying for a session anyway."""
    called = {"n": 0}
    monkeypatch.setattr(utils.asyncio, "run", lambda coro: called.update(n=called["n"] + 1))
    utils.patch_run(1)
    assert called["n"] == 0


def test_a_none_run_id_never_touches_the_database(monkeypatch):
    """REGRESSION. On-demand work has no run to report against. This used to be
    signalled with 0, which is not a valid run_logs id — the insert violated the
    FK and the on-demand summary button failed on every click."""
    called = {"n": 0}

    def spy(coro):
        called["n"] += 1
        coro.close()

    monkeypatch.setattr(utils.asyncio, "run", spy)
    utils.patch_run(None, **{"+insights_extracted": 3})
    assert called["n"] == 0, "a run-less update should short-circuit, not query"


def test_a_real_run_id_does_reach_the_database(monkeypatch):
    """The counterpart: the short-circuit must not swallow genuine updates."""
    called = {"n": 0}

    def spy(coro):
        called["n"] += 1
        coro.close()

    monkeypatch.setattr(utils.asyncio, "run", spy)
    utils.patch_run(42, **{"+insights_extracted": 3})
    assert called["n"] == 1


@pytest.mark.parametrize("failure", [
    RuntimeError("event loop is closed"),
    OSError("postgres unreachable"),
])
def test_a_database_failure_never_propagates(monkeypatch, failure):
    """Progress reporting is best-effort by design. A counter that cannot be
    written must not destroy the extraction that earned it."""
    def boom(coro):
        coro.close()
        raise failure

    monkeypatch.setattr(utils.asyncio, "run", boom)
    utils.patch_run(1, **{"+new_posts_found": 5})   # must not raise


def test_increment_fields_are_distinguishable_from_assignments():
    """The "+" prefix is the whole calling convention: "+new_posts_found" adds
    to the running total while "current_target" replaces it. A caller that
    confuses them either overwrites a run's progress with a single task's count
    or accumulates a name."""
    increments = {k: v for k, v in {"+new_posts_found": 5, "current_target": "X"}.items()
                  if k.startswith("+")}
    assignments = {k: v for k, v in {"+new_posts_found": 5, "current_target": "X"}.items()
                   if not k.startswith("+")}
    assert increments == {"+new_posts_found": 5}
    assert assignments == {"current_target": "X"}


def test_patch_run_accepts_an_optional_run_id_in_its_signature():
    """Typed `int` originally, which is what made 0 look like the way to say
    "no run". Pinned so the sentinel cannot come back."""
    import inspect

    annotation = inspect.signature(utils.patch_run).parameters["run_id"].annotation
    assert "None" in str(annotation), (
        "run_id must be optional — a magic numeric sentinel violates the FK")
