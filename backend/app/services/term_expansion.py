"""Bilingual term expansion for topic and congress matching.

The corpus is deliberately French. Topic names are written in English — the
client types "subcutaneous administration", and the matcher looks for that
literal string in posts that say "administration sous-cutanée". Measured on the
live database: the topic "subcutaneous administration" matched **0** rows, while
the same concept in French was present in the corpus.

That is the whole reason burning-topic reports read thin. The report generator
is fine; it was being handed 8 documents instead of the ones that exist.

Two things happen here:

1. **Concept expansion** — a term is widened to its counterpart in the other
   language plus the abbreviations French oncology actually uses (CBNPC for
   NSCLC, "voie SC" for subcutaneous). Curated rather than machine-translated:
   a wrong synonym silently pollutes every report built on it, so the list only
   contains pairs verified against this domain.

2. **Accent folding** — Postgres `LIKE` is accent-sensitive, so `%sous-cutane%`
   does not match "sous-cutané". Every variant is emitted in both accented and
   folded form, which is cheaper and more predictable than requiring the
   `unaccent` extension on the managed database.

Expansion only ever ADDS terms. A topic keeps matching everything it matched
before, so this cannot narrow an existing report.
"""
from __future__ import annotations

import unicodedata

# Concept groups. Every member of a group expands to all the others, so the
# direction the user typed does not matter.
#
# Scope is lung cancer + the pharma vocabulary around it, matching the client's
# market. Adding a group is safe; adding a WRONG pair is not, because it pulls
# unrelated posts into a report that claims to be about the topic.
_CONCEPT_GROUPS: tuple[tuple[str, ...], ...] = (
    # Administration routes — the case that exposed the bug.
    ("subcutaneous", "sous-cutané", "sous cutané", "voie sous-cutanée", "voie sc"),
    ("intravenous", "intraveineux", "intraveineuse", "voie iv", "perfusion"),
    ("oral administration", "voie orale", "par voie orale", "comprimé"),
    # The disease itself.
    ("lung cancer", "cancer du poumon", "cancer bronchique", "cancer pulmonaire"),
    ("nsclc", "cbnpc", "cancer bronchique non à petites cellules",
     "non-small cell lung cancer"),
    ("sclc", "cbpc", "cancer bronchique à petites cellules",
     "small cell lung cancer"),
    ("mesothelioma", "mésothéliome"),
    ("metastasis", "métastase", "métastatique", "metastatic"),
    # Care pathway.
    ("screening", "dépistage"),
    ("diagnosis", "diagnostic"),
    ("treatment", "traitement", "prise en charge"),
    ("clinical trial", "essai clinique", "étude clinique"),
    ("survival", "survie"),
    ("prognosis", "pronostic"),
    ("relapse", "rechute", "récidive"),
    ("remission", "rémission"),
    # Modalities.
    ("immunotherapy", "immunothérapie"),
    ("chemotherapy", "chimiothérapie"),
    ("radiotherapy", "radiothérapie"),
    ("targeted therapy", "thérapie ciblée", "traitement ciblé"),
    ("biomarker", "biomarqueur"),
    ("mutation", "mutation"),
    # Safety and experience. NOT an AE filter — that stays in ae_filter.py; this
    # only helps a topic ABOUT tolerability find the French wording.
    ("side effects", "effets secondaires", "effets indésirables"),
    ("tolerability", "tolérance"),
    ("quality of life", "qualité de vie"),
    ("patient reported", "ressenti patient", "témoignage patient"),
    ("caregiver", "aidant"),
    # Market / access.
    ("reimbursement", "remboursement"),
    ("market access", "accès au marché"),
    ("guidelines", "recommandations", "référentiel"),
    ("approval", "autorisation", "amm"),
    ("real world evidence", "données de vie réelle", "vie réelle"),
)


def fold_accents(value: str) -> str:
    """Strip diacritics so 'sous-cutané' and 'sous-cutane' compare equal."""
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _build_index() -> dict[str, tuple[str, ...]]:
    index: dict[str, tuple[str, ...]] = {}
    for group in _CONCEPT_GROUPS:
        for member in group:
            index[fold_accents(member).lower()] = group
    return index


_INDEX = _build_index()


def expand_term(term: str) -> list[str]:
    """One term to every spelling worth searching for, original first.

    Matches the whole term first, then falls back to concepts appearing INSIDE
    it — so "subcutaneous administration in NSCLC" still picks up both
    "sous-cutane" and "cbnpc" even though the full phrase is in no group.
    """
    cleaned = (term or "").strip()
    if not cleaned:
        return []

    variants: list[str] = [cleaned]
    key = fold_accents(cleaned).lower()

    group = _INDEX.get(key)
    if group:
        variants.extend(group)
    else:
        # Substring pass. Longest concepts first so "lung cancer" wins over a
        # bare "cancer" that would drag in every oncology post ever collected.
        for concept_key in sorted(_INDEX, key=len, reverse=True):
            if len(concept_key) >= 4 and concept_key in key:
                variants.extend(_INDEX[concept_key])
                break

    # Emit accented AND folded spellings of everything. Postgres LIKE is
    # accent-sensitive in both directions: `%sous-cutane%` misses "sous-cutanée",
    # and `%sous-cutané%` misses a post that dropped the accent. The groups above
    # therefore carry real French accents, and each yields both forms here.
    out: list[str] = []
    for variant in variants:
        for form in (variant, fold_accents(variant)):
            form = form.strip()
            if form and form.lower() not in {o.lower() for o in out}:
                out.append(form)
    return out


def expand_terms(terms: list[str]) -> list[str]:
    """Expand a list, preserving order and dropping duplicates."""
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        for variant in expand_term(term):
            low = variant.lower()
            if low not in seen:
                seen.add(low)
                out.append(variant)
    return out
