"""Turning a typed question into things you can actually search for.

The client types a sentence — *"What do doctors think about subcutaneous
therapies in lung cancer?"* — and expects a report. Matching that sentence
against stored text with a LIKE finds nothing, because no row contains the
question; and searching the web for the whole sentence is barely better.

So a question is reduced to its content terms first. Two layers, cheapest first:

1. A stopword strip that always works, costs nothing, and cannot fail.
2. An LLM expansion that adds French clinical vocabulary and synonyms, because
   a French oncologist writes *CBNPC* where the question said *lung cancer*.

Layer 2 is best-effort: if the model is slow, truncated or unavailable, layer 1
still produced usable terms. A question must never fail to produce a report just
because an enrichment call did.
"""
from __future__ import annotations

import json
import re

import structlog

logger = structlog.get_logger(__name__)

# Question scaffolding and generic filler. French and English, because the box
# accepts both. Kept to words that carry no topical meaning — anything clinical
# stays, even if common ("patient", "traitement").
_STOPWORDS = frozenset("""
what which who whom whose when where why how does do did is are was were be been
being am can could shall should will would may might must the a an of to in on at
for with about from by as into over under between during before after above below
and or but if then than that this these those there here it its their his her our
your my me you they them we us i he she
about tell show give find list explain describe compare think thinks thinking say
says said talk talks talking discuss discussed discussing mention mentioned any
some more most much many any anything something
que quel quelle quels quelles qui quoi dont ou où quand comment pourquoi est sont
ete été etre être avec dans pour par sur mais le la les un une des du de au aux
ce cet cette ces son sa ses leur leurs nous vous ils elles je on ne pas plus
dit dire disent parle parlent parlant pense pensent penser
""".split())

# Keep short tokens that are real clinical shorthand rather than noise.
_KEEP_SHORT = frozenset({"cbnpc", "cpc", "alk", "egfr", "kras", "pdl1", "pd1",
                         "her2", "brca", "ros1", "sg", "ssp", "os", "pfs", "asco",
                         "esmo", "aacr", "wclc", "inca", "has", "ifct", "splf"})

_TOKEN_RE = re.compile(r"[\w'’-]+", re.UNICODE)


def content_terms(question: str, limit: int = 8) -> list[str]:
    """Content words from a question, longest first, duplicates removed.

    Deterministic and dependency-free — this is the floor that guarantees a
    question always yields something to search.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(question or ""):
        token = raw.strip("'’-")
        low = token.lower()
        if not low or low in _STOPWORDS or low in seen:
            continue
        if len(low) < 3 and low not in _KEEP_SHORT:
            continue
        if low.isdigit() and len(low) != 4:      # keep years, drop stray numbers
            continue
        seen.add(low)
        terms.append(token)
    # Longer tokens are more discriminating; a LIKE on "the" helps nobody.
    terms.sort(key=len, reverse=True)
    return terms[:limit]


def phrase_candidates(question: str) -> list[str]:
    """Multi-word phrases worth matching as a unit ("cancer du poumon").

    Adjacent content words often mean more together than apart, and a phrase
    match is far more precise than either word alone.
    """
    words = [w for w in _TOKEN_RE.findall(question or "")
             if w.lower() not in _STOPWORDS and len(w) > 2]
    return [f"{a} {b}" for a, b in zip(words, words[1:])][:4]


def expand_for_search(question: str, *, language: str | None = "fr",
                     ttl_secs: int = 86_400) -> list[str]:
    """Search terms for a typed question, cached so repeat searches are free.

    The social search bar previously matched the WHOLE typed string with a
    single LIKE, so "does KOL think subcutaneous is better than IV" looked for
    that exact sentence inside post text and found nothing — measured 0 rows
    against a corpus that does contain the subject.

    Enrichment costs an LLM call, which is fine once per question and wasteful
    on every keystroke-submit, so the result is cached in Redis under the
    normalised question. Redis being down only makes this slower, never wrong.
    """
    from app.services.term_expansion import expand_terms

    key = f"question:terms:{(language or 'fr')}:{(question or '').strip().lower()}"
    client = None
    try:
        import redis as _redis

        from app.config import get_settings
        client = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        cached = client.get(key)
        if cached:
            return json.loads(cached)
    except Exception:                            # noqa: BLE001 - cache is optional
        client = None

    terms = expand(question, language=language)["terms"]
    # Bilingual/accent variants on top: the corpus is French, questions are not.
    terms = expand_terms(terms)

    if client is not None:
        try:
            client.setex(key, ttl_secs, json.dumps(terms))
        except Exception:                        # noqa: BLE001
            pass
    return terms


def expand(question: str, *, language: str | None = "fr") -> dict:
    """Search terms for a question: deterministic terms plus LLM enrichment.

    Returns ``{"terms": [...], "queries": [...]}`` — `terms` match stored rows,
    `queries` are issued to web search. Never raises.
    """
    base = content_terms(question)
    phrases = phrase_candidates(question)
    terms = list(dict.fromkeys(phrases + base))

    enriched: list[str] = []
    try:
        enriched = _llm_terms(question, language)
    except Exception as exc:                     # noqa: BLE001 - enrichment is optional
        logger.warning("question.expand_failed", error=str(exc)[:160])

    terms = list(dict.fromkeys(terms + enriched))[:14]

    # Web queries: the question itself is a fine query for a search engine (it
    # handles natural language), plus the sharper term combinations.
    queries = [question.strip()]
    if base:
        queries.append(" ".join(base[:4]))
    queries.extend(enriched[:4])
    return {"terms": terms, "queries": [q for q in dict.fromkeys(queries) if q]}


def _llm_terms(question: str, language: str | None) -> list[str]:
    """Ask the model for the terms a French clinician would actually use."""
    from app.services.llm_router import call_llm

    french = (language or "fr") == "fr"
    prompt = (
        "You turn a user's question into search terms for a pharma intelligence "
        "database covering the FRENCH market.\n\n"
        f'QUESTION: "{question}"\n\n'
        "Return ONLY a JSON array of 4-8 short search terms — no prose, no keys.\n"
        + ("At least half must be French, written the way a French oncologist "
           "types them: CBNPC not NSCLC, 'cancer du poumon', 'immunothérapie', "
           "'thérapie ciblée', 'essai clinique'. Include drug brand and INN names "
           "unchanged (they are the same in every language).\n" if french else
           "Use the clinical vocabulary a specialist would type.\n")
        + 'Example: ["cancer du poumon", "CBNPC", "voie sous-cutanée", '
          '"immunothérapie", "subcutaneous", "Tecentriq"]'
    )
    # Generous cap: gemini-2.5-flash spends this budget on reasoning too, and a
    # truncated reply parses to nothing.
    raw = call_llm([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=2048)
    text = (raw or "").strip()
    if "```" in text:
        text = text.split("```")[1].lstrip("json").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    parsed = json.loads(text[start:end + 1])
    return [t.strip() for t in parsed
            if isinstance(t, str) and 2 < len(t.strip()) < 60][:8]

# Words that are everywhere in a pharma corpus, so matching on them alone
# retrieves the whole database rather than the question's subject. Kept as
# *fallback* terms: useful when a specific search found almost nothing, harmful
# when it found plenty.
_LOW_SPECIFICITY = frozenset({
    "study", "studies", "etude", "étude", "etudes", "études",
    "trial", "trials", "essai", "essais",
    "cancer", "cancers", "tumour", "tumor", "oncology", "oncologie",
    "patient", "patients", "patiente", "patientes",
    "doctor", "doctors", "medecin", "médecin", "medecins", "médecins",
    "therapy", "therapies", "therapie", "thérapie", "therapies", "thérapies",
    "treatment", "treatments", "traitement", "traitements",
    "data", "donnees", "données", "result", "results", "resultat", "résultats",
    "drug", "drugs", "medicament", "médicament", "research", "recherche",
    "health", "sante", "santé", "care", "soin", "soins", "news", "article",
})


def is_specific(term: str) -> bool:
    """True when a term is discriminating enough to search on its own.

    A phrase always is — two words together are far rarer than either alone.
    A single word is only if it is not one of the corpus-wide staples.
    """
    cleaned = (term or "").strip().lower()
    if not cleaned:
        return False
    if " " in cleaned:
        return True
    return cleaned not in _LOW_SPECIFICITY


def split_by_specificity(terms: list[str]) -> tuple[list[str], list[str]]:
    """``(specific, fallback)`` — search the first, widen to the second only if needed."""
    specific = [t for t in terms if is_specific(t)]
    fallback = [t for t in terms if not is_specific(t)]
    return specific, fallback
