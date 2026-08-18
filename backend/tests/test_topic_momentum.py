"""Activity trend for a tracked topic — and what it refuses to claim.

Burning Topics is a tracking feature: the client chooses what to monitor. This
does not rank his topics, it annotates them with the one thing no other surface
shows — whether a topic is moving, rather than only what is being said in it.

Most of what is pinned here is restraint. A percentage computed off two posts is
noise dressed as a measurement, and on a page a client reads weekly, a confident
"+200%" that means "one post became three" is worse than no number.
"""
import pytest

from app.services.topic_momentum import (
    FLAT_BAND_PCT, MIN_BASE_FOR_TREND, classify,
)


# ── Refusing to invent a trend ────────────────────────────

def test_a_topic_with_no_prior_activity_is_new_not_up_infinitely():
    """No baseline exists, so no percentage can. Calling it +100% would invent
    the very number the reader would act on."""
    change, direction = classify(current=40, previous=0)
    assert direction == "new"
    assert change is None


def test_a_topic_with_nothing_at_all_is_unknown():
    assert classify(current=0, previous=0) == (None, "unknown")


@pytest.mark.parametrize("previous", range(1, MIN_BASE_FOR_TREND))
def test_a_tiny_baseline_gives_no_percentage(previous):
    """1 post becoming 3 is +200% and means nothing. Reported as unknown."""
    change, direction = classify(current=previous * 3, previous=previous)
    assert change is None
    assert direction == "unknown"


# ── Reporting a real change ───────────────────────────────

def test_a_clear_rise_is_reported_as_rising():
    change, direction = classify(current=90, previous=30)
    assert direction == "rising"
    assert change == pytest.approx(200.0)


def test_a_clear_fall_is_reported_as_falling():
    change, direction = classify(current=10, previous=40)
    assert direction == "falling"
    assert change == pytest.approx(-75.0)


def test_small_wobble_reads_as_steady_not_as_a_trend():
    """Week-to-week counts move on their own. A handful of posts either way is
    not a signal, and labelling it one trains the reader to ignore the label."""
    _change, direction = classify(current=42, previous=40)
    assert direction == "steady"


def test_the_flat_band_is_wide_enough_to_absorb_noise():
    assert FLAT_BAND_PCT >= 10
    assert MIN_BASE_FOR_TREND >= 3


def test_a_change_just_past_the_band_is_a_trend():
    previous = 100
    current = int(previous * (1 + (FLAT_BAND_PCT + 5) / 100))
    _change, direction = classify(current, previous)
    assert direction == "rising"


def test_dropping_to_zero_is_falling_not_unknown():
    """A topic that went quiet is a real finding for someone tracking it."""
    _change, direction = classify(current=0, previous=50)
    assert direction == "falling"


# ── Shape ─────────────────────────────────────────────────

def test_the_direction_is_always_one_of_the_known_words():
    """The UI colours on these; an unexpected value would render unstyled."""
    known = {"rising", "falling", "steady", "new", "unknown"}
    for current, previous in [(0, 0), (5, 0), (1, 2), (50, 50), (90, 30), (0, 50)]:
        assert classify(current, previous)[1] in known
