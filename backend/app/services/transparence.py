"""Transparence Santé — resolve a target to an RPPS, then fetch their payments.

The register's public Opendatasoft API: free, no key, server-side filtering and
aggregation. See models/transparence.py for why identity is pinned to RPPS
rather than matched on a name.

Everything here is deliberately conservative about identity. The platform's
existing literature lanes already carry a scar from this exact class of bug —
author-name matching that silently returns the WRONG person — and a payment
misattributed in a competitive brief is worse than a missing one, because the
reader has no way to tell it is wrong.
"""
from __future__ import annotations

import json
import unicodedata
import urllib.parse
import urllib.request
from datetime import date

import structlog

logger = structlog.get_logger(__name__)

_BASE = ("https://www.transparence.sante.gouv.fr/api/explore/v2.1"
         "/catalog/datasets/declarations/records")
_TIMEOUT = 45
_UA = "PharmaRadar/1.0 (pharma intelligence; contact via platform administrator)"

# The register's own page cap.
_PAGE = 100

# A target is pinned only when ONE rpps accounts for at least this share of the
# name-matched records that carry an identifier at all.
#
# Set from measurement, not taste: Barlési's dominant RPPS covers 224 of his 225
# identified records (99.6%), and the stragglers are a company-style "FR..." id
# rather than a competing physician. A genuine two-people-same-name case splits
# far below this. The band between "dominant" and "clearly ambiguous" is exactly
# where a wrong answer would be produced confidently, so it resolves to
# `ambiguous` and shows nothing.
_DOMINANCE = 0.85

# Below this many identified records a share is not evidence — 1 of 1 is 100%
# and means nothing.
_MIN_RECORDS = 3

# Identifier types that denote a natural person in the national directory.
# SIREN/FINESS/RNA are companies and institutions; AUTRE is a free-text id a
# declarer invented, which cannot be trusted to identify anyone.
_PERSON_ID_TYPES = ("RPPS/ADELI",)


# Hyphens and apostrophes are PART of French names, not punctuation to strip.
# The register stores them: querying surname PUJOL returns given names
# 'JEAN-FRANCOIS' and 'ANNE-MARIE' verbatim. Folding them to spaces turned
# "Jean-Louis Pujol" into surname "LOUIS PUJOL" / given "JEAN", which matches
# nobody — and would have silently lost the large share of French clinicians
# with a compound given name.
_NAME_PUNCT = "-'"


def fold(value: str) -> str:
    """Uppercase and strip accents — the register's own convention.

    'Barlési' -> 'BARLESI'. Filed names are unaccented uppercase, so a target
    stored as "Fabrice Barlési" would never match on a literal comparison.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    kept = "".join(c if (c.isalnum() or c.isspace() or c in _NAME_PUNCT) else " "
                   for c in stripped)
    return " ".join(kept.upper().split())


def _get(params: dict) -> dict:
    url = f"{_BASE}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


def _quote(value: str) -> str:
    """Escape a value for an ODSQL string literal."""
    return value.replace('"', '\\"')


def name_orderings(full_name: str) -> list[tuple[str, str]]:
    """Both plausible (surname, given) readings of a stored name.

    Targets in this platform are stored surname-first ("CORTOT ALEXIS",
    "MAZIERES JULIEN"), but a name typed by a user naturally reads given-first
    ("Fabrice Barlési"), and both appear in practice. Rather than encode a
    convention that the next person to add a target will not know about, try
    both and let the register decide which one is a real physician — being
    wrong here looks identical to "this KOL has no declarations", which is the
    silent-empty-feature failure mode.

    Compound names are split at the LAST token for surname-first and the FIRST
    token for given-first, so "BENNOUNA LOURIDI JAAFAR" yields both
    ("BENNOUNA LOURIDI", "JAAFAR") and ("LOURIDI JAAFAR", "BENNOUNA").
    """
    parts = [p for p in fold(full_name).split() if p]
    if len(parts) < 2:
        return []
    surname_first = (" ".join(parts[:-1]), parts[-1])   # CORTOT ALEXIS
    given_first = (" ".join(parts[1:]), parts[0])       # Fabrice Barlési
    return [surname_first] if surname_first == given_first else [surname_first, given_first]


def resolve_rpps(full_name: str) -> dict:
    """Find the one RPPS that is this person, or refuse to guess.

    Returns {status, rpps, confidence, candidates, records}. `status` is one of
    the RESOLUTION_STATES; `rpps` is set only when status == 'resolved'.
    """
    orderings = name_orderings(full_name)
    if not orderings:
        return {"status": "not_found", "rpps": None, "confidence": 0.0,
                "candidates": [], "records": 0,
                "note": "need both a given name and a surname to search"}

    attempts = [_resolve_ordering(s, g) for s, g in orderings]
    resolved = [a for a in attempts if a["status"] == "resolved"]

    if len(resolved) == 1:
        return resolved[0]
    if len(resolved) > 1:
        # Both readings found a confident, DIFFERENT person — e.g. a name where
        # each half is a real surname. Refusing is the only safe answer.
        if len({a["rpps"] for a in resolved}) > 1:
            return {"status": "ambiguous", "rpps": None,
                    "confidence": max(a["confidence"] for a in resolved),
                    "candidates": [{"rpps": a["rpps"], "records": a["records"]} for a in resolved],
                    "records": sum(a["records"] for a in resolved),
                    "note": "both name orderings match different people — cannot tell which"}
        return resolved[0]

    # Nothing resolved: report the most informative failure rather than the first.
    ranked = sorted(attempts, key=lambda a: (a["status"] != "ambiguous", -a["records"]))
    return ranked[0]


def _resolve_ordering(surname: str, given: str) -> dict:
    where = (f'identite="{_quote(surname)}" AND prenom="{_quote(given)}"')
    try:
        payload = _get({
            "where": where,
            "group_by": "beneficiaire_identifiant,beneficiaire_type",
            "select": "beneficiaire_identifiant,beneficiaire_type,count(*) as n",
            "order_by": "n DESC",
            "limit": 20,
        })
    except Exception as exc:
        logger.warning("transparence.resolve_failed",
                       surname=surname, given=given, error=str(exc)[:200])
        return {"status": "unresolved", "rpps": None, "confidence": 0.0,
                "candidates": [], "records": 0, "note": f"lookup failed: {str(exc)[:120]}"}

    groups = payload.get("results") or []

    # Only identifiers that denote a natural person can pin an identity. Rows
    # with no id, or a declarer-invented 'AUTRE' id, are counted as coverage
    # context but must never become the pin.
    people = [g for g in groups
              if g.get("beneficiaire_identifiant")
              and g.get("beneficiaire_type") in _PERSON_ID_TYPES]
    identified = sum(int(g.get("n") or 0) for g in people)
    total = sum(int(g.get("n") or 0) for g in groups)

    if not groups:
        return {"status": "not_found", "rpps": None, "confidence": 0.0,
                "candidates": [], "records": 0,
                "note": "no declarations filed under this name"}

    if not people:
        # The name exists in the register but nothing carries a national id, so
        # there is no way to know it is one person rather than several.
        return {"status": "ambiguous", "rpps": None, "confidence": 0.0,
                "candidates": [], "records": total,
                "note": "declarations exist but none carry an RPPS to pin them to"}

    people.sort(key=lambda g: int(g.get("n") or 0), reverse=True)
    top = people[0]
    share = int(top.get("n") or 0) / identified if identified else 0.0
    candidates = [{"rpps": g["beneficiaire_identifiant"], "records": int(g.get("n") or 0)}
                  for g in people[:5]]

    if identified < _MIN_RECORDS:
        return {"status": "ambiguous", "rpps": None, "confidence": round(share, 3),
                "candidates": candidates, "records": total,
                "note": f"only {identified} identified declaration(s) — too few to pin safely"}

    if share < _DOMINANCE:
        return {"status": "ambiguous", "rpps": None, "confidence": round(share, 3),
                "candidates": candidates, "records": total,
                "note": (f"{len(people)} RPPS share this name; top one holds only "
                         f"{share:.0%} — likely more than one person")}

    return {"status": "resolved", "rpps": top["beneficiaire_identifiant"],
            "confidence": round(share, 3), "candidates": candidates, "records": total,
            "note": (f"{share:.0%} of {identified} identified declarations "
                     f"share this RPPS")}


def fetch_payments(rpps: str, since: date | None = None, cap: int = 2000) -> list[dict]:
    """All declarations for one RPPS, optionally only those published since a date.

    `since` filters on the register's publication date, not the payment date: a
    payment made in 2017 can be published in 2026, so an incremental sync keyed
    on the payment date would miss late filings entirely.
    """
    where = f'beneficiaire_identifiant="{_quote(rpps)}"'
    if since:
        where += f' AND date_publication > "{since.isoformat()}"'

    out: list[dict] = []
    offset = 0
    while offset < cap:
        try:
            payload = _get({
                "where": where,
                "select": ("id,montant,date,date_publication,raison_sociale,numero_siren,"
                           "motif_lien_interet,lien_interet,ville,beneficiaire_identifiant"),
                "order_by": "date_publication DESC",
                "limit": _PAGE,
                "offset": offset,
            })
        except Exception as exc:
            logger.warning("transparence.fetch_failed", rpps=rpps,
                           offset=offset, error=str(exc)[:200])
            break

        rows = payload.get("results") or []
        out.extend(rows)
        if len(rows) < _PAGE:
            break
        offset += _PAGE

    logger.info("transparence.fetched", rpps=rpps, rows=len(out),
                since=since.isoformat() if since else None)
    return out


def _as_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def normalise_payment(row: dict) -> dict | None:
    """Register row -> TransparencePayment kwargs. None if unusable.

    A row without an id cannot be deduplicated, and one without an amount is not
    a payment — both are dropped rather than stored as zero, which would
    understate a company's real spend while looking like a measurement.
    """
    declaration_id = row.get("id")
    amount = row.get("montant")
    if not declaration_id or amount is None:
        return None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None

    return {
        "declaration_id": str(declaration_id),
        "rpps": str(row.get("beneficiaire_identifiant") or ""),
        "company": (row.get("raison_sociale") or "Unknown")[:255],
        "company_siren": (str(row["numero_siren"])[:32] if row.get("numero_siren") else None),
        "amount_eur": amount,
        "paid_on": _as_date(row.get("date")),
        "kind": (str(row["lien_interet"])[:32] if row.get("lien_interet") else None),
        "reason": (str(row["motif_lien_interet"])[:255] if row.get("motif_lien_interet") else None),
        "published_on": _as_date(row.get("date_publication")),
        "city": (str(row["ville"])[:128] if row.get("ville") else None),
    }


# ── Aggregation for the UI ────────────────────────────────
#
# Companies are grouped by SIREN, never by trade name. The register contains
# both "ROCHE SAS" and "ROCHE" as separate strings for the same legal entity
# (SIREN 552012031, 462 + 251 payments across our tracked KOLs). Grouped by
# name, Roche's total reads €569,841 and lands BELOW AstraZeneca's €788,814;
# grouped by SIREN it is €803,701 and lands above. A client drawing a
# share-of-investment conclusion from the first version would be drawing the
# wrong one — from data that is individually accurate and collectively false.
#
# Foreign affiliates (ROCHE SUISSE, ROCHE MAROC…) carry no SIREN because they
# are not French entities. They keep their own name as the grouping key rather
# than being folded into the French company, which is the honest reading: they
# are different legal entities, and the client's remit is France.

def company_key_sql() -> str:
    """SQL expression that groups payments by legal entity, not trade name."""
    return "COALESCE(NULLIF(company_siren, ''), UPPER(company))"
