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
