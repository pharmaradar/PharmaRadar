"""Activity trend for a tracked topic.

Burning Topics is a TRACKING feature: the client defines the topics he wants
monitored, with their own period, language, exclusion words and restriction
terms. This does not decide which topics matter — he already did that — it
answers the next question about each one: is it moving?

Volume in the current window against the same length of time immediately
before. Movement is the thing no other surface in the platform shows; burning
topics, social trends and the briefs all answer "what is being said" as of now,
and never "compared to what".

It is information attached to each tracked topic, deliberately NOT an ordering.
His watchlist stays in his order — re-sorting it by movement would rearrange the
page under him on every load.

Counting reuses the topic's own matching rules (terms, exclusions, window) from
the report generator, so a topic's momentum is measured over exactly the posts
its report would be written from. A second, near-identical matcher would drift
and the number would quietly stop describing the report beside it.

No LLM and no scraping: this is COUNT(*) over rows already stored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog

logger = structlog.get_logger(__name__)

# Below this the percentage is noise: going from 1 post to 3 is +200% and means
# nothing. Reported as "too few to judge" rather than as a trend.
MIN_BASE_FOR_TREND = 5

# How far the change has to move before it is called anything but steady.
# Week-to-week counts wobble; a 10% swing on 40 posts is four posts.
FLAT_BAND_PCT = 15


@dataclass
class Momentum:
    current: int
    previous: int
    change_pct: float | None      # None when the base is too small to judge
    direction: str                # rising | falling | steady | new | unknown
    window_days: int

    def as_dict(self) -> dict:
        return {
            "current": self.current,
            "previous": self.previous,
            "change_pct": self.change_pct,
            "direction": self.direction,
            "window_days": self.window_days,
        }


def classify(current: int, previous: int) -> tuple[float | None, str]:
    """Turn two counts into a percentage and a word.

    The words matter more than the number here, because the reader scans a list.
    'new' is kept separate from 'rising': a topic with no prior activity has no
    percentage to give, and calling that +100% would invent a baseline.
    """
    if previous == 0:
        return (None, "new" if current > 0 else "unknown")
    if previous < MIN_BASE_FOR_TREND:
        # A real base, but too small for a percentage to mean anything.
        return (None, "unknown")

    change = (current - previous) / previous * 100
    if abs(change) < FLAT_BAND_PCT:
        return (round(change, 1), "steady")
    return (round(change, 1), "rising" if change > 0 else "falling")


async def topic_momentum(topic, *, window_days: int | None = None,
                         session=None) -> Momentum:
    """Count this topic's posts now versus the window immediately before.

    `session` is the caller's — the topic list renders every tracked topic and
    polls, so opening a fresh connection per topic churned the pool for no
    reason. Both windows are counted in ONE query per table using conditional
    aggregation rather than four separate round trips.
    """
    from sqlalchemy import case, func, select

    from app.database import CelerySessionLocal
    from app.models import ScrapedPost, SocialPost
    from app.services.ae_filter import post_not_ae, social_not_ae
    from app.tasks.burning_topics import _exclude, _match_any, _scope_terms

    days = window_days or topic.period_days or 30
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=days)
    previous_start = now - timedelta(days=days * 2)

    terms, exclusions = _scope_terms(topic, None)
    if not terms:
        return Momentum(0, 0, None, "unknown", days)

    def _windowed(model, stamp, *text_columns):
        """One row: (current_window_count, previous_window_count)."""
        current_case = func.count(case((stamp >= current_start, 1)))
        previous_case = func.count(
            case(((stamp >= previous_start) & (stamp < current_start), 1)))
        q = (select(current_case, previous_case)
             .select_from(model)
             .where(stamp >= previous_start, stamp < now)
             .where(_match_any(terms, *text_columns)))
        return q

    async def _run(sess) -> tuple[int, int]:
        scraped = _windowed(ScrapedPost, ScrapedPost.scraped_at,
                            ScrapedPost.raw_content, ScrapedPost.title).where(post_not_ae())
        scraped = _exclude(scraped, exclusions, ScrapedPost.raw_content, ScrapedPost.title)

        social = _windowed(SocialPost, SocialPost.scraped_at,
                           SocialPost.text, SocialPost.topic,
                           SocialPost.hashtags).where(social_not_ae())
        social = _exclude(social, exclusions,
                          SocialPost.text, SocialPost.topic, SocialPost.hashtags)

        s_cur, s_prev = (await sess.execute(scraped)).one()
        o_cur, o_prev = (await sess.execute(social)).one()
        return (s_cur or 0) + (o_cur or 0), (s_prev or 0) + (o_prev or 0)

    if session is not None:
        current, previous = await _run(session)
    else:
        async with CelerySessionLocal() as sess:
            current, previous = await _run(sess)

    change_pct, direction = classify(current, previous)
    return Momentum(current, previous, change_pct, direction, days)
