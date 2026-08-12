"""Brand detection — share of voice by product.

A medical-affairs team does not think in "topics", it thinks in assets: is the
conversation about Tecentriq or Keytruda, and is ours winning or losing ground?
The client named exactly that set — "lung cancer, Tecentriq, Keytruda, Imfinzi" —
so the product dimension is what turns a pile of insights into something a brand
lead can act on.

Everything needed is already in the text we store; nothing extra is scraped. A
brand is matched on its trade name AND its INN (generic) name, because clinicians
write "atezolizumab" at least as often as "Tecentriq", and press releases use
both in the same paragraph.

Matching is word-boundary regex, not substring: "Opdivo" must not be found inside
"Opdivoqtig", and a bare "MSD" inside "MSDN" would be a false positive that
inflates a competitor's share.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Brand:
    """One tracked product. `owner` drives the us-versus-them split."""

    name: str
    owner: str                      # "roche" | competitor company name
    inn: tuple[str, ...] = ()       # generic names, as clinicians write them
    indication: str = ""

    @property
    def is_ours(self) -> bool:
        return self.owner == "roche"


# Oncology-focused, French-market relevant. Roche first, then the competitors the
# client asked to track. Extend here — the API and UI read this list.
BRANDS: tuple[Brand, ...] = (
    # ── Roche / Genentech ──
    Brand("Tecentriq", "roche", ("atezolizumab",), "lung, bladder, liver"),
    Brand("Avastin", "roche", ("bevacizumab",), "lung, colorectal"),
    Brand("Alecensa", "roche", ("alectinib",), "ALK+ lung"),
    Brand("Rozlytrek", "roche", ("entrectinib",), "ROS1/NTRK lung"),
    Brand("Herceptin", "roche", ("trastuzumab",), "breast, gastric"),
    Brand("Perjeta", "roche", ("pertuzumab",), "breast"),
    Brand("Kadcyla", "roche", ("trastuzumab emtansine", "T-DM1"), "breast"),
    Brand("Phesgo", "roche", (), "breast"),
    Brand("Polivy", "roche", ("polatuzumab",), "lymphoma"),
    Brand("Columvi", "roche", ("glofitamab",), "lymphoma"),
    Brand("Lunsumio", "roche", ("mosunetuzumab",), "lymphoma"),
    Brand("Itovebi", "roche", ("inavolisib",), "breast"),
    # ── Competitors the client tracks ──
    Brand("Keytruda", "MSD", ("pembrolizumab",), "lung and others"),
    Brand("Opdivo", "BMS", ("nivolumab",), "lung and others"),
    Brand("Yervoy", "BMS", ("ipilimumab",), "lung, melanoma"),
    Brand("Opdualag", "BMS", ("nivolumab relatlimab",), "melanoma"),
    Brand("Imfinzi", "AstraZeneca", ("durvalumab",), "lung"),
    Brand("Imjudo", "AstraZeneca", ("tremelimumab",), "liver, lung"),
    Brand("Tagrisso", "AstraZeneca", ("osimertinib",), "EGFR+ lung"),
    Brand("Enhertu", "AstraZeneca", ("trastuzumab deruxtecan", "T-DXd"), "breast, lung"),
    Brand("Libtayo", "Regeneron", ("cemiplimab",), "lung, skin"),
    Brand("Rybrevant", "Johnson & Johnson", ("amivantamab",), "EGFR+ lung"),
    Brand("Lumakras", "Amgen", ("sotorasib",), "KRAS G12C lung"),
    Brand("Krazati", "BMS", ("adagrasib",), "KRAS G12C lung"),
)

BRANDS_BY_NAME: dict[str, Brand] = {b.name.lower(): b for b in BRANDS}


def _pattern(brand: Brand) -> re.Pattern:
    """Word-boundary alternation over the trade name and every INN."""
    terms = [brand.name, *brand.inn]
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.IGNORECASE)


_PATTERNS: dict[str, re.Pattern] = {b.name: _pattern(b) for b in BRANDS}


def detect(text: str) -> list[str]:
    """Brand names mentioned in this text, deduplicated, in BRANDS order."""
    if not text:
        return []
    return [name for name, pattern in _PATTERNS.items() if pattern.search(text)]


@dataclass
class BrandStat:
    brand: Brand
    mentions: int = 0
    sentiment: dict[str, int] = field(default_factory=dict)
    engagement: int = 0
    sources: set[str] = field(default_factory=set)

    def as_row(self, total: int) -> dict:
        positive = self.sentiment.get("positive", 0)
        negative = self.sentiment.get("negative", 0)
        rated = positive + negative
        return {
            "brand": self.brand.name,
            "owner": self.brand.owner,
            "is_ours": self.brand.is_ours,
            "indication": self.brand.indication,
            "mentions": self.mentions,
            "share": round(100 * self.mentions / total) if total else 0,
            "sentiment": self.sentiment,
            # Net sentiment over the mentions that actually carry an opinion —
            # a brand discussed 40 times neutrally is not "0% positive", it is
            # simply unrated, and the two must not look the same.
            "net_sentiment": round(100 * (positive - negative) / rated) if rated else None,
            "rated_mentions": rated,
            "engagement": self.engagement,
            "sources": len(self.sources),
        }


def tally(items: list[dict]) -> dict:
    """Aggregate brand mentions across insights/posts.

    Each item is ``{text, sentiment, engagement, source}``. One item mentioning
    two brands counts once for each — that is share of *conversation*, not a
    partition, so shares are read against the mention total and can exceed 100%
    only if you sum them, which the UI does not do.
    """
    stats: dict[str, BrandStat] = {}
    for item in items:
        names = detect(item.get("text") or "")
        if not names:
            continue
        sentiment = (item.get("sentiment") or "").lower() or "neutral"
        for name in names:
            stat = stats.setdefault(name, BrandStat(brand=BRANDS_BY_NAME[name.lower()]))
            stat.mentions += 1
            stat.sentiment[sentiment] = stat.sentiment.get(sentiment, 0) + 1
            stat.engagement += int(item.get("engagement") or 0)
            if item.get("source"):
                stat.sources.add(item["source"])

    total = sum(s.mentions for s in stats.values())
    rows = sorted((s.as_row(total) for s in stats.values()),
                  key=lambda r: r["mentions"], reverse=True)
    ours = sum(r["mentions"] for r in rows if r["is_ours"])
    return {
        "total_mentions": total,
        "roche_mentions": ours,
        "competitor_mentions": total - ours,
        "roche_share": round(100 * ours / total) if total else 0,
        "brands": rows,
        "by_owner": _by_owner(rows),
    }


def _by_owner(rows: list[dict]) -> list[dict]:
    owners: dict[str, dict] = {}
    for row in rows:
        entry = owners.setdefault(row["owner"], {
            "owner": row["owner"], "is_ours": row["is_ours"],
            "mentions": 0, "brands": [],
        })
        entry["mentions"] += row["mentions"]
        entry["brands"].append(row["brand"])
    total = sum(o["mentions"] for o in owners.values())
    for entry in owners.values():
        entry["share"] = round(100 * entry["mentions"] / total) if total else 0
    return sorted(owners.values(), key=lambda o: o["mentions"], reverse=True)
