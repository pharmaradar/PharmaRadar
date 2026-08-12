"""Market-research report — the shared generator behind Topic Explorer and Burning Topics.

The client asked for six named sections. Two of them (voice distribution, volume)
are COMPUTED from rows rather than written by the model, because a model asked to
estimate "how many patients are discussing this" produces a confident number with
nothing behind it. These tests guard that boundary, and the honesty of the
figures on either side of it.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import market_report as mr
from app.services.voice_profile import (
    DOCTOR, EXACT, INFERRED, KOL, ORGANISATION, OTHER, PATIENT,
    build_breakdown, classify,
)


# ── Voice classification ──────────────────────────────────

def test_tracked_kol_is_exact_not_guessed():
    """An insight is tied to a Target by foreign key — that is a fact."""
    bucket, confidence, _ = classify("BESSE BENJAMIN", is_tracked_kol=True)
    assert (bucket, confidence) == (KOL, EXACT)
    bucket, confidence, _ = classify("AstraZeneca France", target_type="competitor")
    assert (bucket, confidence) == (ORGANISATION, EXACT)


def test_registry_domains_classify_exactly():
    assert classify("", url="https://has-sante.fr/x")[:2] == (ORGANISATION, EXACT)
    assert classify("", url="https://ligue-cancer.net/a")[:2] == (PATIENT, EXACT)
    assert classify("leQdM", url="https://lequotidiendumedecin.fr/a")[:2] == (ORGANISATION, EXACT)


@pytest.mark.parametrize("author", [
    "Dr Smith", "Pr Nicolas Girard", "drozgurtoklucu", "drmonish_childneuro",
    "@DrLeSage", "prcardio", "jean.md",
])
def test_clinician_handles_are_inferred_doctors(author):
    bucket, confidence, _ = classify(author)
    assert (bucket, confidence) == (DOCTOR, INFERRED)


@pytest.mark.parametrize("author", [
    "drugstore", "drone_pilot", "dreamteam", "Andrew Drone",
    "profile_fr", "practice_mgmt", "prime_health",
])
def test_ordinary_words_are_not_mistaken_for_clinicians(author):
    """"dr"/"pr" prefixes are common in English words; a false doctor count is
    worse than an unknown one because the client would act on it."""
    assert classify(author)[0] != DOCTOR


def test_missing_author_is_unknown_not_other_guesswork():
    bucket, confidence, evidence = classify(None)
    assert bucket == OTHER and confidence == "unknown"
    assert "no author" in evidence


def test_breakdown_merges_a_speaker_and_reports_exact_share():
    mentions = [
        {"author": "BESSE BENJAMIN", "url": "", "is_tracked_kol": True, "target_type": "kol"},
        {"author": "BESSE BENJAMIN", "url": "", "is_tracked_kol": True, "target_type": "kol"},
        {"author": "randomuser", "url": "", "is_tracked_kol": False, "target_type": None},
    ]
    breakdown = build_breakdown(mentions)
    assert breakdown.total == 3
    assert len(breakdown.voices) == 2, "one speaker, two mentions"
    assert breakdown.counts[KOL] == 2
    assert round(breakdown.exact_share * 100) == 67


def test_a_speaker_identified_later_is_upgraded_not_left_guessed():
    """The same author can appear first with no URL and later from a registry
    domain; the definite classification must win."""
    breakdown = build_breakdown([
        {"author": "leQdM", "url": "", "is_tracked_kol": False, "target_type": None},
        {"author": "leQdM", "url": "https://lequotidiendumedecin.fr/a",
         "is_tracked_kol": False, "target_type": None},
    ])
    voice = breakdown.voices[0]
    assert voice.confidence == EXACT and voice.bucket == ORGANISATION


def test_percentages_sum_to_about_one_hundred():
    breakdown = build_breakdown([
        {"author": f"user{i}", "url": "", "is_tracked_kol": False, "target_type": None}
        for i in range(7)
    ] + [{"author": "Dr Smith", "url": "", "is_tracked_kol": False, "target_type": None}])
    assert 99 <= sum(r["percent"] for r in breakdown.as_rows()) <= 101


# ── Volume ────────────────────────────────────────────────

def _item(kind="social post", at=None, engagement=0):
    return {"kind": kind, "at": at, "engagement": engagement}


def test_volume_reports_date_coverage_rather_than_hiding_it():
    """Only ~41% of social posts carry a posted_at — the search path cannot
    supply one — so a bare trend line would describe a minority of the data."""
    now = datetime.now(timezone.utc)
    items = [_item(at=now), _item(at=now - timedelta(days=8)), _item(at=None), _item(at=None)]
    volume = mr.compute_volume(items, 30)
    assert volume["total"] == 4
    assert volume["dated"] == 2
    assert volume["date_coverage"] == 50
    assert len(volume["per_week"]) == 2, "weekly buckets cover only the dated subset"


def test_volume_counts_by_kind_and_engagement():
    now = datetime.now(timezone.utc)
    volume = mr.compute_volume(
        [_item("KOL statement", now), _item("social post", now, 10), _item("social post", now, 5)],
        30,
    )
    assert volume["by_kind"]["social post"] == 2
    assert volume["by_kind"]["KOL statement"] == 1
    assert volume["total_engagement"] == 15


def test_volume_of_nothing_does_not_divide_by_zero():
    volume = mr.compute_volume([], 30)
    assert volume["total"] == 0 and volume["date_coverage"] == 0


# ── Prompt and parsing ────────────────────────────────────

def _material():
    now = datetime.now(timezone.utc)
    items = [
        {"kind": "KOL statement", "author": "BESSE BENJAMIN", "target_type": "kol",
         "is_tracked_kol": True, "text": "L'immunothérapie néoadjuvante change la prise en charge.",
         "topic": "CBNPC", "sentiment": "positive", "url": "https://edimark.fr/a",
         "source_name": "edimark.fr", "date": "2026-08-02", "engagement": 0, "at": now},
        {"kind": "twitter post", "author": "@HAS_sante", "target_type": None,
         "is_tracked_kol": False, "text": "Avis sur le dépistage.", "topic": "dépistage",
         "sentiment": "", "url": "https://has-sante.fr/x", "source_name": "has-sante.fr",
         "date": "2026-08-05", "engagement": 12, "at": now},
    ]
    voices = build_breakdown([
        {"author": i["author"], "url": i["url"], "is_tracked_kol": i["is_tracked_kol"],
         "target_type": i["target_type"]} for i in items
    ])
    return mr.Material(items=items, voices=voices, volume=mr.compute_volume(items, 30))


RAW = """##EXEC_SUMMARY##
Le néoadjuvant domine la conversation [1].
##SO_WHAT##
Roche doit défendre sa position [1][2].
##WHAT_IS_SAID##
Les cliniciens insistent sur la survie [1].
##VOICES##
La conversation est portée par les KOLs [1].
##VOLUME##
Volume stable sur la période [2].
##SUBTOPICS##
- Dépistage organisé [2]
- Survie à 3 ans [1]
##KEY_POSTS##
[1] Première prise de position française
[99] out of range, must be dropped
"""


def test_prompt_hands_the_model_computed_figures_and_demands_citations():
    prompt = mr.build_prompt("What do doctors think about X?", _material())
    assert "VOICE DISTRIBUTION:" in prompt and "VOLUME:" in prompt
    assert "do not" in prompt.lower() and "recount" in prompt.lower()
    assert "CITATIONS ARE MANDATORY" in prompt
    assert "[1]" in prompt and "[2]" in prompt


def test_all_six_required_sections_are_parsed():
    report = mr.parse_report(RAW, _material())
    for key in ("exec_summary", "so_what", "what_is_said", "voices_note", "volume_note"):
        assert report[key], f"missing section: {key}"
    assert len(report["subtopics"]) == 2


def test_computed_sections_come_from_rows_not_from_the_model():
    report = mr.parse_report(RAW, _material())
    assert report["voice_rows"], "voice distribution must be computed"
    assert report["volume"]["total"] == 2
    # The KOL bucket is exact, so the share is not zero.
    assert report["voice_exact_share"] > 0


def test_out_of_range_citations_are_dropped_not_fabricated():
    report = mr.parse_report(RAW, _material())
    assert all(s["n"] <= 2 for s in report["sources"])
    assert len(report["key_posts"]) == 1, "[99] must not become a key post"
    assert "[1]" not in report["key_posts"][0]["why"]


def test_sources_resolve_to_real_urls():
    report = mr.parse_report(RAW, _material())
    by_n = {s["n"]: s for s in report["sources"]}
    assert by_n[1]["url"] == "https://edimark.fr/a"
    assert by_n[2]["source_name"] == "has-sante.fr"


def test_empty_response_yields_an_empty_report_not_a_crash():
    report = mr.parse_report("", mr.Material())
    assert report["subtopics"] == [] and report["key_posts"] == [] and report["sources"] == []


# ── Rendering ─────────────────────────────────────────────

def test_html_contains_all_six_numbered_sections():
    report = mr.parse_report(RAW, _material())
    html = mr.render_html("What do doctors think about X?", report,
                          datetime.now(timezone.utc), 30)
    for heading in ("1. Executive summary", "2. So what", "3. What is being said",
                    "4. Voice distribution", "5. Volume of mentions",
                    "6. Key sub-topics to consider"):
        assert heading in html, f"missing: {heading}"


def test_html_states_the_inference_caveat():
    """A voice chart that presents guesses as facts is worse than none."""
    report = mr.parse_report(RAW, _material())
    html = mr.render_html("q", report, datetime.now(timezone.utc), 30)
    assert "inferred from the" in html


def test_html_escapes_hostile_content():
    material = _material()
    material.items[0]["author"] = "<script>alert(1)</script>"
    report = mr.parse_report(RAW, material)
    html = mr.render_html("<img onerror=x>", report, datetime.now(timezone.utc), 30)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_slugify_makes_a_safe_filename():
    assert mr.slugify("What do doctors think about subcutaneous therapies?") \
        .startswith("what-do-doctors-think")
    assert "/" not in mr.slugify("a/b c") and mr.slugify("") == "query"
