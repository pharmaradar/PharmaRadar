"""BDPM ingestion — the French drug registry, HAS rulings and ANSM shortages.

Three source-file traps are pinned here, because each one corrupts data without
raising anything, and all three were hit while building this lane:

  the download URL 404s with an HTML body   -> parses to zero rows, silently
  the encoding is cp1252, not latin-1        -> every French apostrophe mangled
  two date formats in the same download      -> half the dates become NULL

The files themselves are never fetched — `fetch_file` is patched with real
sample rows copied out of the live files.
"""
import pytest

from app.services import bdpm


# ── Content validation ────────────────────────────────────

def test_an_html_error_page_is_not_accepted_as_data():
    """The documented telechargement.php endpoint returns HTTP 404 with a 28KB
    HTML body. Trusting the status code — or a "file exists and is non-empty"
    cache check — stores markup as data and every parse yields nothing."""
    assert bdpm._looks_like_data("<!doctype html>\n<html lang='fr'>...") is False
    assert bdpm._looks_like_data("<html><body>Not found</body></html>") is False


def test_real_tabular_content_is_accepted():
    assert bdpm._looks_like_data("65150617\tCT-21782\tRéévaluation\t20260715\tIV\ttext") is True


def test_a_body_with_no_tabs_is_rejected():
    """Whatever it is, it is not one of these files."""
    assert bdpm._looks_like_data("some plain prose with no structure at all") is False


def test_fetch_raises_loudly_on_an_error_page(monkeypatch):
    """The failure this guards is silence, so it must not return empty."""
    class FakeResponse:
        def read(self): return b"<!doctype html><html>404</html>"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(bdpm.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    with pytest.raises(ValueError, match="did not return tabular data"):
        bdpm.fetch_file("asmr")


# ── Encoding ──────────────────────────────────────────────

def test_the_curly_apostrophe_survives_decoding(monkeypatch):
    """REGRESSION. These files are Windows-1252, not Latin-1. The two are nearly
    identical so latin-1 decodes without raising, but 0x92 is a curly apostrophe
    in cp1252 and a control character in latin-1 — the live ASMR file has 3,868
    such bytes. Decoded wrongly, "n'apporte pas" becomes "napporte pas" in every
    French reasoning text, with no error anywhere.
    """
    body = b"65150617\tCT-21782\tmotif\t20260715\tV\tBEYFORTUS n\x92apporte pas une amelioration"

    class FakeResponse:
        def read(self): return body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(bdpm.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    rows = bdpm.fetch_file("asmr")
    assert "n’apporte pas" in rows[0][5]
    assert "\x92" not in rows[0][5]


# ── Dates ─────────────────────────────────────────────────

def test_rulings_use_compact_dates():
    assert bdpm._date_compact("20260715").isoformat() == "2026-07-15"


def test_shortages_use_slashed_dates():
    """Different format in the same download — parsing both with one function
    would null out every date on one of the two feeds."""
    assert bdpm._date_slashed("30/07/2026").isoformat() == "2026-07-30"


@pytest.mark.parametrize("bad", ["", None, "not-a-date", "2026-07-15", "99999999"])
def test_unparseable_dates_return_none_rather_than_raising(bad):
    assert bdpm._date_compact(bad) is None


@pytest.mark.parametrize("bad", ["", None, "31/31/2026", "20260715"])
def test_unparseable_slashed_dates_return_none(bad):
    assert bdpm._date_slashed(bad) is None


# ── Text cleaning ─────────────────────────────────────────

def test_html_breaks_are_replaced_not_deleted():
    """HAS stores <br> inside a tab-separated field. Deleting without replacing
    runs sentences together."""
    out = bdpm._clean("Compte tenu :<br>de la démonstration<br/>d'une supériorité")
    assert "<br" not in out
    assert "tenu : de la" in out


def test_cleaning_leaves_plain_french_text_intact():
    text = "ENHERTU apporte une amélioration mineure (ASMR IV)"
    assert bdpm._clean(text) == text


# ── Brand matching ────────────────────────────────────────

@pytest.mark.parametrize("registry_name,expected", [
    ("TECENTRIQ 1 875 mg, solution injectable", "Tecentriq"),
    ("KEYTRUDA 25 mg/ml, solution à diluer", "Keytruda"),
    ("IMFINZI 50 mg/ml, solution à diluer pour perfusion", "Imfinzi"),
])
def test_tracked_brands_are_matched_from_the_registry_name(registry_name, expected):
    brand = bdpm.match_brand(registry_name)
    assert brand is not None and brand.name == expected


def test_untracked_drugs_are_ignored():
    """The register covers 15,857 specialities across all of medicine. Keeping
    everything would bury the signal under dermatology and vaccines."""
    assert bdpm.match_brand("AMIKACINE VIATRIS 50 mg/1 ml, solution injectable") is None
    assert bdpm.match_brand("IXIARO, suspension injectable. Vaccin") is None


def test_matching_is_word_anchored_not_substring():
    """Same rule brands.py already applies: "Opdivo" must not be found inside
    "Opdivoqtig", which is a different product."""
    assert bdpm.match_brand("OPDIVOQTIG 100 mg, solution") is None


def test_brand_owner_comes_through_for_the_ours_vs_theirs_split():
    assert bdpm.match_brand("TECENTRIQ 840 mg").owner == "roche"
    assert bdpm.match_brand("KEYTRUDA 100 mg").owner == "MSD"


# ── Deduplication key ─────────────────────────────────────

def test_the_same_ruling_hashes_identically():
    """Source files are full snapshots re-read nightly; a re-sync must add zero."""
    a = bdpm._hash("asmr", "69209340", "CT-21782", "III", "2026-06-24")
    b = bdpm._hash("asmr", "69209340", "CT-21782", "III", "2026-06-24")
    assert a == b


def test_the_rating_is_part_of_the_key():
    """One opinion can grant different ASMRs to different presentations on the
    same day — collapsing them would drop a real ruling."""
    base = ("asmr", "69209340", "CT-21782")
    assert bdpm._hash(*base, "IV", "2026-06-24") != bdpm._hash(*base, "V", "2026-06-24")


def test_different_drugs_under_one_opinion_stay_distinct():
    assert (bdpm._hash("asmr", "65150617", "CT-21782", "IV", "2026-07-15")
            != bdpm._hash("asmr", "62438151", "CT-21782", "IV", "2026-07-15"))
