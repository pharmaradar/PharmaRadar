"""Curated French source registry — the single definition of "a French source".

The client's requirement is a SOURCE requirement, not a language one: content must
come from French publications, institutions and accounts, rather than being French
text plucked out of a worldwide haul. Language is inferred from content we have
already paid to fetch; a source is chosen *before* anything is spent.

Everything that reaches the outside world reads its French scoping from here:

    services/scraper.py       KOL pipeline search + candidate ranking
    routers/discovery.py      Topic Explorer search / deep search
    tasks/burning_topics.py   burning-topic and congress report web context
    services/tinyfish_social.py  Twitter / LinkedIn social search

Measured (TinyFish Search, 2026-08-11), which is why `site:` scoping is used and the
old `(site:.fr OR France OR français)` disjunction was removed:

    "immunothérapie cancer du poumon (site:.fr OR France OR français)"   3/10 .fr
    "immunothérapie cancer du poumon site:.fr"                          10/10 .fr
    "... (site:gustaveroussy.fr OR site:curie.fr OR site:e-cancer.fr)"  10/10 .fr

The OR-group was *worse than no filter*: any page merely containing the word
"France" satisfies it, so sciencedirect.com and boehringer-ingelheim.com ranked in.
That is a content test wearing a source test's clothes — exactly what the client
rejected.

SCOPE IS PER-LANE, NOT GLOBAL. Competitor tracking (AstraZeneca, MSD, BMS) and
congress coverage (ASCO, ESMO, AACR) are inherently international: their news lives
on statnews / endpoints / fiercepharma and the congresses are American. Applying a
French pin there empties those features while the UI keeps rendering them, so those
lanes deliberately stay global. See `Scope`.
"""
from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse


class Scope(str, Enum):
    """Which source pool a given acquisition lane should draw from.

    FR      — French sources strongly preferred (KOL, population, burning topics)
    GLOBAL  — no French pin; required for competitor and congress lanes, which are
              international by nature and would go empty under a French pin
    """

    FR = "fr"
    GLOBAL = "global"


# ── The registry ──────────────────────────────────────────
# Grouped by category so the UI can explain provenance ("Institution", "Medical
# press") and so callers can build focused `site:` groups. Hosts are bare
# registrable domains, no scheme and no "www." — matching is done on the
# normalised host in `is_french_source`.
#
# Several of these names were already present in the codebase as *keywords*
# (tasks/social.py `_PHARMA_SIGNALS`, the seeded social keywords in main.py). This
# module turns that reviewed vocabulary into addressable sources.

FR_MEDICAL_PRESS: frozenset[str] = frozenset({
    "lequotidiendumedecin.fr",
    "egora.fr",
    "univadis.fr",
    "jim.fr",
    "edimark.fr",
    "vidal.fr",
    "whatsupdoc-lemag.fr",
    "medscape.fr",
    "francais.medscape.com",   # Medscape's French edition lives on a .com host
    "apmnews.com",             # APM News — French health press, .com host
    "lesechos.fr",
    "pourquoidocteur.fr",
    "destinationsante.com",
})

FR_INSTITUTIONS: frozenset[str] = frozenset({
    "e-cancer.fr",             # INCa — Institut National du Cancer
    "cancer.fr",               # INCa's public-facing domain
    "has-sante.fr",            # Haute Autorité de Santé
    "ansm.sante.fr",           # Agence nationale de sécurité du médicament
    "inserm.fr",
    "unicancer.fr",
    "santepubliquefrance.fr",
    "solidarites-sante.gouv.fr",
    "ameli.fr",
    "sante.gouv.fr",
})

FR_CANCER_CENTRES: frozenset[str] = frozenset({
    "gustaveroussy.fr",
    "curie.fr",
    "institut-curie.org",
    "centreleonberard.fr",
    "icans.eu",                # Institut de cancérologie Strasbourg
    "oncopole.fr",
    "institutbergonie.fr",
    "centre-eugene-marquis.fr",
    "iucpq.qc.ca",             # note: Québec — francophone, not France
    "aphp.fr",                 # Assistance Publique - Hôpitaux de Paris
    "chu-lyon.fr",
    "chu-toulouse.fr",
    "chu-bordeaux.fr",
    "chu-lille.fr",
    "chu-nantes.fr",
})

FR_LEARNED_SOCIETIES: frozenset[str] = frozenset({
    "ifct.fr",                 # Intergroupe Francophone de Cancérologie Thoracique
    "splf.fr",                 # Société de Pneumologie de Langue Française
    "afsos.org",               # Soins oncologiques de support
    "sfpo.com",                # Société Française de Pharmacie Oncologique
    "oncorif.fr",
    "arcagy.org",
    "sfro.fr",
})

# French affiliates of pharma companies. The client tracks competitors
# (AstraZeneca, MSD, BMS) but asked for "these companies' messaging regarding lung
# cancer, exclusively in French" — i.e. what the French affiliate says, not the
# global press release. Each host below was resolved on 2026-08-11.
#
# Two competitors cannot be pinned by domain: AstraZeneca France lives at
# astrazeneca.com/content/az-fr and BMS France at bms.com/fr — path-based French
# sections of a global domain. Their French social accounts and bmsmedinfo.fr are
# the addressable handles; the global domains stay out of the registry so they do
# not pull in worldwide corporate news.
FR_PHARMA: frozenset[str] = frozenset({
    "roche.fr",
    "msd-france.com",
    "bmsmedinfo.fr",
    "sanofi.fr",
    "sanofipro.fr",
    "pfizer.fr",
    "novartis.fr",
    "gsk.fr",
    "takeda.fr",
    "amgen.fr",
    "servier.fr",
    "leem.org",                # Les Entreprises du Médicament — industry body
})

FR_PATIENT_ASSOCIATIONS: frozenset[str] = frozenset({
    "ligue-cancer.net",        # Ligue contre le cancer
    "fondation-arc.org",
    "rose-up.fr",
    "patientsenreseau.fr",
    "monreseau-cancerdupoumon.com",
    "cancercontribution.fr",
    "francelymphomeespoir.fr",
})

# Every French source, by category. Ordering matters only for display.
FR_SOURCES_BY_CATEGORY: dict[str, frozenset[str]] = {
    "medical_press": FR_MEDICAL_PRESS,
    "institution": FR_INSTITUTIONS,
    "cancer_centre": FR_CANCER_CENTRES,
    "learned_society": FR_LEARNED_SOCIETIES,
    "patient_association": FR_PATIENT_ASSOCIATIONS,
    "pharma": FR_PHARMA,
}

FR_SOURCE_DOMAINS: frozenset[str] = frozenset(
    d for group in FR_SOURCES_BY_CATEGORY.values() for d in group
)

# Reverse index, built once, so `source_category` is a dict lookup rather than a
# scan of five frozensets per URL.
_DOMAIN_CATEGORY: dict[str, str] = {
    domain: category
    for category, group in FR_SOURCES_BY_CATEGORY.items()
    for domain in group
}

# Platform hosts whose French edition is a distinct locale. Used to prefer the
# French locale of a global platform rather than excluding the platform outright.
FR_PLATFORM_LOCALES: dict[str, str] = {
    "linkedin.com": "fr.linkedin.com",
}

# The locale hosts themselves count as French sources: a post reached through
# fr.linkedin.com was acquired from the French locale, which is a source fact,
# not an inference about its text.
FR_LOCALE_HOSTS: frozenset[str] = frozenset(FR_PLATFORM_LOCALES.values())

# ── French social accounts ────────────────────────────────
# Social platforms have no `.fr` domain to pin, so the source is the ACCOUNT.
# Every handle below was verified empirically on 2026-08-12 by issuing
# `site:x.com/<handle>` and confirming the results are that account's own posts —
# a probe proves the account exists, whereas a researched list invents handles.
#
# Not listed because no findable X account was confirmed: AstraZeneca France,
# MSD France, IFCT, SPLF, AP-HP. They remain reachable through their websites
# (FR_LEARNED_SOCIETIES / FR_CANCER_CENTRES) and can be added once verified.
FR_X_ACCOUNTS: tuple[str, ...] = (
    # Cancer centres & research
    "GustaveRoussy",
    "institut_curie",
    "Inserm",
    # Institutions & agencies
    "Institut_cancer",      # INCa
    "Unicancer",
    "HAS_sante",
    "ansm",
    # Patient associations
    "laliguecancer",
    "FondationARC",
    # Pharma (French affiliates)
    "Roche_France",
    "SanofiFR",
    "BMSFrance",
    # Medical press
    "leQdM",                # Le Quotidien du Médecin
    "univadisfr",
)

# Batched into `site:` disjunctions so N accounts cost one search rather than N.
_X_GROUP_SIZE = 5


def fr_account_groups(platform: str, group_size: int = _X_GROUP_SIZE) -> list[str]:
    """`site:` groups pinning a search to verified French accounts.

    Only X/Twitter is account-pinnable through the web index: LinkedIn has a real
    country locale (fr.linkedin.com) which is cheaper and broader, and Instagram
    is not indexed usefully this way. Returns [] for anything else so callers can
    concatenate unconditionally.
    """
    if platform != "twitter":
        return []
    handles = FR_X_ACCOUNTS
    groups: list[str] = []
    for i in range(0, len(handles), group_size):
        batch = handles[i:i + group_size]
        joined = " OR ".join(f"site:x.com/{h}" for h in batch)
        groups.append(f"({joined})")
    return groups


def is_fr_account_url(url: str) -> bool:
    """True if a social URL belongs to a verified French account in the registry."""
    lowered = (url or "").lower()
    return any(
        f"x.com/{handle.lower()}" in lowered or f"twitter.com/{handle.lower()}" in lowered
        for handle in FR_X_ACCOUNTS
    )


# ── Disease-area focus ────────────────────────────────────
# `Target.disease_area` was stored and editable but never reached the scraper —
# it only filtered the dashboard. The client asked for competitor tracking to
# "strictly focus on lung cancer, exclusively in French", which is a constraint
# on *acquisition*, so the vocabulary lives here and is applied when queries are
# built (services/scraper.build_search_queries).
#
# French first, deliberately: a French oncologist writes CBNPC, never NSCLC, so
# an English-only term list cannot reach French sources at all. The English
# terms stay because French affiliates publish congress material in English.
DISEASE_FOCUS: dict[str, dict[str, tuple[str, ...]]] = {
    "lung_cancer": {
        "fr": (
            '"cancer du poumon"',
            '"cancer bronchique"',
            "CBNPC",
            "CPC",
            '"oncologie thoracique"',
            '"cancer pulmonaire"',
        ),
        "en": (
            '"lung cancer"',
            "NSCLC",
            "SCLC",
            '"thoracic oncology"',
        ),
    },
}


def focus_terms(disease_area: str | None, scope: str = "fr") -> tuple[str, ...]:
    """Search terms that pin a target's queries to one disease area.

    Returns () for an unknown or unset area, so a target with no focus keeps its
    normal broad coverage.
    """
    entry = DISEASE_FOCUS.get((disease_area or "").strip().lower())
    if not entry:
        return ()
    if scope == Scope.FR.value:
        # French terms lead; a couple of English ones keep congress coverage.
        return entry["fr"] + entry["en"][:2]
    return entry["en"] + entry["fr"][:2]


def focus_clause(disease_area: str | None, scope: str = "fr") -> str:
    """The focus terms as one OR group, or "" when the target has no focus."""
    terms = focus_terms(disease_area, scope)
    return f"({' OR '.join(terms)})" if terms else ""


# Search-engine locale hints for the TinyFish CLI. The CLI accepts
# `--location <value>` and `--language <value>` on `search query`; the codebase
# never passed them, so every search ran at the CLI's US/EN default. Measured:
# the same KOL query returned 0/10 French sources without these and 3/9 with.
SEARCH_LOCATION_FR = "France"
SEARCH_LANGUAGE_FR = "fr"


def normalize_host(url_or_host: str) -> str:
    """Return the bare lowercase host: no scheme, no 'www.', no port or path."""
    value = (url_or_host or "").strip().lower()
    if not value:
        return ""
    if "//" not in value:
        # Bare host or host/path — urlparse needs a scheme to populate netloc.
        value = "//" + value
    try:
        host = urlparse(value).netloc or ""
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]        # strip credentials and port
    return host[4:] if host.startswith("www.") else host


def is_french_source(url_or_host: str) -> bool:
    """True if the URL/host is a registry entry, a subdomain of one, or any .fr host.

    The `.fr` TLD is included deliberately: the registry cannot enumerate every
    French hospital and society, and a `.fr` domain is itself a France signal —
    the whole point of a source test. Subdomains match so `presse.curie.fr` and
    `wptest.splf.fr` (both seen in live results) are recognised.
    """
    host = normalize_host(url_or_host)
    if not host:
        return False
    if host.endswith(".fr"):
        return True
    if host in FR_LOCALE_HOSTS:
        return True
    if any(host == d or host.endswith("." + d) for d in FR_SOURCE_DOMAINS):
        return True
    # Social platforms have no French domain — the source is the account.
    return is_fr_account_url(url_or_host)


def source_category(url_or_host: str) -> str | None:
    """Registry category for a URL, or None when it is not a registry entry.

    A bare `.fr` host that is not in the registry returns None — it counts as a
    French source but has no curated category.
    """
    host = normalize_host(url_or_host)
    if not host:
        return None
    if is_fr_account_url(url_or_host):
        return "social_account"
    if host in FR_LOCALE_HOSTS:
        return "platform_locale"
    hit = _DOMAIN_CATEGORY.get(host)
    if hit:
        return hit
    for domain, category in _DOMAIN_CATEGORY.items():
        if host.endswith("." + domain):
            return category
    return None


def site_scope(domains, *, prefix: str = "") -> str:
    """Build a `site:` disjunction: ``(site:a.fr OR site:b.fr)``.

    Never emits a bare "France"/"français" term — that is the content test the
    client rejected, and it measurably destroys the scoping (3/10 vs 10/10).
    Returns "" for an empty list so callers can concatenate unconditionally.
    """
    scoped = [f"site:{normalize_host(d)}" for d in domains if normalize_host(d)]
    if not scoped:
        return ""
    joined = " OR ".join(scoped)
    return f"{prefix}({joined})" if prefix else f"({joined})"


def fr_site_groups(group_size: int = 6) -> list[str]:
    """Split the registry into `site:` groups small enough to stay effective.

    One disjunction over ~60 domains dilutes the query, so callers rotate several
    focused groups across their query slots instead. Groups are built per category
    (press with press, institutions with institutions) so each query targets a
    coherent slice of the French ecosystem. Deterministic ordering keeps search
    caches stable across runs.
    """
    groups: list[str] = []
    for category in sorted(FR_SOURCES_BY_CATEGORY):
        domains = sorted(FR_SOURCES_BY_CATEGORY[category])
        for i in range(0, len(domains), group_size):
            scope = site_scope(domains[i:i + group_size])
            if scope:
                groups.append(scope)
    return groups


def localize_platform(host: str) -> str:
    """Map a global platform host to its French locale, else return it unchanged."""
    return FR_PLATFORM_LOCALES.get(normalize_host(host), host)
