"""Europe PMC / ClinicalTrials.gov ingestion — author identity above all.

This lane attributes publications to named clinicians, and getting it wrong is
not a near miss: the module's own docstring records that `AUTH:"Moro S"` returns
31 papers by Russano/Sturlese et al. while `AUTH:"Moro-Sibilot D"` returns the
right 255. A publication credited to the wrong researcher is invisible to the
reader and ends up in a client-facing brief.

French names are what make it hard — compound surnames ("MORO SIBILOT DENIS"),
and no way to tell from the string where the surname ends. The design answer is
to offer every plausible reading and then VERIFY against the returned author
list rather than trust a guess, so most of what is pinned here is the
verification, not the guessing.

The network is never touched: `_get_json` is patched.
"""
import pytest

from app.services import literature as lit


# ── Name folding ──────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Moro-Sibilot", "moro sibilot"),
    ("Barlési", "barlesi"),
    ("BESSE", "besse"),
    ("Jean-Louis", "jean louis"),
    ("A.B. Smith", "a b  smith"),
])
def test_folding_normalises_accents_hyphens_and_dots(raw, expected):
    """Comparison happens after folding, so "Moro-Sibilot" in our target list has
    to match "Moro-Sibilot D" in an author string regardless of punctuation."""
    assert lit._fold(raw) == expected


def test_folding_survives_empty_input():
    assert lit._fold("") == ""
    assert lit._fold(None) == ""


# ── Candidate generation ──────────────────────────────────

def test_a_compound_surname_offers_the_hyphenated_reading_first():
    """The case that motivated the whole design. "Moro S" is a different person;
    the hyphenated form must be tried before the naive first-token split."""
    candidates = lit._author_candidates("MORO SIBILOT DENIS")
    assert ("Moro-Sibilot", "D") in candidates
    assert candidates.index(("Moro-Sibilot", "D")) < candidates.index(("Moro", "S"))


def test_a_simple_name_reads_surname_first():
    assert ("Girard", "N") in lit._author_candidates("GIRARD NICOLAS")


def test_a_middle_name_reading_is_offered():
    """"BENNOUNA LOURIDI JAAFAR" publishes as "Bennouna J", which neither the
    compound reading nor the naive split produces."""
    assert ("Bennouna", "J") in lit._author_candidates("BENNOUNA LOURIDI JAAFAR")


def test_candidates_are_unique():
    """Duplicates would spend a network round-trip re-testing the same clause."""
    candidates = lit._author_candidates("MORO SIBILOT DENIS")
    keys = [(s.lower(), i) for s, i in candidates]
    assert len(keys) == len(set(keys))


def test_a_single_token_name_still_produces_a_candidate():
    assert lit._author_candidates("Barlesi") == [("Barlesi", "")]


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_an_empty_name_produces_no_candidates(empty):
    assert lit._author_candidates(empty) == []


def test_a_comma_separated_name_is_handled():
    """Some sources store "SURNAME, First"."""
    assert ("Girard", "N") in lit._author_candidates("GIRARD, NICOLAS")


# ── Resolution: verify, never trust ───────────────────────

@pytest.fixture(autouse=True)
def _clear_cache():
    lit._AUTHOR_CACHE.clear()
    yield
    lit._AUTHOR_CACHE.clear()


def _payload(*author_strings):
    return {"resultList": {"result": [{"authorString": a} for a in author_strings]}}


def test_a_candidate_is_accepted_only_when_the_authors_confirm_it(monkeypatch):
    monkeypatch.setattr(lit, "_get_json",
                        lambda url: _payload("Moro-Sibilot D, Levra M, Toffart A."))
    assert lit.resolve_author("MORO SIBILOT DENIS", "lung cancer") == 'AUTH:"Moro-Sibilot D"'


def test_hits_alone_do_not_prove_identity(monkeypatch):
    """THE regression. A wrong surname returns plenty of papers — by someone
    else. Results must be rejected when the surname is absent from the author
    list, however many of them there are."""
    monkeypatch.setattr(lit, "_get_json",
                        lambda url: _payload("Russano M, Sturlese A, Bianchi P.",
                                             "Sturlese A, Russano M."))
    assert lit.resolve_author("MORO SIBILOT DENIS", "lung cancer") is None


def test_resolution_falls_through_to_a_later_candidate(monkeypatch):
    """The first reading failing is expected, not exceptional — that is why
    several are offered."""
    seen = []

    def fake(url):
        seen.append(url)
        # Only the plain "Bennouna J" reading corresponds to a real author; the
        # compound readings return somebody else entirely.
        if "Bennouna%20J" in url or 'Bennouna J' in url:
            return _payload("Bennouna J, Girard N.")
        return _payload("Unrelated A, Other B.")

    monkeypatch.setattr(lit, "_get_json", fake)
    assert lit.resolve_author("BENNOUNA LOURIDI JAAFAR", "lung cancer") == 'AUTH:"Bennouna J"'
    assert len(seen) > 1, "later candidates were never tried"


def test_an_unresolvable_name_returns_none_rather_than_a_guess(monkeypatch):
    """No attribution is the correct output. A best-effort guess here credits
    another researcher's work to this KOL."""
    monkeypatch.setattr(lit, "_get_json", lambda url: {"resultList": {"result": []}})
    assert lit.resolve_author("NOBODY HERE", "lung cancer") is None


def test_a_network_failure_does_not_resolve_anything(monkeypatch):
    def boom(url):
        raise RuntimeError("europepmc unreachable")
    monkeypatch.setattr(lit, "_get_json", boom)
    assert lit.resolve_author("GIRARD NICOLAS", "lung cancer") is None


def test_resolution_is_cached_per_name(monkeypatch):
    """Verification costs a round-trip per candidate; repeating it for every
    sync of the same person would multiply the whole sweep."""
    calls = {"n": 0}

    def counting(url):
        calls["n"] += 1
        return _payload("Girard N, Besse B.")

    monkeypatch.setattr(lit, "_get_json", counting)
    lit.resolve_author("GIRARD NICOLAS", "lung cancer")
    first = calls["n"]
    lit.resolve_author("GIRARD NICOLAS", "lung cancer")
    assert calls["n"] == first, "the second lookup hit the network again"


def test_an_unresolved_name_is_also_cached(monkeypatch):
    """Otherwise every sweep re-pays the full candidate ladder for a name that
    is never going to resolve."""
    calls = {"n": 0}

    def counting(url):
        calls["n"] += 1
        return {"resultList": {"result": []}}

    monkeypatch.setattr(lit, "_get_json", counting)
    lit.resolve_author("NOBODY HERE", "lung cancer")
    first = calls["n"]
    lit.resolve_author("NOBODY HERE", "lung cancer")
    assert calls["n"] == first


def test_accented_authors_match_their_unaccented_index_form(monkeypatch):
    """Europe PMC indexes "Barlesi F"; our target list stores "Barlési"."""
    monkeypatch.setattr(lit, "_get_json", lambda url: _payload("Barlesi F, Planchard D."))
    assert lit.resolve_author("Barlési", "lung cancer") is not None


# ── Journal naming ────────────────────────────────────────

def test_the_short_journal_name_is_preferred():
    """A reader recognises "Ann Oncol", not the full title."""
    item = {"journalInfo": {"journal": {"medlineAbbreviation": "Ann Oncol",
                                       "title": "Annals of Oncology"}}}
    assert lit._journal_of(item) == "Ann Oncol"


def test_the_full_title_is_used_when_there_is_no_abbreviation():
    item = {"journalInfo": {"journal": {"title": "Annals of Oncology"}}}
    assert lit._journal_of(item) == "Annals of Oncology"


def test_a_missing_journal_block_does_not_raise():
    assert lit._journal_of({}) in ("", None)


# ── Window helper ─────────────────────────────────────────

def test_since_datetime_is_in_the_past_and_timezone_aware():
    from datetime import datetime, timezone

    stamp = lit.since_datetime(30)
    assert stamp < datetime.now(timezone.utc)
    assert stamp.tzinfo is not None


def test_the_right_surname_with_the_wrong_initial_is_rejected(monkeypatch):
    """REGRESSION. Verification used to check the surname only, so asking for
    AUTH:"Bennouna J" and receiving a page of papers by Bennouna L passed — and
    those papers were filed under the wrong KOL.

    This is the more common French failure than a wrong surname: shared
    surnames are frequent, and every result still contains the surname."""
    monkeypatch.setattr(lit, "_get_json", lambda url: _payload(
        "Bennouna L, Dupont M.", "Bennouna L, Martin P."))
    assert lit.resolve_author("BENNOUNA JAAFAR", "lung cancer") is None


def test_a_matching_initial_is_still_accepted(monkeypatch):
    monkeypatch.setattr(lit, "_get_json", lambda url: _payload("Bennouna J, Dupont M."))
    assert lit.resolve_author("BENNOUNA JAAFAR", "lung cancer") == 'AUTH:"Bennouna J"'


@pytest.mark.parametrize("author_string", [
    "Bennouna JA, Dupont M.",      # two initials
    "Bennouna Jaafar, Dupont M.",  # full given name
])
def test_the_same_person_written_differently_still_matches(monkeypatch, author_string):
    """The check must pin the pair without rejecting legitimate spellings."""
    monkeypatch.setattr(lit, "_get_json", lambda url: _payload(author_string))
    assert lit.resolve_author("BENNOUNA JAAFAR", "lung cancer") is not None


def test_a_compound_surname_still_verifies_with_its_initial(monkeypatch):
    """Folding turns "Moro-Sibilot D" into "moro sibilot d", so the compound
    reading has to survive the stricter check."""
    monkeypatch.setattr(lit, "_get_json",
                        lambda url: _payload("Moro-Sibilot D, Levra M."))
    assert lit.resolve_author("MORO SIBILOT DENIS", "lung cancer") == 'AUTH:"Moro-Sibilot D"'
