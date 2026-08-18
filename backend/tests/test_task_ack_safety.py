"""Long tasks must not be acks_late — or the broker duplicates them mid-flight.

The trap, hit for real by wave2_rescue: with `acks_late=True` the message is
acknowledged only when the task FINISHES. Redis' `visibility_timeout` (set in
celery_app.broker_transport_options) is how long the broker waits for that ack
before deciding the worker died and handing the message to someone else. So any
acks_late task allowed to run LONGER than visibility_timeout will, reliably, be
redelivered while it is still running — two copies, concurrently, on the same
arguments.

For this codebase that is not a theoretical concern: wave2_rescue drives the
billed TinyFish agent path (~52 steps per target), so a duplicate is paid for
twice, and it also duplicates the summary → PDF chain behind it.

Every long task except wave2_rescue already opted out of acks_late, each with a
comment saying why. This test makes that convention enforced rather than
remembered, for tasks that do not exist yet as much as for the ones that do.
"""
import pytest

from app.tasks.celery_app import celery_app

# Force-import every task module so the registry is fully populated. Without
# this the test would silently pass by inspecting an almost-empty registry.
import app.tasks.accounts          # noqa: F401
import app.tasks.burning_topics    # noqa: F401
import app.tasks.literature        # noqa: F401
import app.tasks.llm               # noqa: F401
import app.tasks.maintenance       # noqa: F401
import app.tasks.market_report     # noqa: F401
import app.tasks.pdf               # noqa: F401
import app.tasks.scheduler         # noqa: F401
import app.tasks.scrape            # noqa: F401
import app.tasks.social            # noqa: F401
import app.tasks.synthesis         # noqa: F401


def _visibility_timeout() -> int:
    opts = celery_app.conf.broker_transport_options or {}
    # Celery's own default when unset is 3600s.
    return int(opts.get("visibility_timeout", 3600))


def _effective_limits(name: str, task) -> tuple[int, int]:
    """Resolve the limits actually in force: annotations beat the decorator,
    and the global conf default fills in for whatever neither sets."""
    ann = (celery_app.conf.task_annotations or {}).get(name, {})
    soft = (ann.get("soft_time_limit")
            or getattr(task, "soft_time_limit", None)
            or celery_app.conf.task_soft_time_limit)
    hard = (ann.get("time_limit")
            or getattr(task, "time_limit", None)
            or celery_app.conf.task_time_limit)
    return int(soft or 0), int(hard or 0)


def _own_tasks() -> list[tuple[str, object]]:
    return [(n, t) for n, t in celery_app.tasks.items() if n.startswith("app.tasks.")]


def test_the_task_registry_is_actually_populated():
    """Guards the test itself: an empty registry would make everything below vacuous."""
    assert len(_own_tasks()) >= 15


def test_visibility_timeout_is_configured_explicitly():
    """Relying on Celery's 3600s default would hide the constraint being tested."""
    opts = celery_app.conf.broker_transport_options or {}
    assert "visibility_timeout" in opts


@pytest.mark.parametrize("name,task", _own_tasks(), ids=lambda v: v if isinstance(v, str) else "")
def test_acks_late_tasks_finish_before_the_broker_redelivers(name, task):
    """An acks_late task must be unable to outlive visibility_timeout.

    Checked against the HARD limit, since that is the longest the task can
    actually stay alive (the soft limit only raises an exception the task may
    catch — as several of ours do — and keep going).
    """
    if not getattr(task, "acks_late", False):
        return  # opted out: redelivery-on-timeout does not apply

    _soft, hard = _effective_limits(name, task)
    vis = _visibility_timeout()
    assert hard, f"{name} is acks_late with no resolvable time limit"
    assert hard < vis, (
        f"{name} is acks_late with a {hard}s hard limit but visibility_timeout is "
        f"{vis}s — the broker will redeliver it while it is still running, so it "
        f"can execute twice concurrently. Either set acks_late=False on it or "
        f"raise visibility_timeout above every acks_late task's hard limit."
    )


@pytest.mark.parametrize("name,task", _own_tasks(), ids=lambda v: v if isinstance(v, str) else "")
def test_soft_limit_leaves_room_for_the_hard_limit(name, task):
    """A soft limit >= the hard limit means cleanup never gets to run: the task
    is SIGKILLed at the hard limit without the soft exception ever firing."""
    soft, hard = _effective_limits(name, task)
    if not (soft and hard):
        return
    assert soft < hard, f"{name}: soft_time_limit {soft}s must be < time_limit {hard}s"


def test_wave2_rescue_specifically_is_not_acks_late():
    """The regression this file was written for — pinned by name so a future
    edit that flips it back fails loudly rather than silently double-billing."""
    task = celery_app.tasks["app.tasks.scrape.wave2_rescue"]
    assert task.acks_late is False
    _soft, hard = _effective_limits("app.tasks.scrape.wave2_rescue", task)
    assert hard > _visibility_timeout(), (
        "wave2_rescue is expected to outlive visibility_timeout — that is "
        "precisely why it must not be acks_late. If this assertion fails the "
        "task got faster or the timeout got longer, and the acks_late=False "
        "reasoning in its decorator needs rereading."
    )


# ── Wave 2 cost containment ───────────────────────────────
#
# wave2_rescue is the ONLY path that bills TinyFish credits (search and fetch
# are unmetered; `agent run` bills its num_of_steps). Anything that makes it
# repeat work spends real money, so its budget arithmetic is pinned here.

def test_wave2_budget_leaves_room_for_one_whole_target():
    """The loop must never START a target it cannot finish. One target is up to
    5 agent calls (~120s each) plus a 180s wait on the summary chain; without
    headroom the task was killed mid-target by the soft limit."""
    from app.tasks import scrape

    assert scrape._WAVE2_BUDGET > scrape._PER_TARGET_BUDGET, (
        "budget cannot fit a single target, so the loop would never run one")

    task = celery_app.tasks["app.tasks.scrape.wave2_rescue"]
    soft, _hard = _effective_limits("app.tasks.scrape.wave2_rescue", task)
    assert scrape._WAVE2_BUDGET < soft, (
        f"budget {scrape._WAVE2_BUDGET}s must expire before the {soft}s soft "
        f"limit, or the guard never fires and Celery kills the task instead")


def test_a_finished_target_is_dropped_by_its_exact_redis_member():
    """Resumability is what stops a retry re-paying for completed rescues.

    LREM matches exact bytes. Passing a re-serialised dict only works while
    json.dumps happens to reproduce the original string; when it does not, the
    removal silently does nothing and the next attempt re-runs the agent.
    """
    from app.tasks.scrape import _drop_from_queue

    class FakeRedis:
        def __init__(self): self.removed = []
        def lrem(self, key, count, value): self.removed.append((key, count, value))

    r = FakeRedis()
    raw = '{"target_id": 7, "bot_blocked": [], "idempotency_key": "k"}'
    _drop_from_queue(r, "wave2:1:processing", raw, __import__("structlog").get_logger())
    assert r.removed == [("wave2:1:processing", 1, raw)], \
        "the queue member must be removed verbatim, not re-serialised"


def test_dropping_from_the_queue_never_raises():
    """Bookkeeping must not be able to fail a rescue that already succeeded and
    was already paid for."""
    from app.tasks.scrape import _drop_from_queue

    class Broken:
        def lrem(self, *a, **k): raise RuntimeError("redis gone")

    _drop_from_queue(Broken(), "k", "raw", __import__("structlog").get_logger())
    _drop_from_queue(None, "k", "raw", __import__("structlog").get_logger())
