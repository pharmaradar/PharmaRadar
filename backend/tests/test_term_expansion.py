"""Bilingual expansion — the fix for reports that read thin.

Measured on the live corpus before this existed: the topic "subcutaneous
administration" matched 0 rows, "side effects" 0, "screening" 2. The report
generator was fine; it was being handed almost nothing to write about.
"""
from app.services.term_expansion import expand_term, expand_terms, fold_accents


class TestAccentHandling:
    """Postgres LIKE is accent-sensitive in BOTH directions, so both spellings
    must be emitted or half the corpus stays invisible."""

    def test_folding_strips_diacritics(self):
        assert fold_accents("sous-cutané") == "sous-cutane"
        assert fold_accents("dépistage") == "depistage"

    def test_both_spellings_are_emitted(self):
        variants = {v.lower() for v in expand_term("subcutaneous")}
        # Accented form matches "sous-cutanée" in the database…
        assert "sous-cutané" in variants
        # …and the folded form matches a post that dropped the accent.
        assert "sous-cutane" in variants

    def test_accented_input_also_expands(self):
        """The client may type either language."""
        variants = {v.lower() for v in expand_term("dépistage")}
        assert "screening" in variants


class TestConceptExpansion:
    def test_english_topic_reaches_french_corpus(self):
        """The exact failure that made burning-topic reports thin."""
        variants = {v.lower() for v in expand_term("subcutaneous administration")}
        assert "sous-cutané" in variants

    def test_french_oncology_abbreviations(self):
        assert "cbnpc" in {v.lower() for v in expand_term("NSCLC")}

    def test_multi_word_phrase_matches_inner_concept(self):
        """A phrase in no group still picks up the concept inside it."""
        variants = {v.lower() for v in expand_term("immunotherapy resistance")}
        assert "immunothérapie" in variants

    def test_expansion_never_narrows(self):
        """Safety property: a topic keeps everything it matched before."""
        for term in ("lung cancer", "screening", "some unmapped phrase"):
            assert term in expand_term(term)

    def test_unknown_term_is_returned_unchanged(self):
        assert expand_term("Tecentriq") == ["Tecentriq"]

    def test_blank_yields_nothing(self):
        assert expand_term("   ") == []

    def test_expand_terms_dedupes_across_inputs(self):
        out = expand_terms(["NSCLC", "nsclc", "lung cancer"])
        assert len(out) == len({v.lower() for v in out})
