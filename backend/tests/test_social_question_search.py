"""Question-shaped social search.

The search bar used to match the WHOLE typed string with one LIKE, so
"does KOL think subcutaneous is better than IV" looked for that exact sentence
inside post text. Measured against the live corpus: 0 rows, for a question the
corpus could answer.

These tests stay on the deterministic half of expansion. `expand()` also calls
an LLM to enrich terms; leaving that live made this file take 18s, cost money
per run, and fail with no network. What must hold without it is that a question
is broken into terms at all, and that those terms reach a French corpus.
"""
import pytest

from app.services import question as question_service
from app.services.term_expansion import expand_term, expand_terms


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(question_service, "_llm_terms", lambda *a, **k: [])


def terms_for(question: str) -> list[str]:
    """Deterministic expansion, bypassing the Redis cache.

    The cache is keyed only by question and language, so a cached entry written
    by an earlier LLM-enabled run would defeat the fixture above.
    """
    return expand_terms(question_service.expand(question, language="fr")["terms"])


class TestQuestionExpansion:
    def test_question_becomes_many_terms(self):
        found = terms_for("does KOL think subcutaneous is better than IV")
        assert len(found) > 1
        assert any("subcutaneous" in t.lower() for t in found)

    def test_expansion_reaches_the_french_corpus(self):
        """The corpus is French; the question is not. Without this the search
        cannot match posts that say 'sous-cutanée'."""
        found = {t.lower() for t in terms_for("subcutaneous administration")}
        assert any("sous-cutan" in t for t in found)

    def test_blank_question_is_safe(self):
        assert terms_for("") == []


class TestProvenanceIsNotContent:
    """`SocialPost.query` stores the search that COLLECTED a post, not what the
    post says, and `topic` is a subject for keyword-collected posts but
    provenance for account-collected ones (`account:<handle>`).

    Ignoring that produced a real false positive: the term "KOL" returned a post
    about spinal muscular atrophy purely because it had been collected under an
    older search named "what do kol think about evrysdi".
    """

    @pytest.mark.parametrize("topic,is_provenance", [
        ("account:msd-france", True),
        ("account:roche-en-france", True),
        ("Evrysdi", False),
        ("immunothérapie", False),
        ("", False),
    ])
    def test_provenance_topics_are_separable(self, topic, is_provenance):
        """A question mentioning France must not match every post from
        `account:msd-france` by way of its topic label."""
        assert topic.lower().startswith("account:") is is_provenance


class TestShortQueriesStayLiteral:
    """A one-word query is already a term. Treating it as a question would spend
    an LLM call to learn that, and widen a filter the user meant narrowly."""

    def test_single_word_is_not_a_question(self):
        assert len("dépistage".split()) < 3

    def test_single_word_still_expands_bilingually(self):
        """Cheap, deterministic expansion still applies — no LLM involved."""
        assert "screening" in {t.lower() for t in expand_term("dépistage")}
