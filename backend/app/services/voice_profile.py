"""Who is speaking — voice classification for market-research reports.

The client asked reports to break mentions down by voice: KOLs / Doctors /
Patients / Others. Nothing in the schema answers that question directly, so this
module is deliberate about what is *known* versus *inferred*.

What the data actually supports (measured against the live DB on 2026-08-12):

* **KOL is exact.** An ExtractedInsight is attributed to a tracked Target by
  foreign key, so `target_type == 'kol'` is a fact, not a guess.
* **Organisation is near-exact.** The curated French source registry
  (services/fr_sources) already maps a domain to institution / learned society /
  cancer centre / patient association / medical press / pharma.
* **Doctor and Patient are genuinely weak.** `SocialPost.kind` is `'field'` for
  100% of rows — it never separates KOLs — and **0 of 193** social authors match
  a tracked Target name or handle, because social authors are mostly org handles
  (`@HAS_sante`, `roche`, `bms_france`) and unrelated individuals. The only
  honest signal in an author string is a title prefix (`dr…`, `pr…`, `@Dr…`).

So this returns a bucket *plus a confidence*, and the report is expected to say
which share was inferred. A voice chart that silently presents guesses as facts
would be worse than no chart, because the client would act on it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.fr_sources import normalize_host, source_category

# The four buckets the client asked for. `ORGANISATION` is reported inside
# "Others" but kept separate internally: knowing a mention came from Roche's own
# account rather than an unidentified individual changes what it means.
KOL = "kol"
DOCTOR = "doctor"
PATIENT = "patient"
ORGANISATION = "organisation"
OTHER = "other"

BUCKETS = (KOL, DOCTOR, PATIENT, ORGANISATION, OTHER)

# Labels for display and for the PDF.
BUCKET_LABELS = {
    KOL: "KOLs",
    DOCTOR: "Doctors / HCPs",
    PATIENT: "Patients & associations",
    ORGANISATION: "Organisations (industry, institutions, press)",
    OTHER: "Others / unidentified",
}

EXACT = "exact"       # known from a foreign key or the curated registry
INFERRED = "inferred"  # heuristic on the author string
UNKNOWN = "unknown"    # no signal at all

# Registry categories → voice bucket. These are curated domains, so the mapping
# is as reliable as the registry itself.
_CATEGORY_BUCKET = {
    "institution": ORGANISATION,
    "learned_society": ORGANISATION,
    "cancer_centre": ORGANISATION,
    "medical_press": ORGANISATION,
    "pharma": ORGANISATION,
    "patient_association": PATIENT,
    "social_account": ORGANISATION,
    "platform_locale": None,        # a locale says nothing about who posted
}

# Title as a separate token: "Dr Smith", "@Pr_Girard", "jean.md".
_DOCTOR_RE = re.compile(
    r"(?:^|[\s@._-])(?:dr|dre|prof|pr|md|phd|docteur|professeur)(?:[\s._-]|$)",
    re.IGNORECASE,
)

# Clinicians very often run the title into the handle — "drmonish_childneuro",
# "drozgurtoklucu" — which the token rule above cannot see. Allowing a bare
# "dr" prefix would swallow ordinary words, so those are listed out explicitly.
# Cheaper and more predictable than a clinical-keyword whitelist, which would
# still miss any specialty not enumerated.
_DR_PREFIX_RE = re.compile(r"^@?(?:dr|pr)[a-z]{2,}[a-z0-9._-]*$", re.IGNORECASE)
_DR_PREFIX_FALSE_FRIENDS = frozenset({
    "drug", "drugs", "drugstore", "drone", "drones", "dream", "dreams",
    "drive", "driver", "driven", "dress", "draft", "drain", "drama", "dramas",
    "drink", "drinks", "dritte", "droit", "droits", "drole",
    "press", "presse", "print", "prime", "prince", "prize", "price", "prices",
    "profil", "profile", "project", "promo", "proof", "proud", "provider",
    "practice", "practical", "predict", "premium", "preview", "product",
    "products", "program", "progress", "protect", "protein", "protocol",
})


def _looks_like_clinician_handle(name: str) -> bool:
    """True for run-together clinician handles, false for ordinary dr/pr words."""
    handle = name.strip().lstrip("@").lower()
    if " " in handle or not _DR_PREFIX_RE.match(name.strip()):
        return False
    # Reject when the whole handle is a common word, or starts with one
    # ("drugsafety" → drug). Split tokens are checked too: "drone_pilot".
    first = re.split(r"[._-]", handle)[0]
    if first in _DR_PREFIX_FALSE_FRIENDS:
        return False
    return not any(first.startswith(word) for word in _DR_PREFIX_FALSE_FRIENDS)

# Patient-community wording, French first.
_PATIENT_RE = re.compile(
    r"(?:patient|patiente|survivor|survivant|association|asso|aidant|malade|"
    r"proche|temoignage|témoignage|vivreavec|monparcours)",
    re.IGNORECASE,
)

# Organisation markers in a bare handle, when the domain is unknown.
_ORG_RE = re.compile(
    r"(?:pharma|labo|laborator|institut|hopital|hôpital|chu\b|clinic|clinique|"
    r"fondation|foundation|societ|société|federation|fédération|agence|agency|"
    r"ministere|ministère|journal|revue|magazine|news|presse|press|inc\b|sa\b|"
    r"gmbh|ltd|official|france\b|group|groupe|"
    # French health bodies and networks write themselves as acronyms or
    # hyphenated slugs, so none of the words above appear. Measured on the live
    # table, 100 of 152 distinct authors fell to "other" — among them ANSM and
    # HAS, the French drug agency and health authority, and IFCT, the francophone
    # thoracic-oncology intergroup. On a French pharma platform those are the
    # most authoritative voices there are, and they were counted as unknown.
    r"unicancer|oncorif|oncopl|assurance-maladie|inca\b|sfpo|splf|afsos|"
    r"reseau|réseau|ifsi|ifct|aphp|ap-hp|inserm|cnrs|invs|"
    # Learned societies, journals and registries, French and international.
    r"nejm|lancet|jama|bmj|esmo|asco|aacr|who\b|oms\b)",
    re.IGNORECASE,
)

# Standalone acronyms, matched whole so they cannot fire inside a longer word:
# "has" is a verb, "ars" and "cnam" are not.
_ORG_ACRONYMS = frozenset({
    "ansm", "has", "hcsp", "inca", "aphp", "ap-hp", "inserm", "cnrs", "ars",
    "cnam", "ameli", "splf", "sfpo", "afsos", "ifct", "unicancer", "oncorif",
    "nejm", "esmo", "asco", "aacr", "ema", "fda", "oms", "who",
})

# A LinkedIn public identifier for a PERSON: "firstname-lastname" plus
# LinkedIn's disambiguating suffix, e.g. "mahmoud-zureik-92548b161". Company
# pages never carry that suffix ("haute-autorite-de-sante", "ap-hp"), which is
# what makes the two separable.
#
# The suffix MUST contain a digit. Without that requirement the last word of a
# hyphenated organisation name qualifies as an id: "ligue-contre-le-cancer" read
# as a person, which would file the Ligue contre le cancer — a major French
# patient organisation — as an individual and drop it out of the patient bucket.
_LINKEDIN_PERSON_RE = re.compile(
    r"^[a-z]+(?:-[a-z]+){1,3}-(?=[a-z0-9]*[0-9])[a-z0-9]{6,12}$", re.IGNORECASE)


async def account_voice_map(session) -> dict[str, str]:
    """handle (lowercased) -> voice bucket, from the tracked-accounts registry.

    The registry is the client's own curation: he adds and edits these accounts
    in the UI, and each carries a category (institution, cancer_centre,
    learned_society, patient_association...) that _CATEGORY_BUCKET already knows
    how to read. Until now that was consulted only for URLs, so an account the
    client had explicitly categorised was still classified by guessing at its
    handle — and 67% of distinct authors landed in "other", ANSM and HAS among
    them.

    Loaded once and passed down rather than queried per author, the same way
    tasks/social threads the tracked-handle list through a scan.
    """
    from sqlalchemy import select

    from app.models import TrackedAccount

    rows = (await session.execute(
        select(TrackedAccount.handle, TrackedAccount.category, TrackedAccount.role)
    )).all()

    out: dict[str, str] = {}
    for handle, category, role in rows:
        if not handle:
            continue
        # `role` is the more specific field and wins where it is set; `category`
        # is what is actually populated today.
        bucket = _CATEGORY_BUCKET.get((role or "").strip().lower()) \
            or _CATEGORY_BUCKET.get((category or "").strip().lower())
        if bucket:
            out[handle.strip().lstrip("@").lower()] = bucket
    return out


def _is_org_acronym(name: str) -> bool:
    cleaned = name.strip().lstrip("@").lower()
    return cleaned in _ORG_ACRONYMS


def looks_like_linkedin_person(name: str) -> bool:
    """A LinkedIn slug that identifies an individual rather than a company."""
    cleaned = (name or "").strip().lstrip("@").lower()
    if cleaned in _ORG_ACRONYMS:
        return False
    return bool(_LINKEDIN_PERSON_RE.match(cleaned))


@dataclass
class Voice:
    """One speaker in the material, with how confidently it was classified."""

    name: str
    bucket: str
    confidence: str
    source: str = ""          # domain or platform the mention came from
    mentions: int = 0
    evidence: str = ""        # why this bucket was chosen — shown in the report

    def label(self) -> str:
        return BUCKET_LABELS.get(self.bucket, BUCKET_LABELS[OTHER])


@dataclass
class VoiceBreakdown:
    """Aggregated distribution, plus how much of it is guesswork."""

    counts: dict[str, int] = field(default_factory=dict)
    voices: list[Voice] = field(default_factory=list)
    total: int = 0

    @property
    def exact_share(self) -> float:
        """Fraction of mentions whose bucket came from a key or the registry."""
        if not self.total:
            return 0.0
        exact = sum(v.mentions for v in self.voices if v.confidence == EXACT)
        return exact / self.total

    def as_rows(self) -> list[dict]:
        """Display rows, largest bucket first, with percentages."""
        rows = []
        for bucket in BUCKETS:
            count = self.counts.get(bucket, 0)
            if not count:
                continue
            rows.append({
                "bucket": bucket,
                "label": BUCKET_LABELS[bucket],
                "mentions": count,
                "percent": round(100 * count / self.total) if self.total else 0,
            })
        return sorted(rows, key=lambda r: r["mentions"], reverse=True)


def classify(author: str | None, *, url: str = "", is_tracked_kol: bool = False,
             target_type: str | None = None,
             known_accounts: dict[str, str] | None = None) -> tuple[str, str, str]:
    """Classify one speaker. Returns ``(bucket, confidence, evidence)``.

    Order matters: facts first, heuristics last.
    """
    # 1. Foreign-key truth — an insight belongs to a tracked target.
    if is_tracked_kol or target_type == "kol":
        return KOL, EXACT, "tracked KOL target"
    if target_type == "competitor":
        return ORGANISATION, EXACT, "tracked competitor target"

    # 2. Curated registry — the domain is one we chose to monitor.
    host = normalize_host(url)
    category = source_category(url) if url else None
    if category:
        bucket = _CATEGORY_BUCKET.get(category)
        if bucket:
            return bucket, EXACT, f"registry: {category.replace('_', ' ')}"

    name = (author or "").strip()
    if not name:
        return OTHER, UNKNOWN, "no author recorded"

    # 2b. The client's own registry, before any heuristic. If he has categorised
    # this account, that is a fact about it and no pattern should overrule it.
    if known_accounts:
        registered = known_accounts.get(name.lstrip("@").lower())
        if registered:
            return registered, EXACT, "categorised in the tracked-accounts registry"

    # 3. Heuristics on the author string. Patient wording is checked before the
    # doctor title so "association de patients" is not read as an institution.
    if _PATIENT_RE.search(name):
        return PATIENT, INFERRED, "patient/association wording in author"
    if _DOCTOR_RE.search(name) or _looks_like_clinician_handle(name):
        return DOCTOR, INFERRED, "clinical title in author"
    if _is_org_acronym(name):
        return ORGANISATION, INFERRED, "known health body or society"
    if _ORG_RE.search(name) or (host and not host.endswith((".com", ".net"))
                                and category is None and " " not in name and "." in name):
        return ORGANISATION, INFERRED, "organisation wording in author"

    # Checked LAST, after every organisation signal: a LinkedIn slug proves this
    # is an individual, not which KIND. Calling them a doctor would be a guess
    # (many are researchers, managers or patients), so they land in OTHER with
    # the reason recorded rather than being promoted on no evidence.
    if looks_like_linkedin_person(name):
        return OTHER, INFERRED, "individual LinkedIn profile, role unknown"

    return OTHER, UNKNOWN, "no identifying signal"


def build_breakdown(mentions: list[dict]) -> VoiceBreakdown:
    """Aggregate classified mentions into a distribution.

    Each mention is ``{author, url, is_tracked_kol, target_type}``. Speakers are
    merged case-insensitively so one prolific account is a single voice with a
    mention count, not N separate voices.
    """
    by_speaker: dict[str, Voice] = {}
    counts: dict[str, int] = {}

    for mention in mentions:
        author = (mention.get("author") or "").strip()
        url = mention.get("url") or ""
        bucket, confidence, evidence = classify(
            author,
            url=url,
            is_tracked_kol=bool(mention.get("is_tracked_kol")),
            target_type=mention.get("target_type"),
        )
        key = (author or normalize_host(url) or "unattributed").lower()
        voice = by_speaker.get(key)
        if voice is None:
            voice = Voice(
                name=author or normalize_host(url) or "Unattributed",
                bucket=bucket,
                confidence=confidence,
                source=normalize_host(url),
                evidence=evidence,
            )
            by_speaker[key] = voice
        elif confidence == EXACT and voice.confidence != EXACT:
            # A later mention identified this speaker definitively — upgrade it.
            voice.bucket, voice.confidence, voice.evidence = bucket, confidence, evidence
        voice.mentions += 1

    for voice in by_speaker.values():
        counts[voice.bucket] = counts.get(voice.bucket, 0) + voice.mentions

    return VoiceBreakdown(
        counts=counts,
        voices=sorted(by_speaker.values(), key=lambda v: v.mentions, reverse=True),
        total=sum(counts.values()),
    )
