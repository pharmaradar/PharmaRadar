"""FR/EN language detection for scraped social posts and web results.

Replaces the ">= 4 French function words in the first 1000 chars" heuristic that
was duplicated in ``tasks/social.py`` and ``routers/discovery.py``. That test was
*absolute*, not comparative, so anything shorter than a couple of paragraphs —
i.e. almost every social post — scored under the threshold and was labelled
``en``. With the French display filter on, those posts disappeared. That single
bug produced both halves of the client complaint: "too few posts" and "posts are
not in French".

This detector scores French *against* English on normalised text, so a two-line
French tweet is judged on the same evidence a long article is. It returns
``unknown`` rather than silently defaulting to English when there is no signal —
a strict French filter must hide what it cannot verify, not mislabel it.

Pure stdlib on purpose: the Celery workers run under a hard memory ceiling (see
the OOM history in DEPLOY.md), so this stays a few KB of frozensets rather than
a model download.
"""
from __future__ import annotations

import re

FR = "fr"
EN = "en"
UNKNOWN = "unknown"

# Words below are meant to be *exclusive* to one language as used in pharma and
# social copy. Anything ambiguous across FR/EN ("on", "a", "or", "car", "son",
# "patients", "cancer", "plus", "no") carries no evidence, so the two lists are
# intersected below and shared tokens are dropped from both automatically.
_FR_RAW = frozenset("""
le la les un une des du de et est sont ont ete été etre être avec dans pour par
sur mais ou où qui que quoi ce cet cette ces sa ses leur leurs nous
vous ils elles elle je au aux se ne pas tres très aussi comme tout tous toute
toutes bien encore chez entre sans sous depuis apres après avant contre vers
alors donc ainsi cela ça faire fait peut peuvent doit doivent avons avez
etait était etaient étaient notre votre quel quelle quels quelles lors selon
afin cependant toutefois grâce annee année annees années jour jours
sante santé traitement traitements essai essais etude étude etudes études
resultats résultats medecin médecin medecins médecins malade maladie maladies
soins soignants poumon poumons sein foie sang chercheur chercheurs
equipe équipe nouvelle nouveau nouveaux nouvelles meilleur meilleure plusieurs
certains autres beaucoup moins deja déjà toujours jamais surtout enfin
prise patiente patientes depistage dépistage survie guerison guérison
essaiclinique cancerologie cancérologie pulmonaire bronchique immunotherapie
immunothérapie chimiotherapie chimiothérapie radiotherapie radiothérapie
""".split())

_EN_RAW = frozenset("""
the and is are was were be been being of to in with that this these those it
its we you they he she from at by as an have has had will would should could
can may might our your their there here what when where which who why how
about after before between during into through over under again more most
other some such only own same than too very just now also new first last year
years study studies results result treatment trials trial showed show shows
said says according following including data findings among however therefore
further both each many much because while within without across against
toward towards early late high low significant survival lung breast screening
""".split())

# A token in both lists is evidence of nothing. Drop it from both rather than
# asserting — a bad word list must never take the workers down at import time.
_SHARED = _FR_RAW & _EN_RAW
FR_WORDS = _FR_RAW - _SHARED
EN_WORDS = _EN_RAW - _SHARED

# Elisions have no English equivalent and survive normalisation, so each is
# worth more than one stopword hit.
_FR_ELISIONS = ("l'", "d'", "qu'", "n'", "c'", "j'", "s'", "m'", "t'")
_FR_ACCENTS = frozenset("àâäçéèêëîïôöûùüÿœæ")

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
# Hashtags and @mentions are overwhelmingly English even on French posts
# (#lungcancer, #ASCO26) and used to drag French text to an English verdict.
_HANDLE_RE = re.compile(r"[@#]\w+")
# No apostrophe joining: "l'étude" must split to ["l", "étude"] so the French
# word list still matches the elided noun. Elisions are counted separately.
_TOKEN_RE = re.compile(r"[a-zà-öø-ÿ]+")

# Weights. Elisions and accents are French-only markers and carry more evidence
# per occurrence than a stopword. Both are capped so one accent-heavy word — or
# a quoted French title inside an English article — cannot swing the verdict.
_W_ELISION = 1.5
_W_ACCENT = 0.75
_MAX_ELISION_SCORE = 6.0
_MAX_ACCENT_SCORE = 4.5

_MIN_TEXT_LEN = 12   # below this there is nothing to judge
_MIN_TOKENS = 3
_MIN_SIGNAL = 1.0    # no marker of either language at all -> unknown
_MIN_MARGIN = 0.5    # two near-tied scores mean mixed copy, not a coin flip


def normalize(text: str) -> str:
    """Lowercase and drop the parts of social copy that carry no language signal."""
    t = (text or "").lower()
    t = _URL_RE.sub(" ", t)
    t = _HANDLE_RE.sub(" ", t)
    # Normalise the typographic apostrophe so "l’étude" and "l'étude" score alike.
    return t.replace("’", "'")


def score(text: str) -> tuple[float, float]:
    """Return ``(french_score, english_score)``. Exposed for tests and tuning."""
    t = normalize(text)
    if len(t.strip()) < _MIN_TEXT_LEN:
        return 0.0, 0.0

    tokens = _TOKEN_RE.findall(t)
    if len(tokens) < _MIN_TOKENS:
        return 0.0, 0.0

    fr = float(sum(1 for tok in tokens if tok in FR_WORDS))
    en = float(sum(1 for tok in tokens if tok in EN_WORDS))

    fr += min(sum(t.count(e) for e in _FR_ELISIONS) * _W_ELISION, _MAX_ELISION_SCORE)
    fr += min(sum(1 for c in t if c in _FR_ACCENTS) * _W_ACCENT, _MAX_ACCENT_SCORE)
    return fr, en


def detect_lang(text: str) -> str:
    """Classify text as ``fr``, ``en``, or ``unknown``.

    ``unknown`` covers empty, very short, and genuinely ambiguous text. Callers
    filtering for French must treat ``unknown`` as *not* French.
    """
    fr, en = score(text)
    if fr < _MIN_SIGNAL and en < _MIN_SIGNAL:
        return UNKNOWN
    if abs(fr - en) < _MIN_MARGIN:
        return UNKNOWN
    return FR if fr > en else EN


def is_french(text: str) -> bool:
    """Strict French test — ``unknown`` counts as not French."""
    return detect_lang(text) == FR
