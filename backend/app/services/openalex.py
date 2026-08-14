"""KOL discovery — who actually leads a topic in France, ranked by output.

The spec asks the platform to identify "the main speaker for topic X or Y that
could be outside our current audience". Every other lane can only describe the
people already on the target list; this one finds the list itself.

Measured 2026-08-14 on lung cancer, French institutions, 2025+: 2,070 works, and
four of the ten most prolific authors — Debieuvre, Barlési, Greillier, Remón —
were NOT tracked. Those are exactly the voices the client asked to surface.

OpenAlex is free, keyless and ranks by real publication volume rather than by
who happens to post on social media. Filtering is by
`institutions.country_code:fr`, so France is applied at acquisition, not by
discarding afterwards.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

import structlog

logger = structlog.get_logger(__name__)

_API = "https://api.openalex.org"
_TIMEOUT = 30
# OpenAlex asks for a contact address rather than a key, and rewards it with the
# faster pool.
_HEADERS = {"User-Agent": "PharmaRadar/1.0 (mailto:admin@pharmaradar.com)"}


def _get(path: str, params: dict) -> dict:
    url = f"{_API}/{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


def _fold(value: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", value or "")
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.lower().replace("-", " ").replace(".", " ")


def _name_key(value: str) -> frozenset:
    """Order-independent name key.

    Targets are stored surname-first ("BESSE BENJAMIN"); OpenAlex returns
    "Benjamin Besse". Comparing strings would call the same person two people,
    so both collapse to a set of tokens. Initials ("M. Pérol") are dropped
    because a single letter matches far too much.
    """
    return frozenset(t for t in _fold(value).split() if len(t) > 1)


def _is_same_person(candidate: str, known: str) -> bool:
    a, b = _name_key(candidate), _name_key(known)
    if not a or not b:
        return False
    # One may carry a middle name the other omits, so containment either way
    # counts — but only when the shorter side has a real surname in it.
    return a.issubset(b) or b.issubset(a)


def discover_authors(topic: str = "lung cancer", *, country: str = "fr",
                     since: str = "2025-01-01", limit: int = 25) -> list[dict]:
    """Most prolific authors on a topic, restricted to one country's institutions.

    Returns ranked candidates; the caller decides which are already tracked.
    """
    try:
        grouped = _get("works", {
            "filter": (f"title_and_abstract.search:{topic},"
                       f"institutions.country_code:{country},"
                       f"from_publication_date:{since}"),
            "group_by": "authorships.author.id",
            "per-page": max(1, min(limit, 50)),
        })
    except Exception as exc:                        # noqa: BLE001 - discovery is optional
        logger.warning("openalex.discover_failed", topic=topic, error=str(exc)[:160])
        return []

    out: list[dict] = []
    for group in (grouped.get("group_by") or [])[:limit]:
        author_id = (group.get("key") or "").rstrip("/").split("/")[-1]
        name = group.get("key_display_name") or ""
        if not author_id or not name:
            continue
        out.append({
            "openalex_id": author_id,
            "name": name,
            "papers_on_topic": group.get("count") or 0,
        })
    logger.info("openalex.discovered", topic=topic, country=country, authors=len(out))
    return out


def enrich_author(author_id: str) -> dict:
    """Institution, lifetime output and research topics for one author.

    Kept separate from discovery because it costs one request per author: the
    caller enriches only the candidates it intends to show.
    """
    try:
        data = _get(f"authors/{author_id}", {})
    except Exception as exc:                        # noqa: BLE001
        logger.warning("openalex.enrich_failed", author=author_id, error=str(exc)[:140])
        return {}

    institutions = data.get("last_known_institutions") or []
    first = institutions[0] if institutions else {}
    country = (first.get("country_code") or "").lower()
    return {
        "institution": first.get("display_name"),
        # The country filter matches works with ANY French institution, so a
        # foreign co-author of French research appears in the ranking. That is
        # legitimate — they shape the French conversation — but the client is
        # buying French coverage, so where they are based must be visible
        # rather than implied.
        "institution_country": country or None,
        "france_based": country == "fr",
        "works_count": data.get("works_count") or 0,
        "cited_by_count": data.get("cited_by_count") or 0,
        "topics": [t.get("display_name") for t in (data.get("topics") or [])[:3]],
        "orcid": data.get("orcid"),
    }


def rank_candidates(topic: str, tracked_names: list[str], *, country: str = "fr",
                    since: str = "2025-01-01", limit: int = 25,
                    enrich_top: int = 12) -> list[dict]:
    """Ranked authors on a topic, each flagged as tracked or not.

    Only the top `enrich_top` untracked candidates are enriched — the profile
    lookup is one request each, and the client acts on the head of the list.
    """
    authors = discover_authors(topic, country=country, since=since, limit=limit)
    enriched = 0
    for author in authors:
        author["tracked"] = any(_is_same_person(author["name"], known)
                                for known in tracked_names)
        if not author["tracked"] and enriched < enrich_top:
            author.update(enrich_author(author["openalex_id"]))
            enriched += 1
    return authors
