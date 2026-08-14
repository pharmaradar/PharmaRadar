"""Turning a typed question into searchable terms.

The client types a sentence and expects a report. Before this, the whole
sentence was used as a single LIKE pattern, so his own example question —
"What do doctors think about subcutaneous therapies in lung cancer?" — matched
nothing and the report came back empty. That was the "no significant difference"
he reported.
"""
import pytest

from app.services.question import (
    content_terms, is_specific, phrase_candidates, split_by_specificity,
)


def test_question_scaffolding_is_stripped():
    terms = content_terms("What do doctors think about subcutaneous therapies in lung cancer?")
    lowered = [t.lower() for t in terms]
    assert "subcutaneous" in lowered and "cancer" in lowered
    for filler in ("what", "do", "think", "about", "in", "the"):
        assert filler not in lowered


def test_french_questions_work_too():
    terms = [t.lower() for t in
             content_terms("Que pensent les médecins de l'immunothérapie dans le CBNPC ?")]
    assert "cbnpc" in terms
    assert any("immunothérapie" in t for t in terms)
    assert "que" not in terms and "les" not in terms


def test_clinical_shorthand_survives_the_length_filter():
    """CBNPC, ALK, EGFR are short but are the whole point of the question."""
    assert "ALK" in content_terms("ALK rearranged lung cancer")
    assert "EGFR" in content_terms("EGFR resistance mechanisms")


def test_years_are_kept_but_stray_numbers_are_not():
    terms = content_terms("Was the ATOMIC study discussed at ASCO 2026?")
    assert "2026" in terms
    assert "7" not in content_terms("top 7 studies")


def test_phrases_are_generated_for_precision():
    """Two adjacent words are far rarer than either alone."""
    phrases = phrase_candidates("What do doctors think about subcutaneous therapies in lung cancer?")
    assert "lung cancer" in phrases


@pytest.mark.parametrize("term,expected", [
    ("lung cancer", True),      # a phrase is always discriminating
    ("ATOMIC", True),
    ("CBNPC", True),
    ("study", False),           # matches almost every row in a pharma corpus
    ("cancer", False),
    ("patients", False),
    ("médecins", False),
])
def test_specificity_classification(term, expected):
    assert is_specific(term) is expected


def test_generic_terms_are_held_back_as_fallback():
    """Searching "study" first fills the caps with unrelated material — a question
    about the ATOMIC trial retrieved 104 unrelated items that way."""
    specific, fallback = split_by_specificity(
        ["ATOMIC study", "ATOMIC", "ASCO 2026", "study", "cancer"])
    assert "ATOMIC" in specific and "ASCO 2026" in specific
    assert set(fallback) == {"study", "cancer"}


def test_empty_and_junk_questions_do_not_crash():
    assert content_terms("") == []
    assert content_terms("   ?  ") == []
    assert phrase_candidates("") == []
    assert split_by_specificity([]) == ([], [])
