"""French medical press feeds — France-first at acquisition, not by filtering.

Every other lane targets France by searching in French and then discarding what
misses. A French outlet's own feed needs neither: everything in it is French
market content by construction, it costs nothing, and no scraping-ToS question
arises because publishers offer these feeds for exactly this purpose.

Feeds are verified before being listed. Guessing URLs produces silent zeros that
look identical to a quiet news week — the four below were probed and returned
items; HAS (403), ANSM and INCa did not, so they are recorded as unavailable
rather than left in the list to fail nightly.

Relevance filtering happens here because these are GENERAL medical outlets: most
of Le Quotidien du Médecin is not about lung cancer, and storing all of it would
bury the client's disease area in unrelated news.
"""
from __future__ import annotations

import html
import re
import urllib.request
from datetime import datetime, timezone
from xml.etree import ElementTree

import structlog

logger = structlog.get_logger(__name__)

_TIMEOUT = 20
# Some publishers reject the default urllib agent outright.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PharmaRadar/1.0; +pharma intelligence)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# name -> feed URL. Verified 2026-08-14; each returned items.
FEEDS: dict[str, str] = {
    "Le Quotidien du Médecin": "https://www.lequotidiendumedecin.fr/rss.xml",
    "Le Généraliste": "https://www.legeneraliste.fr/rss.xml",
    "Egora": "https://www.egora.fr/rss.xml",
    "Ligue contre le cancer": "https://www.ligue-cancer.net/rss.xml",
    # A cancer centre, so far denser in oncology than the general press feeds.
    "Gustave Roussy": "https://www.gustaveroussy.fr/fr/rss.xml",
}

# Probed and NOT working, kept so nobody re-adds them expecting success:
#   HAS      https://www.has-sante.fr/...        403, blocks non-browser clients
#   ANSM     https://ansm.sante.fr/rss           404, no feed published
#   INCa     https://www.e-cancer.fr/rss.xml     TLS chain fails verification
#   SPLF     https://splf.fr/feed/               403
#   IFCT     https://www.ifct.fr/feed/           returns a page, no <item> elements
#   Curie / JIM / Univadis                       404, no feed at the obvious paths
#
# Yield warning, measured: the general medical press is mostly NOT oncology —
# Le Quotidien du Médecin returned 50 items mentioning "cancer" zero times.
# This lane is free and runs daily, so it catches lung-cancer coverage when it
# appears, but it is not a fix for corpus size. Publications and trials are.
# The INCa case is deliberately NOT worked around by disabling certificate
# checking: silently accepting an unverified certificate to ingest health data
# is a worse trade than missing one feed.

# An item must mention at least one of these to be stored. Both languages,
# accented and folded, because feed titles are inconsistent about accents.
_RELEVANT = (
    "cancer du poumon", "cancer bronchique", "cancer pulmonaire", "poumon",
    "cbnpc", "cpnpc", "nsclc", "mésothéliome", "mesotheliome",
    "oncologie", "oncologie thoracique", "immunothérapie", "immunotherapie",
    "lung cancer", "thoracique", "tumeur", "chimiothérapie", "chimiotherapie",
    "dépistage", "depistage", "tecentriq", "keytruda", "imfinzi", "opdivo",
    "atezolizumab", "pembrolizumab", "durvalumab", "nivolumab", "osimertinib",
)


def _text(element, *names: str) -> str:
    for name in names:
        found = element.find(name)
        if found is not None and (found.text or "").strip():
            return html.unescape(found.text.strip())
    return ""


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").replace("&nbsp;", " ").strip()


def is_relevant(title: str, summary: str) -> bool:
    """Whether a general-press item touches the client's disease area."""
    haystack = f"{title} {summary}".lower()
    return any(term in haystack for term in _RELEVANT)


def fetch_feed(name: str, url: str, *, limit: int = 60) -> list[dict]:
    """Parse one feed. Never raises — a dead feed must not end the sweep."""
    try:
        request = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read()
    except Exception as exc:                        # noqa: BLE001
        logger.warning("fr_feeds.fetch_failed", feed=name, error=str(exc)[:140])
        return []

    try:
        root = ElementTree.fromstring(raw)
    except Exception as exc:                        # noqa: BLE001
        logger.warning("fr_feeds.parse_failed", feed=name, error=str(exc)[:140])
        return []

    # RSS uses <item>, Atom uses <entry>; support both rather than assume.
    items = root.iter("item") if root.find(".//item") is not None else root.iter(
        "{http://www.w3.org/2005/Atom}entry")

    out: list[dict] = []
    for item in items:
        title = _text(item, "title", "{http://www.w3.org/2005/Atom}title")
        link = _text(item, "link", "guid")
        if not link:
            atom_link = item.find("{http://www.w3.org/2005/Atom}link")
            link = atom_link.get("href", "") if atom_link is not None else ""
        summary = _strip_html(_text(
            item, "description", "{http://www.w3.org/2005/Atom}summary", "content:encoded"))
        published = _text(item, "pubDate", "{http://www.w3.org/2005/Atom}updated")
        if not title or not link:
            continue
        out.append({
            "title": title,
            "snippet": summary[:2000],
            "url": link,
            "source_name": name,
            "published_date": published[:32],
        })
        if len(out) >= limit:
            break
    return out


def fetch_all(*, only_relevant: bool = True) -> list[dict]:
    """Every configured feed, optionally narrowed to the disease area."""
    collected: list[dict] = []
    for name, url in FEEDS.items():
        items = fetch_feed(name, url)
        kept = [i for i in items if not only_relevant
                or is_relevant(i["title"], i["snippet"])]
        logger.info("fr_feeds.fetched", feed=name, items=len(items), relevant=len(kept))
        collected.extend(kept)
    return collected


def now() -> datetime:
    return datetime.now(timezone.utc)
