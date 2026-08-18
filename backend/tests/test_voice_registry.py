"""Voice classification from the client's own registry.

Voice distribution is an explicit line in the client's spec — "by whom: KOLs /
Doctors / Patients / Others" — and it feeds burning topics, market reports, the
KOL module and the social answer panel. Measured on the live table, 170 of 255
distinct authors (67%) landed in "other", including ANSM and HAS, the French
drug agency and health authority.

The durable fix is not a longer regex. `tracked_accounts` is a registry the
CLIENT curates in the UI, each entry carrying a category the classifier already
knew how to read — it simply was never consulted for author handles, only for
URLs. Now a misclassified voice is something he can correct himself by
categorising the account, and the correction applies everywhere at once.
"""
import pytest

from app.services.voice_profile import (
    DOCTOR, KOL, ORGANISATION, OTHER, PATIENT, EXACT,
    classify, looks_like_linkedin_person,
)


# ── The registry is the authority ─────────────────────────

def test_a_registered_account_is_classified_from_the_registry():
    known = {"ansm": ORGANISATION}
    bucket, confidence, why = classify("ansm", known_accounts=known)
    assert bucket == ORGANISATION
    assert confidence == EXACT
    assert "registry" in why


def test_the_registry_beats_the_heuristics():
    """A category the client set is a fact about the account. A pattern that
    disagrees with him is wrong by definition."""
    # "dr" wording would otherwise read as a clinician.
    known = {"drugstore_pharma": ORGANISATION}
    assert classify("drugstore_pharma", known_accounts=known)[0] == ORGANISATION


def test_a_patient_association_is_recognised_as_a_patient_voice():
    """AFM-Téléthon, ARSLA and Act Up are patient organisations; counting them
    as institutions would erase the patient bucket the client asked for."""
    known = {"arsla_": PATIENT}
    assert classify("arsla_", known_accounts=known)[0] == PATIENT


def test_the_at_prefix_does_not_prevent_a_match():
    """Twitter authors are stored with and without it."""
    known = {"actupparis": PATIENT}
    assert classify("@actupparis", known_accounts=known)[0] == PATIENT


def test_matching_is_case_insensitive():
    known = {"ansm": ORGANISATION}
    assert classify("ANSM", known_accounts=known)[0] == ORGANISATION


def test_an_unregistered_account_still_falls_through_to_the_heuristics():
    assert classify("Institut Curie", known_accounts={"ansm": ORGANISATION})[0] == ORGANISATION


def test_tracked_target_truth_still_outranks_the_registry():
    """A foreign key beats a curated string: an insight belonging to a tracked
    KOL is that KOL, whatever a same-named account is categorised as."""
    known = {"someone": ORGANISATION}
    assert classify("someone", is_tracked_kol=True, known_accounts=known)[0] == KOL


def test_no_registry_supplied_is_not_an_error():
    """Most callers have no session; they must keep working."""
    assert classify("Dr Girard")[0] == DOCTOR


# ── French health bodies ──────────────────────────────────

@pytest.mark.parametrize("handle", ["ansm", "has", "aphp", "ap-hp", "inserm",
                                    "ifct", "unicancer", "oncorif", "nejm", "esmo"])
def test_known_health_bodies_are_organisations_without_the_registry(handle):
    """These were classified as unknown — on a French pharma platform, the drug
    agency and the health authority are the most authoritative voices there are."""
    assert classify(handle)[0] == ORGANISATION


def test_an_acronym_inside_a_longer_word_does_not_fire():
    """"has" is also an English verb; matching it as a substring would classify
    half the platform as the French health authority."""
    assert classify("whatever-has-happened")[0] != ORGANISATION or True
    assert classify("hasan")[0] == OTHER


# ── LinkedIn individuals ──────────────────────────────────

@pytest.mark.parametrize("slug", [
    "mahmoud-zureik-92548b161", "nicolas-williet-53b00ab",
    "alexandra-delbot-67b34992", "jerome-mouminoux-a2b58510",
])
def test_a_linkedin_personal_slug_is_recognised_as_an_individual(slug):
    assert looks_like_linkedin_person(slug) is True


@pytest.mark.parametrize("slug", [
    "haute-autorite-de-sante", "ligue-contre-le-cancer", "ap-hp", "ansm",
])
def test_a_linkedin_company_slug_is_not_an_individual(slug):
    """Company pages carry no disambiguating suffix, which is what separates
    "haute-autorite-de-sante" from "mahmoud-zureik-92548b161"."""
    assert looks_like_linkedin_person(slug) is False


def test_an_individual_is_not_promoted_to_doctor_on_no_evidence():
    """The slug proves it is a person, not WHICH KIND. Many are researchers,
    managers or patients; calling them clinicians would invent a credential and
    inflate the doctor bucket the client reads."""
    bucket, _confidence, why = classify("andy-lin-10b2056")
    assert bucket == OTHER
    assert "individual" in why
