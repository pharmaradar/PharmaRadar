"""Dashboard synthesis reports — KOL, Competitor, Comprehensive.

The client asked for three downloadable PDFs in one shape (Main information /
"So What" / Key Articles & Posts), a deeper level of analysis with actionable
recommendations, and every section traceable to the sources behind it.

Citations are the mechanism for that last part: the model only ever emits an
index, and the URL is resolved from the row afterwards — so a "source" in a
report is always a document that exists.
"""
from datetime import datetime, timezone

import pytest

from app.services import synthesis_report as sr


def _insights():
    return [
        {"id": 1, "target": "Benjamin Besse", "target_type": "kol", "topic": "CBNPC",
         "said": "L'immunothérapie néoadjuvante change la prise en charge du CBNPC résécable.",
         "sentiment": "positive", "category": "clinical_trial",
         "url": "https://www.edimark.fr/article-1", "source_name": "edimark.fr",
         "source_scope": "fr", "date": "2026-08-02"},
        {"id": 2, "target": "AstraZeneca", "target_type": "competitor", "topic": "Imfinzi",
         "said": "AstraZeneca met en avant Imfinzi en périopératoire.",
         "sentiment": "neutral", "category": "other_pharma",
         "url": "https://www.astrazeneca.com/x", "source_name": "astrazeneca.com",
         "source_scope": "global", "date": "2026-08-05"},
        {"id": 3, "target": "Nicolas Girard", "target_type": "kol", "topic": "dépistage",
         "said": "Le dépistage organisé reste insuffisant en France.",
         "sentiment": "negative", "category": "policy",
         "url": "https://www.splf.fr/b", "source_name": "splf.fr",
         "source_scope": "fr", "date": "2026-07-29"},
    ]


RAW = """##MAIN##
- L'immunothérapie néoadjuvante s'impose dans le CBNPC résécable [1]
- AstraZeneca pousse Imfinzi en périopératoire [2]
##SO_WHAT##
Le terrain se déplace vers le périopératoire [1][2].
##RECOMMENDATIONS##
- Préparer un contre-argumentaire face à Imfinzi [2]
- Financer un symposium sur le dépistage [3]
##WATCH##
- Publication des données de survie à 3 ans [1]
##KEY_POSTS##
[1] Première prise de position française sur le néoadjuvant
[2] Signal concurrentiel direct sur notre indication
[99] out of range and must be dropped
"""


# ── Scopes ────────────────────────────────────────────────

def test_every_scope_has_a_spec():
    assert set(sr.SCOPES) == {"kol", "competitor", "comprehensive"}
    for scope in sr.SCOPES:
        spec = sr.spec_for(scope)
        assert spec.title and spec.analyst and spec.target_types


def test_scopes_select_the_right_targets():
    assert sr.spec_for("kol").target_types == ("kol",)
    assert sr.spec_for("competitor").target_types == ("competitor",)
    assert set(sr.spec_for("comprehensive").target_types) == {"kol", "competitor"}


def test_unknown_scope_is_rejected():
    with pytest.raises(ValueError):
        sr.spec_for("everything")


# ── Parsing and citations ─────────────────────────────────

def test_all_sections_are_parsed():
    report = sr.parse_report(RAW, _insights())
    assert len(report["main"]) == 2
    assert len(report["recommendations"]) == 2
    assert len(report["watch"]) == 1
    assert "périopératoire" in report["so_what"]


def test_citations_resolve_to_real_rows():
    """The model emits an index; the URL comes from the row. That is what makes
    'linked to its main sources' true rather than claimed."""
    report = sr.parse_report(RAW, _insights())
    by_n = {s["n"]: s for s in report["sources"]}
    assert set(by_n) == {1, 2, 3}
    assert by_n[1]["url"] == "https://www.edimark.fr/article-1"
    assert by_n[3]["source_name"] == "splf.fr"


def test_out_of_range_citations_are_dropped_not_faked():
    report = sr.parse_report(RAW, _insights())
    assert all(s["n"] <= 3 for s in report["sources"])
    assert len(report["key_posts"]) == 2, "[99] must not become a key post"


def test_key_posts_keep_the_source_row_and_strip_the_marker():
    report = sr.parse_report(RAW, _insights())
    first = report["key_posts"][0]
    assert first["target"] == "Benjamin Besse"
    assert first["url"] == "https://www.edimark.fr/article-1"
    assert first["why"].startswith("Première")
    assert "[1]" not in first["why"]


def test_empty_response_yields_an_empty_report_not_a_crash():
    report = sr.parse_report("", [])
    assert report["main"] == [] and report["key_posts"] == [] and report["sources"] == []


def test_prompt_numbers_every_statement_and_demands_citations():
    prompt = sr.build_prompt(sr.spec_for("kol"), _insights())
    assert "[1]" in prompt and "[3]" in prompt
    assert "CITATIONS ARE MANDATORY" in prompt
    assert "Benjamin Besse" in prompt


# ── Rendering ─────────────────────────────────────────────

def test_html_contains_every_client_requested_section():
    report = sr.parse_report(RAW, _insights())
    html = sr.render_html(sr.spec_for("comprehensive"), report,
                          datetime.now(timezone.utc), 3)
    for heading in ("Main information", "So what", "Recommendations",
                    "Key articles &amp; posts", "Sources"):
        assert heading in html, f"missing section: {heading}"
    # Sources are rendered as real URLs, not as bare [n] markers.
    assert "edimark.fr/article-1" in html


def test_html_escapes_content():
    hostile = [{**_insights()[0], "target": "<script>alert(1)</script>",
                "said": "a & b", "url": "https://x.fr/a?b=1&c=2"}]
    report = sr.parse_report("##MAIN##\n- finding [1]\n##KEY_POSTS##\n[1] why\n", hostile)
    html = sr.render_html(sr.spec_for("kol"), report, datetime.now(timezone.utc), 1)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_report_renders_the_explanation_rather_than_blank_sections():
    html = sr.render_html(sr.spec_for("kol"), sr.parse_report("", []),
                          datetime.now(timezone.utc), 0)
    assert "No data in the last 30 days." in html


def test_window_is_thirty_days():
    """The client's standard reporting period."""
    assert sr.WINDOW_DAYS == 30
