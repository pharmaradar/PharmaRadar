"""Pass-1 candidate selection — how much we fetch, and how on-topic it is.

Two properties are in tension and this file pins both.

**Volume.** Pass 1 is free. `_billable_steps` shows only `agent run` consumes
TinyFish credits; search and fetch are rate-limited but unmetered. The old
ceiling of 10 was throttling something that costs nothing, so it moved to 24.

**Accuracy.** Selection used to pad every spare slot with French pages that were
not about the target (`on_topic + rest` sliced to the limit). Measured against
the collected corpus, the great majority of KOL posts never mention the KOL by
name. Padding scales with the ceiling, so raising the ceiling on the old
behaviour would have bought mostly noise.

The two changes only make sense together, which is why they are tested together.
"""
import pytest

from app.services import scraper
from app.services.scraper import _select_candidates


def fr(i: int, relevant: bool) -> dict:
    """A French-source candidate; `relevant` is the on-topic flag."""
    return {"url": f"https://curie.fr/{i}", "relevant": relevant, "score": 1}


def foreign(i: int) -> dict:
    return {"url": f"https://example.com/{i}", "relevant": True, "score": 9}


# ── Volume ────────────────────────────────────────────────

def test_a_rich_target_fills_the_whole_ceiling_with_on_topic_material():
    """The point of raising the ceiling: when there IS on-topic French content,
    take all of it up to the cap."""
    picked = _select_candidates([fr(i, True) for i in range(40)], limit=24)
    assert len(picked) == 24
    assert all(p["relevant"] for p in picked)


def test_the_ceiling_is_higher_than_the_old_throttle():
    """Guards the constant itself — a silent revert would quietly halve intake
    of a resource that costs nothing."""
    assert scraper._FETCH_CEILING >= 20


# ── Accuracy ──────────────────────────────────────────────

def test_spare_slots_are_not_padded_with_off_topic_pages():
    """REGRESSION. With 3 on-topic and 40 off-topic French candidates the old
    code returned 24 — 21 of them about someone else. The ceiling is a maximum,
    not a quota."""
    picked = _select_candidates(
        [fr(i, True) for i in range(3)] + [fr(100 + i, False) for i in range(40)],
        limit=24)
    off_topic = [p for p in picked if not p["relevant"]]
    assert len(picked) < 24
    assert len(off_topic) <= scraper._MAX_BACKFILL


def test_on_topic_candidates_are_never_dropped_for_off_topic_ones():
    picked = _select_candidates(
        [fr(i, True) for i in range(5)] + [fr(100 + i, False) for i in range(30)],
        limit=24)
    assert sum(1 for p in picked if p["relevant"]) == 5


def test_backfill_does_not_grow_with_the_ceiling():
    """The property that makes raising the ceiling safe: off-topic intake is an
    ABSOLUTE cap, so a bigger ceiling buys on-topic material or nothing.

    Written proportional first (limit * 0.30) and this test caught it — at
    limit=100 that took 30 off-topic pages against 3 at limit=10, which is the
    scaling the change exists to remove."""
    pool = [fr(i, True) for i in range(2)] + [fr(100 + i, False) for i in range(200)]
    small = [p for p in _select_candidates(pool, limit=10) if not p["relevant"]]
    large = [p for p in _select_candidates(pool, limit=100) if not p["relevant"]]
    assert len(large) == len(small), (
        "off-topic intake scales with the ceiling — raising the cap would import "
        "proportionally more material that is not about the target")


# ── The thin-target exception ─────────────────────────────

def test_a_thin_target_still_gets_some_backfill():
    """A target that ends Pass 1 empty escalates to the Wave-2 agent rescue —
    the ONLY path that bills TinyFish credits. Starving it to protect precision
    costs money, so a bounded amount of off-topic French material is allowed."""
    picked = _select_candidates(
        [fr(0, True)] + [fr(100 + i, False) for i in range(10)], limit=10)
    assert len(picked) > 1


def test_a_target_with_no_french_candidates_falls_back_rather_than_returning_empty():
    """Same reasoning: an empty result is the expensive outcome, not the safe
    one. Documented behaviour, pinned so precision work does not remove it."""
    picked = _select_candidates([foreign(i) for i in range(5)], limit=10)
    assert len(picked) == 5


def test_french_sources_are_preferred_over_foreign_ones_under_the_fr_scope():
    picked = _select_candidates(
        [foreign(i) for i in range(10)] + [fr(i, True) for i in range(3)], limit=10)
    assert all("curie.fr" in p["url"] for p in picked)


# ── Guards ────────────────────────────────────────────────

@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_fetches_nothing(limit):
    assert _select_candidates([fr(0, True)], limit=limit) == []


def test_the_global_scope_is_unaffected_by_french_selection():
    """Only fr-scoped targets get the French rules; a global target keeps the
    plain ranked list."""
    pool = [foreign(i) for i in range(30)]
    assert len(_select_candidates(pool, limit=12, scope="global")) == 12


def test_unassessed_candidates_are_not_treated_as_off_topic():
    """"Relevance was never computed" is not the same as "these are off-topic".

    Caught by an existing fr_sources test whose fixtures carry no `relevant`
    key: capping those to _MAX_BACKFILL throttled a caller that had simply not
    assessed relevance, which would read as a sudden collapse in intake rather
    than as a bug.
    """
    unassessed = [{"url": f"https://c{i}.fr/y", "score": 9} for i in range(20)]
    assert len(_select_candidates(unassessed, limit=10)) == 10
