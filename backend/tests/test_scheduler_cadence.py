"""Run cadence — weekly or monthly.

The client asked for Daily to be removed: a report covering a 30-day window has
little new to say every 24 hours, and each run spends real scraping credit.

The risk being guarded here is a schedule that fires more often than intended.
A row still holding the retired "daily" value must fall back to the *cheaper*
cadence, never to firing every day.
"""
import pytest

from app.tasks.scheduler import VALID_FREQUENCIES, _normalise_frequency


def test_only_weekly_and_monthly_are_supported():
    assert VALID_FREQUENCIES == ("weekly", "monthly")


@pytest.mark.parametrize("stored", ["daily", "DAILY", "", None, "garbage", "hourly"])
def test_retired_or_unknown_cadence_falls_back_to_weekly(stored):
    """Never to daily — the fallback must be the cheaper direction."""
    assert _normalise_frequency(stored) == "weekly"


@pytest.mark.parametrize("stored,expected", [
    ("weekly", "weekly"),
    ("monthly", "monthly"),
    ("  Monthly  ", "monthly"),
    ("WEEKLY", "weekly"),
])
def test_supported_cadences_survive_normalisation(stored, expected):
    assert _normalise_frequency(stored) == expected


# ── Firing logic ──────────────────────────────────────────
# Mirrors the day check in scheduler._check so the calendar edge cases are
# covered without standing up Celery and a database.

def _fires(frequency: str, *, day_of_week: int, day_of_month: int,
           now_weekday: int, now_day: int) -> bool:
    freq = _normalise_frequency(frequency)
    if freq == "monthly":
        target = min(max(int(day_of_month or 1), 1), 28)
        return now_day == target
    return now_weekday == (day_of_week or 1)


def test_weekly_fires_only_on_the_configured_weekday():
    fires = lambda wd: _fires("weekly", day_of_week=1, day_of_month=1,
                              now_weekday=wd, now_day=15)
    assert fires(1) is True                      # Tuesday
    assert [fires(d) for d in (0, 2, 3, 4, 5, 6)] == [False] * 6


def test_monthly_fires_only_on_the_configured_day():
    fires = lambda dom: _fires("monthly", day_of_week=1, day_of_month=5,
                               now_weekday=3, now_day=dom)
    assert fires(5) is True
    assert fires(4) is False and fires(6) is False


def test_monthly_day_is_capped_so_short_months_are_never_skipped():
    """A run scheduled for the 31st would silently skip most of the year."""
    assert _fires("monthly", day_of_week=1, day_of_month=31,
                  now_weekday=0, now_day=28) is True
    assert _fires("monthly", day_of_week=1, day_of_month=0,
                  now_weekday=0, now_day=1) is True


def test_a_row_still_saying_daily_does_not_fire_every_day():
    """The regression that matters: before the fallback, 'daily' skipped the day
    check entirely and triggered a paid run every 24 hours."""
    fired = [
        _fires("daily", day_of_week=1, day_of_month=1, now_weekday=wd, now_day=15)
        for wd in range(7)
    ]
    assert fired.count(True) == 1, "must behave weekly, not daily"
