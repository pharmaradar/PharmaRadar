"""Transparence Santé — identity resolution must refuse rather than guess.

This feature attributes money to named clinicians in client-facing competitive
briefs. A missing figure is a gap the reader can see; a figure belonging to a
different doctor is invisible and wrong, and it is the kind of error that costs
the platform its credibility rather than a bug report. So the tests here are
mostly about the refusals.

Three real defects found while building it are pinned as regressions:
  - folding destroyed hyphens, so "Jean-Louis Pujol" searched for surname
    "LOUIS PUJOL" and matched nobody;
  - targets are stored surname-first ("CORTOT ALEXIS") while a typed name reads
    given-first, and assuming either one silently found nothing;
  - grouping payments by trade name split "ROCHE SAS" from "ROCHE" and ranked
    Roche BELOW AstraZeneca, from rows that were each individually correct.

The register is never called here — `_get` is patched — so these run offline and
assert the decisions rather than the network.
"""
import pytest

from app.services import transparence as tr


# ── Name folding ──────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Barlési", "BARLESI"),
    ("Gérard", "GERARD"),
    ("  fabrice   barlési ", "FABRICE BARLESI"),
    ("Zalcman", "ZALCMAN"),
])
def test_accents_and_spacing_are_normalised(raw, expected):
    """The register stores unaccented uppercase, so a stored "Barlési" would
    never match on a literal comparison."""
    assert tr.fold(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Jean-Louis", "JEAN-LOUIS"),
    ("Anne-Marie", "ANNE-MARIE"),
    ("O'Brien", "O'BRIEN"),
])
def test_hyphens_and_apostrophes_survive_folding(raw, expected):
    """REGRESSION. These are part of French names, not punctuation. The register
    returns 'JEAN-FRANCOIS' and 'ANNE-MARIE' verbatim; folding the hyphen to a
    space turned "Jean-Louis Pujol" into surname "LOUIS PUJOL" and matched
    nobody — silently losing every clinician with a compound given name."""
    assert tr.fold(raw) == expected


# ── Name ordering ─────────────────────────────────────────

def test_both_name_orderings_are_tried():
    """REGRESSION. Targets are stored surname-first ("CORTOT ALEXIS") but a
    typed name reads given-first ("Fabrice Barlési"). Committing to one
    convention made the other silently resolve to nothing, which is
    indistinguishable from "this KOL has no declarations"."""
    orderings = tr.name_orderings("CORTOT ALEXIS")
    assert ("CORTOT", "ALEXIS") in orderings
    assert ("ALEXIS", "CORTOT") in orderings


def test_compound_surnames_split_at_both_ends():
    orderings = tr.name_orderings("BENNOUNA LOURIDI JAAFAR")
    assert ("BENNOUNA LOURIDI", "JAAFAR") in orderings
    assert ("LOURIDI JAAFAR", "BENNOUNA") in orderings


@pytest.mark.parametrize("name", ["", "   ", "Cher"])
def test_a_single_token_name_is_not_searchable(name):
    assert tr.name_orderings(name) == []


# ── Resolution: when to refuse ────────────────────────────

def _groups(monkeypatch, results):
    """Patch the register with one canned grouped response."""
    monkeypatch.setattr(tr, "_get", lambda params: {"results": results})


def _rpps(identifier, n):
    return {"beneficiaire_identifiant": identifier, "beneficiaire_type": "RPPS/ADELI", "n": n}


def test_a_dominant_identifier_resolves(monkeypatch):
    _groups(monkeypatch, [_rpps("10003416467", 224), _rpps("99999999999", 2)])
    out = tr.resolve_rpps("BARLESI FABRICE")
    assert out["status"] == "resolved"
    assert out["rpps"] == "10003416467"


def test_a_split_name_refuses_to_pick_a_side(monkeypatch):
    """Two clinicians sharing a name is exactly where a confident wrong answer
    would be produced. Nothing is shown instead."""
    _groups(monkeypatch, [_rpps("11111111111", 50), _rpps("22222222222", 45)])
    out = tr.resolve_rpps("MARTIN PASCAL")
    assert out["status"] == "ambiguous"
    assert out["rpps"] is None


def test_too_few_records_is_not_evidence(monkeypatch):
    """One record out of one is 100% and means nothing."""
    _groups(monkeypatch, [_rpps("11111111111", 1)])
    out = tr.resolve_rpps("RARE NAME")
    assert out["status"] == "ambiguous"
    assert out["rpps"] is None


def test_declarations_without_a_national_id_cannot_pin_anyone(monkeypatch):
    """A declarer-invented 'AUTRE' id identifies nobody, and a null one even
    less. The name exists in the register but cannot be tied to one person."""
    _groups(monkeypatch, [
        {"beneficiaire_identifiant": None, "beneficiaire_type": "AUTRE", "n": 40},
        {"beneficiaire_identifiant": "FR19824319065", "beneficiaire_type": "AUTRE", "n": 5},
    ])
    out = tr.resolve_rpps("SOMEONE UNIDENTIFIED")
    assert out["status"] == "ambiguous"
    assert out["rpps"] is None


def test_a_name_absent_from_the_register_is_not_found(monkeypatch):
    _groups(monkeypatch, [])
    assert tr.resolve_rpps("NOBODY HERE")["status"] == "not_found"


def test_a_failed_lookup_does_not_become_a_resolution(monkeypatch):
    """The register being down must not silently pin or clear an identity."""
    def boom(params):
        raise RuntimeError("register unreachable")
    monkeypatch.setattr(tr, "_get", boom)
    out = tr.resolve_rpps("BARLESI FABRICE")
    assert out["status"] == "unresolved"
    assert out["rpps"] is None


def test_each_ordering_matching_a_different_person_refuses(monkeypatch):
    """A name whose halves are both real surnames. Confidently choosing one
    would attribute another clinician's payments."""
    calls = {"n": 0}

    def alternating(params):
        calls["n"] += 1
        return {"results": [_rpps("11111111111" if calls["n"] == 1 else "22222222222", 60)]}

    monkeypatch.setattr(tr, "_get", alternating)
    out = tr.resolve_rpps("MARTIN BERNARD")
    assert out["status"] == "ambiguous"
    assert out["rpps"] is None


def test_dominance_threshold_is_strict_enough_to_matter():
    """A coin-flip split must not qualify. Guards someone 'tuning' the constant
    down until ambiguous cases start resolving."""
    assert tr._DOMINANCE >= 0.8
    assert tr._MIN_RECORDS >= 2


# ── Payment normalisation ─────────────────────────────────

def test_a_usable_row_normalises():
    row = {"id": "d1", "montant": 1500.5, "date": "2024-04-04",
           "date_publication": "2024-06-01", "raison_sociale": "ROCHE SAS",
           "numero_siren": "552012031", "motif_lien_interet": "Contrat d'intervenant",
           "lien_interet": "remuneration", "ville": "VILLEJUIF",
           "beneficiaire_identifiant": "10003416467"}
    out = tr.normalise_payment(row)
    assert out["declaration_id"] == "d1"
    assert out["amount_eur"] == 1500.5
    assert out["company_siren"] == "552012031"
    assert out["paid_on"].isoformat() == "2024-04-04"


@pytest.mark.parametrize("row", [
    {"montant": 100},                                   # no id -> cannot dedup
    {"id": "d2"},                                       # no amount -> not a payment
    {"id": "d3", "montant": None},
    {"id": "d4", "montant": "not-a-number"},
])
def test_unusable_rows_are_dropped_not_zeroed(row):
    """Storing these as €0 would understate a company's real spend while looking
    like a measurement."""
    assert tr.normalise_payment(row) is None


def test_a_malformed_date_does_not_reject_the_payment():
    """The amount is the point; a bad date costs a filter, not the record."""
    out = tr.normalise_payment({"id": "d5", "montant": 10, "date": "not-a-date"})
    assert out is not None and out["paid_on"] is None


# ── Company grouping ──────────────────────────────────────

def test_companies_group_by_siren_not_trade_name():
    """REGRESSION. The register files the same legal entity as both "ROCHE SAS"
    and "ROCHE" (SIREN 552012031). Grouped by name, Roche totalled €569,841 and
    ranked BELOW AstraZeneca's €788,814; grouped by SIREN it is €803,701 and
    ranks above. Every row was individually correct and the conclusion was
    wrong — which is the failure mode this whole feature has to avoid."""
    sql = tr.company_key_sql()
    assert "company_siren" in sql
    assert "COALESCE" in sql.upper()


def test_foreign_affiliates_without_a_siren_keep_their_own_identity():
    """ROCHE SUISSE / ROCHE MAROC carry no SIREN — they are separate legal
    entities outside the client's French remit, so the fallback key is their
    name rather than the French company's."""
    assert "UPPER(company)" in tr.company_key_sql()
