"""Publications and trials — the sources a pharma KOL actually speaks through.

Measured 2026-08-14, before this existed: the entire web/KOL corpus was **27
documents**, while six of the client's own tracked KOLs had **461 papers** since
2024 in Europe PMC, and the four tracked pharma companies had **450 lung-cancer
trials** within 500km of Paris on ClinicalTrials.gov. The platform was mining the
noisiest source available and ignoring the one where oncology opinion is actually
published.

Both APIs are free, keyless, official, and carry no scraping-ToS risk — which is
also what the client's "legal and scalable" sourcing constraint asks for.

What this is NOT: a replacement for social listening. A paper tells you what a
KOL concluded; a post tells you what they are saying this week. The two answer
different questions, so this adds a lane rather than swapping one out.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import structlog

logger = structlog.get_logger(__name__)

_EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_CTGOV = "https://clinicaltrials.gov/api/v2/studies"
_TIMEOUT = 30

# Both services ask for a contactable agent rather than an API key.
_UA = "PharmaRadar/1.0 (pharma intelligence; contact via platform administrator)"


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


def _fold(value: str) -> str:
    """Lowercase, strip accents and hyphens — for comparing author names."""
    import unicodedata

    decomposed = unicodedata.normalize("NFD", value or "")
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.lower().replace("-", " ").replace(".", " ")


def _author_candidates(name: str) -> list[tuple[str, str]]:
    """Plausible `(surname, initial)` readings of a stored target name.

    Targets are stored surname-first ("GIRARD NICOLAS"), but French surnames are
    often compound ("MORO SIBILOT DENIS"), and there is no way to tell from the
    string alone where the surname ends. Guessing wrong is not a near miss — it
    silently returns a DIFFERENT researcher: `AUTH:"Moro S"` returns 31 papers by
    Russano/Sturlese et al., while `AUTH:"Moro-Sibilot D"` returns the right 255.

    So every reading is offered, most specific first, and the caller verifies
    against the returned author list rather than trusting the guess.
    """
    parts = [p for p in (name or "").replace(",", " ").split() if p]
    if not parts:
        return []
    if len(parts) == 1:
        return [(parts[0].capitalize(), "")]

    *surname_parts, last = parts
    candidates: list[tuple[str, str]] = []
    if len(surname_parts) > 1:
        # Compound surname, last token is the first name.
        compound = "-".join(p.capitalize() for p in surname_parts)
        candidates.append((compound, last[0].upper()))
        candidates.append((" ".join(p.capitalize() for p in surname_parts), last[0].upper()))
    # Simple reading: first token is the surname, second is the first name.
    candidates.append((parts[0].capitalize(), parts[1][0].upper()))
    if len(parts) > 2:
        # Surname + middle name(s) + first name — "BENNOUNA LOURIDI JAAFAR" is
        # published as "Bennouna J", which none of the readings above produce.
        candidates.append((parts[0].capitalize(), last[0].upper()))
    seen, unique = set(), []
    for surname, initial in candidates:
        key = (surname.lower(), initial)
        if key not in seen:
            seen.add(key)
            unique.append((surname, initial))
    return unique


# Resolved author forms, so the verification round-trip happens once per name.
_AUTHOR_CACHE: dict[str, str | None] = {}


def resolve_author(name: str, disease_terms: str) -> str | None:
    """The Europe PMC author clause that demonstrably matches this person.

    Verification is the point: a candidate is accepted only when the papers it
    returns actually list that surname among their authors. Hit count alone
    proves nothing — a wrong surname returns plenty of hits for someone else.
    """
    if name in _AUTHOR_CACHE:
        return _AUTHOR_CACHE[name]

    resolved = None
    for surname, initial in _author_candidates(name):
        clause = f'AUTH:"{surname} {initial}"'.strip() if initial else f'AUTH:"{surname}"'
        url = (f"{_EPMC}?query={urllib.parse.quote(clause + f' AND ({disease_terms})')}"
               f"&format=json&pageSize=3&resultType=lite")
        try:
            payload = _get_json(url)
        except Exception as exc:                    # noqa: BLE001
            logger.warning("literature.resolve_failed", author=name, error=str(exc)[:120])
            continue

        results = (payload.get("resultList") or {}).get("result") or []
        if not results:
            continue
        # Verify the INITIAL too, not just the surname.
        #
        # Checking the surname alone catches "wrong surname entirely" — the
        # Moro/Russano case this function was written for — but not the failure
        # that is far more common with French surnames: the right surname and
        # the wrong person. Asking for AUTH:"Bennouna J" and getting back a page
        # of papers by Bennouna L passed verification, because "bennouna" is
        # present in every one of them, and those papers were then filed under
        # the wrong KOL.
        #
        # Author strings are formatted "Bennouna J, Girard N.", so requiring the
        # folded "<surname> <initial>" to appear as a substring pins the pair
        # without needing to parse the list. It still matches "Bennouna JA" and
        # "Bennouna Jaafar", which are the same person written differently.
        wanted = f"{_fold(surname)} {initial.lower()}".strip() if initial else _fold(surname)
        if any(wanted in _fold(r.get("authorString") or "") for r in results):
            resolved = clause
            break
        logger.debug("literature.candidate_rejected", author=name, clause=clause,
                     wanted=wanted)

    if resolved is None:
        logger.info("literature.author_unresolved", author=name)
    _AUTHOR_CACHE[name] = resolved
    return resolved


def _journal_of(item: dict) -> str:
    """Journal title, preferring the short form a reader recognises."""
    journal = ((item.get("journalInfo") or {}).get("journal") or {})
    return (journal.get("medlineAbbreviation")
            or journal.get("title")
            or item.get("journalTitle") or "").strip()


def search_publications(author_name: str, *, since_days: int = 365,
                        disease_terms: str = "lung OR NSCLC OR thoracic OR poumon",
                        limit: int = 25) -> list[dict]:
    """Recent papers by one author, scoped to the disease area.

    The disease scope matters: a prolific oncologist also publishes outside
    thoracic oncology, and pulling everything would bury the signal the client
    cares about in unrelated work.
    """
    author = resolve_author(author_name, disease_terms)
    if not author:
        return []

    start = (date.today() - timedelta(days=max(1, since_days))).isoformat()
    query = (f'{author} AND ({disease_terms}) '
             f'AND FIRST_PDATE:[{start} TO {date.today().isoformat()}]')
    url = (f"{_EPMC}?query={urllib.parse.quote(query)}&format=json"
           f"&pageSize={max(1, min(limit, 100))}&resultType=core")

    try:
        payload = _get_json(url)
    except Exception as exc:                        # noqa: BLE001 - never fail a run
        logger.warning("literature.epmc_failed", author=author_name, error=str(exc)[:160])
        return []

    out: list[dict] = []
    for item in (payload.get("resultList") or {}).get("result") or []:
        # Abstract first: it carries the actual finding. Title alone is too thin
        # for the extractor to draw an insight from.
        abstract = (item.get("abstractText") or "").strip()
        title = (item.get("title") or "").strip()
        if not title:
            continue
        doi = item.get("doi")
        pmid = item.get("pmid") or item.get("id")
        url_out = (f"https://doi.org/{doi}" if doi else
                   f"https://europepmc.org/article/{item.get('source', 'MED')}/{pmid}")
        out.append({
            "title": title,
            "text": abstract or title,
            "url": url_out,
            # `journalTitle` is empty in the core response; the real title sits
            # under journalInfo.journal. Falling back to "Europe PMC" made every
            # paper look like it came from the same publication.
            "source_name": (_journal_of(item) or "Europe PMC"),
            "published_date": (item.get("firstPublicationDate")
                               or item.get("pubYear") or ""),
            "authors": item.get("authorString") or "",
            "cited_by": item.get("citedByCount") or 0,
            "is_open_access": (item.get("isOpenAccess") == "Y"),
            "kind": "publication",
        })
    logger.info("literature.epmc", author=author_name, results=len(out))
    return out


def search_trials(sponsor: str, *, condition: str = "lung cancer",
                  around_paris_km: int = 500, limit: int = 25) -> list[dict]:
    """Trials by one sponsor in the disease area, near the client's market.

    The geo filter is the France constraint applied at acquisition: a sponsor's
    global trial list is mostly irrelevant to a French medical-affairs team, and
    filtering afterwards can only subtract from what we already paid to fetch.
    """
    if not (sponsor or "").strip():
        return []

    params = {
        "query.spons": sponsor,
        "query.cond": condition,
        # Paris, radius covering France and its immediate neighbours.
        "filter.geo": f"distance(48.85,2.35,{around_paris_km}km)",
        "pageSize": str(max(1, min(limit, 100))),
        "countTotal": "true",
        "sort": "LastUpdatePostDate:desc",
    }
    url = f"{_CTGOV}?{urllib.parse.urlencode(params)}"

    try:
        payload = _get_json(url)
    except Exception as exc:                        # noqa: BLE001
        logger.warning("literature.ctgov_failed", sponsor=sponsor, error=str(exc)[:160])
        return []

    out: list[dict] = []
    for study in payload.get("studies") or []:
        protocol = study.get("protocolSection") or {}
        ident = protocol.get("identificationModule") or {}
        status = protocol.get("statusModule") or {}
        design = protocol.get("designModule") or {}
        desc = protocol.get("descriptionModule") or {}

        nct = ident.get("nctId")
        if not nct:
            continue
        title = (ident.get("briefTitle") or ident.get("officialTitle") or "").strip()
        summary = (desc.get("briefSummary") or "").strip()
        phases = ", ".join(design.get("phases") or []) or "n/a"
        enrolment = (design.get("enrollmentInfo") or {}).get("count")

        out.append({
            "title": title,
            "text": (f"{summary}\n\nPhase: {phases}. Status: "
                     f"{status.get('overallStatus') or 'unknown'}. "
                     f"Enrolment: {enrolment if enrolment is not None else 'n/a'}."),
            "url": f"https://clinicaltrials.gov/study/{nct}",
            "source_name": "ClinicalTrials.gov",
            "published_date": (status.get("lastUpdatePostDateStruct") or {}).get("date", ""),
            "nct_id": nct,
            "phase": phases,
            "status": status.get("overallStatus"),
            "kind": "trial",
        })
    logger.info("literature.ctgov", sponsor=sponsor, results=len(out),
                total=payload.get("totalCount"))
    return out


def since_datetime(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)
