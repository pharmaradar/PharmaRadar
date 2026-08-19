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


# ── The SQL itself, executed against a real database ──────
#
# The counting query was rewritten to use conditional aggregation — both windows
# in ONE query per table instead of four round trips, on the caller's session
# instead of a fresh connection per topic, because the topic list renders every
# tracked topic and polls. That rewrite is real SQL and the tests above only
# cover the pure classify() helper, so it is exercised here for real.

import pytest_asyncio  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402


class _Topic:
    """Minimal stand-in carrying what _scope_terms and the window need."""
    def __init__(self, name="immunothérapie", days=30):
        self.name = name
        self.period_days = days
        self.restriction_terms = None
        self.exclusion_words = None
        self.language_filter = "fr"


@pytest.mark.asyncio
async def test_the_counting_query_runs_and_splits_the_two_windows(db_session):
    """Executes the real query. Rows are placed either side of the window edge,
    so a query that collapsed the two CASE branches — or got the boundary
    backwards — would report them in the wrong bucket rather than merely fail."""
    from app.models import ScrapedPost, Target
    from app.services.topic_momentum import topic_momentum

    now = datetime.now(timezone.utc)
    target = Target(name="MOMENTUM TEST KOL", known_urls="[]")
    db_session.add(target)
    await db_session.flush()

    def post(days_ago: int, n: int):
        return ScrapedPost(
            target_id=target.id,
            source_url=f"https://example.test/momentum/{days_ago}/{n}",
            raw_content="Un article sur l'immunothérapie dans le cancer du poumon.",
            content_hash=f"momentum-{days_ago}-{n}",
            scraped_at=now - timedelta(days=days_ago),
        )

    # 3 inside the current 30-day window, 1 inside the previous one.
    for n in range(3):
        db_session.add(post(5, n))
    db_session.add(post(40, 0))
    await db_session.flush()

    result = await topic_momentum(_Topic(days=30), session=db_session)
    assert result.current == 3, "current window miscounted"
    assert result.previous == 1, "previous window miscounted"
    assert result.direction == "new" or result.previous > 0


@pytest.mark.asyncio
async def test_a_topic_matching_nothing_counts_zero_without_error(db_session):
    from app.services.topic_momentum import topic_momentum

    result = await topic_momentum(_Topic(name="zzz-nonexistent-topic-zzz"),
                                  session=db_session)
    assert result.current == 0 and result.previous == 0
    assert result.direction == "unknown"
