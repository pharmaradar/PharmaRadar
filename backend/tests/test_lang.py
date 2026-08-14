"""FR/EN detector tests.

Guards the fix for the bug where French posts were labelled ``en`` and then
hidden by the French display filter — the cause of both "too few posts" and
"posts are not in French" in the client's v1 feedback.

Samples are written to look like what the scrapers actually return: short
tweets, LinkedIn blurbs, congress posts, patient-association copy.
"""
import pytest

from app.services.lang import EN, FR, UNKNOWN, detect_lang, is_french

FRENCH = [
    # Short French posts — the exact shape the old >=4-absolute-hits heuristic
    # always got wrong, because a tweet never reaches four function words.
    "Nouvelle étude sur le cancer du poumon présentée à l'ASCO.",
    "Le dépistage précoce sauve des vies. #cancerdupoumon",
    "Les résultats de l'essai clinique sont très encourageants pour les patients.",
    "L'immunothérapie change la prise en charge du CBNPC métastatique.",
    "Bravo à toute l'équipe pour ce travail sur la survie globale.",
    "Nous publions aujourd'hui nos données sur le traitement du cancer bronchique.",
    "Le Pr Besse a présenté les résultats de l'étude à Paris cette semaine.",
    "Journée mondiale contre le cancer : la Ligue mobilise les soignants en France.",
    "Une avancée majeure dans la thérapie ciblée pour les patientes.",
    "Quels sont les effets secondaires de la chimiothérapie ? Notre médecin répond.",
    # Accents omitted, as people actually type on mobile.
    "Les resultats de l etude sont encourageants pour les patients traites en France.",
]

ENGLISH = [
    "New data on lung cancer screening presented at ASCO this year.",
    "The trial showed a significant improvement in overall survival.",
    "We are excited to share our latest results with the oncology community.",
    "Immunotherapy is changing how we treat metastatic NSCLC in the first line.",
    "According to the study, patients had better outcomes with the new regimen.",
    "Here is what you should know about biomarker testing and targeted therapy.",
]

# No function words in either language — brand names and bare hashtags. The
# detector must say so rather than defaulting to English.
AMBIGUOUS = [
    "Tecentriq",
    "#ASCO26 #lungcancer #NSCLC",
    "Keytruda + Imfinzi",
    "",
    "OK",
    None,
]


@pytest.mark.parametrize("text", FRENCH)
def test_detects_french(text):
    assert detect_lang(text) == FR


@pytest.mark.parametrize("text", ENGLISH)
def test_detects_english(text):
    assert detect_lang(text) == EN


@pytest.mark.parametrize("text", AMBIGUOUS)
def test_no_signal_is_unknown(text):
    assert detect_lang(text) == UNKNOWN


def test_english_hashtags_do_not_flip_a_french_post():
    """Hashtags are English even on French posts, so they are stripped before
    scoring. Otherwise every French congress post reads as English."""
    assert detect_lang(
        "Très belle présentation de l'équipe sur la survie globale. "
        "#lungcancer #ASCO26 #NSCLC"
    ) == FR


def test_french_institution_names_do_not_flip_an_english_post():
    assert detect_lang(
        "Great to see Institut Curie and Gustave Roussy present new data at the meeting."
    ) == EN


def test_quoted_french_inside_english_stays_english():
    assert detect_lang(
        "Our paper 'Traitement du cancer' was published; the study showed "
        "better outcomes for these patients."
    ) == EN


def test_is_french_treats_unknown_as_not_french():
    """A strict French filter must hide what it cannot verify."""
    assert is_french("Les résultats de l'étude sont encourageants.") is True
    assert is_french("Tecentriq") is False
    assert is_french("The trial showed better survival.") is False


def test_word_lists_share_no_tokens():
    """A token in both lists is evidence of nothing and would skew every
    verdict; the module intersects them out at import."""
    from app.services.lang import EN_WORDS, FR_WORDS

    assert not (FR_WORDS & EN_WORDS)
