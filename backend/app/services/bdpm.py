"""Base de Données Publique des Médicaments — French drug registry, HAS rulings, ANSM shortages.

Official, free, no key. Plain tab-separated snapshots re-published daily.

Three things about these files will silently corrupt the data if you take the
obvious route, and all three were hit while building this:

1. **The old download URL is dead and lies about it.** The documented
   `telechargement.php?fichier=<name>.txt` endpoint now returns HTTP 404 *with a
   28KB HTML error page as the body*. A fetcher that checks only the status code
   — or worse, caches "the file is non-empty, skip it" — stores markup as data
   and every downstream parse quietly yields zero rows. Hence `_looks_like_data`
   below: the content is validated, not the response code.

2. **The encoding is Windows-1252, not Latin-1.** They are nearly identical, so
   latin-1 decodes without raising — but bytes 0x80-0x9F differ, and the file has
   3,868 of them. 0x92 is a curly apostrophe in cp1252 and a control character in
   latin-1, so "n'apporte pas" becomes "n\\x92apporte pas" and then "napporte
   pas". Every French apostrophe in the reasoning text, mangled, with no error.

3. **Dates are two different formats in the same download.** Rulings use
   `YYYYMMDD`, shortages use `DD/MM/YYYY`.
"""
from __future__ import annotations

import hashlib
import re
import urllib.request
from datetime import date

import structlog

logger = structlog.get_logger(__name__)

_BASE = "https://base-donnees-publique.medicaments.gouv.fr/download/file"
_TIMEOUT = 90
_UA = "PharmaRadar/1.0 (pharma intelligence; contact via platform administrator)"

# See the module docstring: cp1252, never latin-1.
_ENCODING = "cp1252"

FILES = {
    "specialities": "CIS_bdpm",
    "asmr": "CIS_HAS_ASMR_bdpm",
    "smr": "CIS_HAS_SMR_bdpm",
    "shortages": "CIS_CIP_Dispo_Spec",
    "ct_links": "HAS_LiensPageCT_bdpm",
}

# CIS_bdpm column positions (verified against the live file).
_CIS_CODE, _CIS_NAME, _CIS_MARKETING, _CIS_HOLDER = 0, 1, 6, 10


def _looks_like_data(text: str) -> bool:
    """Reject an HTML error page served with any status code.

    A real file is tab-separated and starts with an 8-digit CIS code or a
    "CT-nnnnn" opinion reference. An error page starts with a doctype.
    """
    head = text.lstrip()[:400].lower()
    if head.startswith(("<!doctype", "<html", "<?xml")):
        return False
    return "\t" in text[:4000]


def fetch_file(key: str) -> list[list[str]]:
    """Download one BDPM file and return its rows as split fields."""
    name = FILES[key]
    url = f"{_BASE}/{name}.txt"
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        raw = response.read()

    text = raw.decode(_ENCODING, errors="replace")
    if not _looks_like_data(text):
        # Loud, because the failure mode this guards is silence.
        raise ValueError(
            f"{name}.txt did not return tabular data ({len(raw)} bytes) — "
            f"the endpoint is probably serving an error page")

    rows = [line.split("\t") for line in text.splitlines() if line.strip()]
    logger.info("bdpm.fetched", file=name, rows=len(rows))
    return rows


def _date_compact(value: str) -> date | None:
    """'20260715' -> date. Ruling format."""
    value = (value or "").strip()
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None


def _date_slashed(value: str) -> date | None:
    """'30/07/2026' -> date. Shortage format."""
    parts = (value or "").strip().split("/")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None


def _clean(text: str) -> str:
    """Strip the HTML the reasoning text is stored with.

    HAS stores `<br>` inside a tab-separated field. Rendering that raw would
    show markup to the reader; stripping it without replacing keeps sentences
    from running together.
    """
    text = re.sub(r"<br\s*/?>", " ", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


def load_specialities() -> dict[str, dict]:
    """CIS code -> {name, holder, marketing}. The join table for everything else."""
    out: dict[str, dict] = {}
    for row in fetch_file("specialities"):
        if len(row) <= _CIS_HOLDER:
            continue
        out[row[_CIS_CODE].strip()] = {
            "name": row[_CIS_NAME].strip(),
            "holder": row[_CIS_HOLDER].strip(),
            "marketing": row[_CIS_MARKETING].strip(),
        }
    return out


def load_ct_links() -> dict[str, str]:
    """Opinion reference -> HAS page URL."""
    out: dict[str, str] = {}
    for row in fetch_file("ct_links"):
        if len(row) >= 2 and row[0].strip():
            out[row[0].strip()] = row[1].strip()
    return out


def match_brand(drug_name: str):
    """Find the tracked brand this registry entry is, or None.

    Matched on the registry's own product name against the brand registry that
    already drives share of voice, so "a drug we track" has one definition.
    Word-boundary anchored for the same reason brands.py does it: a substring
    match would find "Opdivo" inside "Opdivoqtig".
    """
    from app.services.brands import BRANDS

    upper = (drug_name or "").upper()
    for brand in BRANDS:
        if re.search(rf"\b{re.escape(brand.name.upper())}\b", upper):
            return brand
    return None


def _hash(*parts) -> str:
    return hashlib.sha256("|".join(str(p or "") for p in parts).encode()).hexdigest()


def collect_events() -> list[dict]:
    """Every ASMR ruling and ANSM shortage that concerns a tracked drug."""
    specialities = load_specialities()
    ct_links = load_ct_links()
    events: list[dict] = []

    # ── HAS rulings ───────────────────────────────────────
    # Columns: CIS, opinion ref, motive, date(YYYYMMDD), rating, reasoning
    for row in fetch_file("asmr"):
        if len(row) < 6:
            continue
        cis = row[0].strip()
        spec = specialities.get(cis)
        if not spec:
            continue
        brand = match_brand(spec["name"])
        if not brand:
            continue

        opinion_ref = row[1].strip()
        rating = row[4].strip()
        event_date = _date_compact(row[3])
        summary = _clean(row[5])
        events.append({
            "kind": "asmr",
            "cis_code": cis,
            "drug_name": spec["name"][:512],
            "brand": brand.name,
            "owner": brand.owner,
            "holder": spec["holder"][:255] or None,
            "rating": rating[:64] or None,
            "opinion_ref": opinion_ref[:32] or None,
            "event_date": event_date,
            "end_date": None,
            "summary": summary or None,
            "url": ct_links.get(opinion_ref),
            # Rating is part of the key: one opinion can grant different ASMRs
            # to different presentations of the same drug on the same day.
            "content_hash": _hash("asmr", cis, opinion_ref, rating, event_date),
        })

    # ── ANSM shortages ────────────────────────────────────
    # Columns: CIS, ?, code, status text, start(DD/MM/YYYY), end, ?, ANSM URL
    for row in fetch_file("shortages"):
        if len(row) < 8:
            continue
        cis = row[0].strip()
        spec = specialities.get(cis)
        if not spec:
            continue
        brand = match_brand(spec["name"])
        if not brand:
            continue

        status = row[3].strip()
        start = _date_slashed(row[4])
        events.append({
            "kind": "shortage",
            "cis_code": cis,
            "drug_name": spec["name"][:512],
            "brand": brand.name,
            "owner": brand.owner,
            "holder": spec["holder"][:255] or None,
            "rating": status[:64] or None,
            "opinion_ref": None,
            "event_date": start,
            "end_date": _date_slashed(row[5]),
            "summary": None,
            "url": row[7].strip() or None,
            "content_hash": _hash("shortage", cis, status, start),
        })

    logger.info("bdpm.collected", events=len(events),
                asmr=sum(1 for e in events if e["kind"] == "asmr"),
                shortages=sum(1 for e in events if e["kind"] == "shortage"))
    return events
