"""Absent engagement is not zero engagement.

X and LinkedIn arrive through TinyFish search, which carries no metrics at all.
Instagram's come from a separate Apify request that Instagram rate-limits — the
actor logs "media metrics rate limited" and "unable to get media metrics fill",
then returns the post without them.

Measured on the live table: engagement is absent for 100% of X, 100% of
LinkedIn and 32% of Instagram — 752 of 1,044 posts. `_post_reach` already said
"their numbers are absent rather than zero", but the ranking multiplied by that
zero, so no X or LinkedIn post could reach the top 50 of "Trending on Social"
and none could pass a minimum-likes filter. The page was effectively
Facebook-only.

Ranking them against the median of what IS measurable puts them mid-pack, where
recency decides — measured best rank moved from #141 to #27, and from 0 to 24
posts in the top 50.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.routers.social import (
    _engagement, _neutral_engagement, _trend_score, engagement_available,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


class Post:
    def __init__(self, platform="instagram", likes=0, comments=0, views=0,
                 shares=0, age_days=1):
        self.platform, self.likes, self.comments = platform, likes, comments
        self.views, self.shares = views, shares
        self.posted_at = NOW - timedelta(days=age_days)
        self.scraped_at = self.posted_at


# ── Telling absent from zero ──────────────────────────────

@pytest.mark.parametrize("platform", ["twitter", "linkedin"])
def test_search_only_platforms_never_have_measurable_engagement(platform):
    """Their numbers are not low, they are unobtainable."""
    assert engagement_available(Post(platform=platform, likes=0)) is False


def test_an_instagram_post_with_metrics_is_measurable():
    assert engagement_available(Post("instagram", likes=98)) is True


def test_an_instagram_post_whose_metrics_were_rate_limited_is_not():
    """The Apify actor returns the post with the whole metric set empty. A
    genuinely unengaged Instagram post is vanishingly rare, so an all-zero row
    there means the fetch was blocked, not that nobody engaged."""
    assert engagement_available(Post("instagram", likes=0, comments=0, views=0)) is False


def test_a_facebook_post_with_engagement_is_measurable():
    assert engagement_available(Post("facebook", likes=34, comments=8)) is True


# ── Ranking ───────────────────────────────────────────────

def test_an_unmeasurable_post_is_not_pinned_to_zero():
    """THE regression. Multiplying by an absent figure buried 72% of the corpus
    below every measured post, however stale and however lightly engaged."""
    unmeasured = Post("linkedin", age_days=0)
    assert _trend_score(unmeasured, NOW, neutral_engagement=26.0) > 0


def test_a_fresh_unmeasurable_post_outranks_a_stale_barely_engaged_one():
    """A post today from a tracked French KOL should not sit beneath a
    three-week-old Facebook post with three likes."""
    fresh_linkedin = Post("linkedin", age_days=0)
    stale_facebook = Post("facebook", likes=3, age_days=21)
    neutral = 26.0
    assert (_trend_score(fresh_linkedin, NOW, neutral)
            > _trend_score(stale_facebook, NOW, neutral))


def test_a_genuinely_popular_post_still_outranks_an_unmeasurable_one():
    """The fix must not promote unknowns above real, measured popularity."""
    popular = Post("facebook", likes=500, age_days=1)
    unknown = Post("linkedin", age_days=1)
    assert _trend_score(popular, NOW, 26.0) > _trend_score(unknown, NOW, 26.0)


def test_recency_still_separates_two_unmeasurable_posts():
    """With engagement unavailable on both, freshness is the only signal left."""
    older = Post("twitter", age_days=20)
    newer = Post("twitter", age_days=1)
    assert _trend_score(newer, NOW, 26.0) > _trend_score(older, NOW, 26.0)


# ── The neutral baseline ──────────────────────────────────

def test_the_baseline_is_the_median_of_what_can_be_measured():
    posts = [Post("facebook", likes=10), Post("facebook", likes=20),
             Post("facebook", likes=30), Post("linkedin")]
    assert _neutral_engagement(posts) == _engagement(Post("facebook", likes=20))


def test_a_batch_with_nothing_measurable_falls_back_to_pure_recency():
    """No engagement anywhere means engagement cannot separate anything; every
    post gets the same weight and the ordering is by date."""
    posts = [Post("twitter"), Post("linkedin")]
    assert _neutral_engagement(posts) == 1.0


def test_the_baseline_ignores_rate_limited_instagram_rows():
    """Counting their zeroes would drag the median down and re-bury everything."""
    posts = [Post("facebook", likes=100), Post("instagram", likes=0, comments=0)]
    assert _neutral_engagement(posts) == _engagement(Post("facebook", likes=100))


def test_the_baseline_is_never_presented_as_a_post_s_own_engagement():
    """It exists to order rows, not to fill in a number we do not have. A post
    with unreadable metrics must still report zero likes, flagged unavailable —
    inventing 26 likes for it would be fabricating data the client would read."""
    unmeasured = Post("linkedin")
    assert _engagement(unmeasured) == 0
    assert engagement_available(unmeasured) is False


def test_a_search_sourced_platform_is_unmeasurable_even_if_a_number_appears():
    """The platform check is not redundant with the value check.

    X and LinkedIn reach us through TinyFish search, which does not report
    engagement. If a stray figure ever appears on one of those rows it is an
    artefact of the search result, not a measurement of the post, and treating
    it as real would let one unreliable number outrank genuinely measured posts.
    """
    assert engagement_available(Post("linkedin", likes=999)) is False
    assert engagement_available(Post("twitter", likes=50, comments=10)) is False
