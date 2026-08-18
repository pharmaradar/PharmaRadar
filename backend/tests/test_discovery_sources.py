"""OpenAlex, French RSS feeds and the embedding padding — the last untested lanes.

Grouped because they share one property: each is a free, keyless source feeding
the corpus, and each fails silently rather than loudly. A name-matching slip in
OpenAlex marks a tracked KOL as an undiscovered one; a relevance filter that is
too tight empties the French press lane without an error; a mis-sized vector
poisons pgvector similarity for every row written after it.
"""
import pytest

from app.services import embedder, fr_feeds, openalex


# ══ OpenAlex — telling a tracked KOL from a new one ══════
#
# The product decision this drives: "who leads this topic in France that we do
# NOT already follow". Marking someone tracked when they are not hides a real
# discovery; marking an untracked person tracked loses them entirely.

def test_the_same_person_matches_across_name_order():
    """Targets are stored surname-first ("BESSE BENJAMIN"); OpenAlex returns
    "Benjamin Besse". String comparison would call one person two."""
    assert openalex._is_same_person("Benjamin Besse", "BESSE BENJAMIN") is True


def test_accents_do_not_split_one_person_in_two():
    assert openalex._is_same_person("Fabrice Barlési", "BARLESI FABRICE") is True


def test_a_middle_name_on_one_side_still_matches():
    """"BENNOUNA LOURIDI JAAFAR" and "Jaafar Bennouna" are the same clinician."""
    assert openalex._is_same_person("Jaafar Bennouna", "BENNOUNA LOURIDI JAAFAR") is True


def test_two_different_people_do_not_match():
    assert openalex._is_same_person("Nicolas Girard", "BESSE BENJAMIN") is False


def test_a_shared_surname_alone_is_not_a_match():
    """The failure that matters: French surnames repeat, and treating a surname
    collision as identity would silently merge two researchers."""
    assert openalex._is_same_person("Pierre Girard", "GIRARD NICOLAS") is False


def test_initials_are_ignored_because_one_letter_matches_too_much():
    """"M. Pérol" must not match every tracked name beginning with M."""
    assert openalex._name_key("M. Pérol") == frozenset({"perol"})


def test_hyphenated_names_collapse_to_their_parts():
    assert openalex._name_key("Moro-Sibilot") == frozenset({"moro", "sibilot"})


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_an_empty_name_never_matches_anything(empty):
    assert openalex._is_same_person(empty, "BESSE BENJAMIN") is False
    assert openalex._is_same_person("Benjamin Besse", empty) is False


def test_a_name_of_only_initials_never_matches():
    """Nothing survives the length filter, so there is no evidence either way —
    and "no evidence" must not read as "same person"."""
    assert openalex._is_same_person("A. B.", "BESSE BENJAMIN") is False


# ══ French press feeds ═══════════════════════════════════

def test_a_relevant_french_item_is_kept():
    assert fr_feeds.is_relevant(
        "Cancer du poumon : nouveaux résultats", "Étude sur l'immunothérapie") is True


def test_an_unrelated_item_is_dropped():
    """These feeds are general health press; without a filter the lane fills
    with material that has nothing to do with the client's field."""
    assert fr_feeds.is_relevant(
        "Résultats du championnat régional", "Le club local a gagné") is False


def test_relevance_reads_the_summary_as_well_as_the_title():
    """Headlines are often teasers that name nothing."""
    assert fr_feeds.is_relevant("Une avancée majeure", "en oncologie thoracique") is True


def test_relevance_is_case_insensitive():
    assert fr_feeds.is_relevant("CANCER DU POUMON", "") is True


@pytest.mark.parametrize("empty", [("", ""), (None, None)])
def test_empty_items_are_not_relevant(empty):
    assert fr_feeds.is_relevant(*empty) is False


def test_html_is_stripped_from_feed_summaries():
    """RSS summaries carry markup; stored raw it reaches the LLM and the PDF."""
    out = fr_feeds._strip_html("<p>Immunothérapie <b>avancée</b></p>")
    assert "<" not in out and ">" not in out
    assert "Immunothérapie" in out


def test_stripping_html_leaves_plain_text_alone():
    assert fr_feeds._strip_html("Cancer du poumon") == "Cancer du poumon"


def test_feed_timestamps_are_timezone_aware():
    """Naive datetimes compared against tz-aware ones raise at runtime, and this
    value is compared against post dates."""
    assert fr_feeds.now().tzinfo is not None


# ══ Embedding vectors ════════════════════════════════════
#
# pgvector columns are fixed-width. A vector of the wrong length either fails
# the insert or, worse, silently distorts every similarity comparison against it.

def test_a_short_vector_is_padded_to_the_column_width():
    padded = embedder._pad([0.1, 0.2])
    assert len(padded) == embedder.EMBEDDING_DIM
    assert padded[0] == 0.1
    assert padded[-1] == 0.0


def test_a_long_vector_is_truncated_to_the_column_width():
    padded = embedder._pad([0.5] * (embedder.EMBEDDING_DIM + 100))
    assert len(padded) == embedder.EMBEDDING_DIM


def test_a_correctly_sized_vector_passes_through_unchanged():
    vec = [0.25] * embedder.EMBEDDING_DIM
    assert embedder._pad(vec) == vec


def test_an_empty_vector_becomes_zeroes_rather_than_a_short_row():
    padded = embedder._pad([])
    assert len(padded) == embedder.EMBEDDING_DIM
    assert set(padded) == {0.0}


def test_embedding_an_empty_batch_costs_nothing():
    """Called per run; a provider round-trip for zero texts is pure waste."""
    assert embedder.embed_texts([]) == []
