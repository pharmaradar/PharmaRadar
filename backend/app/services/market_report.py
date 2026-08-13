"""Market-research reports — the shared generator behind Topic Explorer and Burning Topics.

The client asked both surfaces for the same deliverable: ask a question, get a
3-5 page report with exactly these sections.

    1. Executive Summary
    2. The "So What" (strategic implications)
    3. What is being said
    4. Voice distribution — KOLs / Doctors / Patients / Others
    5. Volume of mentions
    6. Key sub-topics to consider

Two of those are *computed*, not written by the model. Voice distribution and
volume come from counting rows (see services/voice_profile), because a model
asked to estimate "how many patients are discussing this" will produce a
confident number with nothing behind it. The model is given the real figures and
asked to interpret them.

Everything else follows the citation discipline already used by
services/synthesis_report: material is numbered, the model cites `[n]`, and the
URL is resolved from the row afterwards — so a source in a report is always a
document that exists.

Material is gathered from all three stores, because a question about "what
doctors think" is answered by different rows in each:

    ExtractedInsight  what tracked KOLs and competitors actually said
    SocialPost        the wider conversation, with engagement
    DiscoveryResult   web and press articles already fetched for this query
"""
from __future__ import annotations

import asyncio
import html as _html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from app.config import get_settings
from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete
from app.services.voice_profile import (
    BUCKET_LABELS,
    EXACT,
    VoiceBreakdown,
    build_breakdown,
)

logger = structlog.get_logger(__name__)

DEFAULT_WINDOW_DAYS = 30

# Per-source caps. The prompt has to stay inside the token budget while still
# covering enough ground for a multi-page report.
MAX_INSIGHTS = 40
MAX_SOCIAL = 40
MAX_WEB = 25

# Below this, a precise search is judged too thin and the broader terms are
# tried as well — better a slightly noisy report than an empty one.
_MIN_MATERIAL = 12

# gemini-2.5-flash is a thinking model — reasoning shares this budget. A short
# cap here is what silently truncated other LLM calls in this codebase.
MAX_TOKENS = 8192

_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass
class Material:
    """Everything gathered for one question, already numbered for citation."""

    items: list[dict] = field(default_factory=list)
    voices: VoiceBreakdown = field(default_factory=VoiceBreakdown)
    volume: dict = field(default_factory=dict)
    authors: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    def numbered(self) -> str:
        lines = []
        for i, item in enumerate(self.items, 1):
            who = item.get("author") or item.get("source_name") or "unattributed"
            lines.append(
                f"[{i}] ({item['kind']}) {who} | {item.get('date') or 'undated'} | "
                f"\"{(item.get('text') or '')[:260]}\""
            )
        return "\n".join(lines)


# ── Live research ─────────────────────────────────────────

async def research(session, question: str, queries: list[str],
                   language: str | None = "fr", max_queries: int = 5) -> int:
    """Search the web for this question and persist what comes back.

    Without this the report can only describe what a previous scrape happened to
    collect, so a genuinely new question ("Was the ATOMIC study discussed at ASCO
    2026?") returns nothing — which is exactly the "no significant difference"
    the client reported. Searching makes the tab dynamic.

    TinyFish search is unmetered (only agent runs bill), so this costs wall-clock
    and nothing else. Rows are written as DiscoveryResult, which is where
    _gather_web already looks, and are deduplicated on content hash so asking the
    same question twice does not duplicate the corpus.
    """
    from app.routers.discovery import _save_hit
    from app.services.scraper import _tf_search_discovery

    scope = "fr" if (language or "fr") == "fr" else "global"
    loop = asyncio.get_running_loop()
    seen: set[str] = set()
    saved = 0

    for query in queries[:max_queries]:
        try:
            hits = await loop.run_in_executor(
                None, lambda q=query: _tf_search_discovery(q, scope=scope)
            )
        except Exception as exc:                       # noqa: BLE001
            logger.warning("market_report.search_failed", q=query[:70], error=str(exc)[:120])
            continue
        for hit in hits or []:
            try:
                # Stored under the question so _gather_web's ILIKE finds it.
                # France-only when the report is scoped to France, so the
                # corpus a report is written from never contains a source the
                # scope excludes.
                if await _save_hit(session, question, hit, seen,
                                   fr_only=(scope == "fr")):
                    saved += 1
            except Exception:                          # noqa: BLE001
                continue
    try:
        await session.commit()
    except Exception:                                  # noqa: BLE001
        await session.rollback()
    logger.info("market_report.research", question=question[:70], queries=len(queries[:max_queries]),
                saved=saved)
    return saved


# ── Gathering ─────────────────────────────────────────────

async def _gather_insights(session, terms: list[str], since: datetime) -> list[dict]:
    """What tracked KOLs and competitors said. Attribution here is a foreign key."""
    from sqlalchemy import desc, or_, select

    from app.models import ExtractedInsight, ScrapedPost, Target
    # This query joins ScrapedPost, so the AE filter must be the column form.
    # insight_not_ae() adds a correlated NOT EXISTS that SQLAlchemy collapses to
    # nothing once the post is already in the FROM clause.
    from app.services.ae_filter import post_not_ae

    clauses = []
    for term in terms:
        like = f"%{term.lower()}%"
        clauses += [
            ExtractedInsight.topic.ilike(like),
            ExtractedInsight.what_they_said.ilike(like),
            ExtractedInsight.context.ilike(like),
        ]
    if not clauses:
        return []

    rows = await session.execute(
        select(ExtractedInsight, Target.name, Target.target_type, ScrapedPost)
        .join(Target, ExtractedInsight.target_id == Target.id)
        .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
        .where(or_(*clauses))
        .where(ExtractedInsight.extracted_at >= since)
        .where(post_not_ae())
        .order_by(desc(ExtractedInsight.extracted_at))
        .limit(MAX_INSIGHTS)
    )
    out = []
    for insight, name, target_type, post in rows.all():
        out.append({
            "kind": "KOL statement" if target_type == "kol" else "competitor statement",
            "author": name,
            "target_type": target_type,
            "is_tracked_kol": target_type == "kol",
            "text": (insight.what_they_said or "").strip(),
            "topic": insight.topic or "",
            "sentiment": insight.sentiment or "neutral",
            "url": post.source_url or "",
            "source_name": post.source_name or post.domain or "",
            "date": post.published_date or (
                insight.extracted_at.date().isoformat() if insight.extracted_at else ""),
            "engagement": 0,
            "at": insight.extracted_at,
        })
    return out


async def _gather_social(session, terms: list[str], since: datetime,
                         language: str | None) -> list[dict]:
    """The wider conversation. Author quality is mixed — see voice_profile."""
    from sqlalchemy import desc, or_, select

    from app.models import SocialPost
    from app.services.ae_filter import social_not_ae

    clauses = []
    for term in terms:
        like = f"%{term.lower()}%"
        clauses += [
            SocialPost.text.ilike(like),
            SocialPost.topic.ilike(like),
            SocialPost.query.ilike(like),
            SocialPost.hashtags.ilike(like),
        ]
    if not clauses:
        return []

    query = (
        select(SocialPost)
        .where(or_(*clauses))
        .where(SocialPost.scraped_at >= since)
        .where(social_not_ae())
    )
    if language and language != "all":
        query = query.where(SocialPost.language == language)

    rows = await session.execute(
        query.order_by(desc(SocialPost.likes + SocialPost.comments * 2)).limit(MAX_SOCIAL)
    )
    out = []
    for post in rows.scalars().all():
        out.append({
            "kind": f"{post.platform} post",
            "author": post.author or "",
            "target_type": None,
            "is_tracked_kol": False,
            "text": (post.text or "").strip(),
            "topic": post.topic or "",
            "sentiment": "",
            "url": post.post_url or "",
            "source_name": post.domain or post.platform or "",
            "date": post.posted_at.date().isoformat() if post.posted_at else "",
            "engagement": (post.likes or 0) + 2 * (post.comments or 0) + (post.shares or 0),
            "at": post.posted_at or post.scraped_at,
        })
    return out


async def _gather_web(session, terms: list[str], language: str | None) -> list[dict]:
    """Web and press articles already fetched for this query — no new scraping."""
    from sqlalchemy import desc, or_, select

    from app.models.discovery_result import DiscoveryResult

    clauses = []
    for term in terms:
        like = f"%{term.lower()}%"
        clauses += [
            DiscoveryResult.query.ilike(like),
            DiscoveryResult.title.ilike(like),
            DiscoveryResult.snippet.ilike(like),
        ]
    if not clauses:
        return []

    query = select(DiscoveryResult).where(or_(*clauses))
    if language and language != "all":
        query = query.where(DiscoveryResult.language == language)

    rows = await session.execute(
        query.order_by(desc(DiscoveryResult.scraped_at)).limit(MAX_WEB)
    )
    out = []
    for row in rows.scalars().all():
        out.append({
            "kind": "web article",
            "author": row.source_name or "",
            "target_type": None,
            "is_tracked_kol": False,
            "text": (row.snippet or row.title or "").strip(),
            "topic": row.query or "",
            "sentiment": "",
            "url": row.url or "",
            "source_name": row.source_name or row.domain or "",
            "date": row.published_date or (
                row.scraped_at.date().isoformat() if row.scraped_at else ""),
            "engagement": 0,
            "at": row.scraped_at,
        })
    return out


def compute_volume(items: list[dict], window_days: int) -> dict:
    """Mention volume, stated honestly about how much of it is dated.

    Only 41% of social posts carry a `posted_at` (the TinyFish search path cannot
    supply one), so a bare time series would silently describe a minority of the
    data. The dated subset is reported alongside its coverage, and the caller is
    expected to show that caveat rather than hide it.
    """
    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1

    dated = [i for i in items if i.get("at")]
    per_week: dict[str, int] = {}
    for item in dated:
        stamp = item["at"]
        monday = (stamp - timedelta(days=stamp.weekday())).date().isoformat()
        per_week[monday] = per_week.get(monday, 0) + 1

    engagement = sum(i.get("engagement") or 0 for i in items)
    return {
        "total": len(items),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: kv[1], reverse=True)),
        "dated": len(dated),
        "date_coverage": round(100 * len(dated) / len(items)) if items else 0,
        "per_week": dict(sorted(per_week.items())),
        "total_engagement": engagement,
        "window_days": window_days,
    }


async def _tracked_names(session) -> set[str]:
    """Every name already in the client's audience, folded for comparison.

    Both tables count. `is_tracked_kol` alone is not enough: it is set from
    `target_type == "kol"`, so a tracked COMPETITOR (AstraZeneca France) and a
    tracked social account (@GustaveRoussy) would both be badged "not tracked" —
    the exact opposite of what the badge means.
    """
    from sqlalchemy import select

    from app.models import Target, TrackedAccount
    from app.services.term_expansion import fold_accents

    names: set[str] = set()
    for (name,) in (await session.execute(select(Target.name))).all():
        if name:
            names.add(fold_accents(name).strip().lower())
    for (handle,) in (await session.execute(select(TrackedAccount.handle))).all():
        if handle:
            names.add(fold_accents(handle).strip().lower().lstrip("@"))
    return names


def _compute_main_authors(items: list[dict], tracked_names: set[str] | None = None,
                          limit: int = 10) -> list[dict]:
    """Who is actually speaking on this question, loudest first.

    Replaces the standalone "emerging voices" side panel. The panel showed the
    same information next to the report instead of inside it, so it was read as
    decoration; the spec asks for the main speakers — and specifically for the
    ones "outside our current audience" — as part of the answer.

    `tracked` is what makes this actionable: an untracked author appearing near
    the top is a candidate KOL nobody is following yet.
    """
    stats: dict[str, dict] = {}
    for item in items:
        author = (item.get("author") or "").strip()
        if not author or author.lower() in ("unattributed", "unknown"):
            continue
        from app.services.term_expansion import fold_accents
        folded = fold_accents(author).strip().lower().lstrip("@")
        known = (bool(item.get("is_tracked_kol"))
                 or item.get("target_type") is not None
                 or folded in (tracked_names or set()))
        entry = stats.setdefault(author.lower(), {
            "author": author,
            "mentions": 0,
            "engagement": 0,
            "platforms": set(),
            "tracked": known,
            "target_type": item.get("target_type"),
        })
        entry["mentions"] += 1
        entry["engagement"] += int(item.get("engagement") or 0)
        entry["platforms"].add(item.get("platform") or item.get("kind") or "web")
        # Tracked status is a property of the author, not of one row.
        entry["tracked"] = entry["tracked"] or known

    ranked = sorted(stats.values(),
                    key=lambda e: (-e["mentions"], -e["engagement"], e["author"]))[:limit]
    for entry in ranked:
        entry["platforms"] = sorted(entry["platforms"])
    return ranked


async def gather(session, question: str, *, terms: list[str] | None = None,
                 window_days: int = DEFAULT_WINDOW_DAYS,
                 language: str | None = "fr") -> Material:
    """Collect and score everything relevant to one question."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    # A typed question never appears verbatim in stored text, so matching the
    # whole sentence with a LIKE finds nothing. Reduce it to content terms.
    if terms is None:
        from app.services.question import expand
        terms = expand(question, language=language)["terms"]
    search_terms = [t for t in (terms or [question]) if t and t.strip()]
    # The corpus is French; questions are usually typed in English. Without this
    # a question about "subcutaneous administration" matched 0 rows while the
    # French wording sat in the database. Expansion only adds spellings.
    from app.services.term_expansion import expand_terms as _bilingual
    search_terms = _bilingual(search_terms)

    # Search the discriminating terms first. Corpus-wide staples ("study",
    # "cancer", "patients") match almost every row, so including them up front
    # fills the per-source caps with material about something else entirely —
    # a question about the ATOMIC trial retrieved 104 unrelated items that way.
    # They are only added back if the precise search came up thin.
    from app.services.question import split_by_specificity
    specific, fallback = split_by_specificity(search_terms)
    tiers = [specific or search_terms]
    if fallback and specific:
        tiers.append(search_terms)

    insights: list[dict] = []
    social: list[dict] = []
    web: list[dict] = []
    for tier in tiers:
        insights = await _gather_insights(session, tier, since)
        social = await _gather_social(session, tier, since, language)
        web = await _gather_web(session, tier, language)
        if len(insights) + len(social) + len(web) >= _MIN_MATERIAL:
            break

    # KOL statements first: they are the highest-trust material and should be
    # the low citation numbers the model reaches for.
    items = insights + social + web

    voices = build_breakdown([
        {
            "author": i.get("author"),
            "url": i.get("url"),
            "is_tracked_kol": i.get("is_tracked_kol"),
            "target_type": i.get("target_type"),
        }
        for i in items
    ])
    return Material(items=items, voices=voices,
                    volume=compute_volume(items, window_days),
                    authors=_compute_main_authors(items, await _tracked_names(session)))


# ── Prompt ────────────────────────────────────────────────

def _voice_summary(voices: VoiceBreakdown) -> str:
    if not voices.total:
        return "No attributable voices."
    rows = ", ".join(f"{r['label']}: {r['mentions']} ({r['percent']}%)" for r in voices.as_rows())
    return f"{rows}. {round(voices.exact_share * 100)}% of these are identified from tracked records; the rest are inferred from the author name."


def _volume_summary(volume: dict) -> str:
    kinds = ", ".join(f"{k}: {v}" for k, v in volume.get("by_kind", {}).items())
    weeks = volume.get("per_week") or {}
    trend = ", ".join(f"{week}: {count}" for week, count in list(weeks.items())[-8:])
    return (
        f"{volume.get('total', 0)} mentions over {volume.get('window_days')} days "
        f"({kinds}). {volume.get('dated', 0)} carry a usable date "
        f"({volume.get('date_coverage', 0)}% coverage); weekly counts for that subset: "
        f"{trend or 'none'}. Total engagement across social: {volume.get('total_engagement', 0)}."
    )


def build_prompt(question: str, material: Material) -> str:
    return (
        "You are a senior pharma market-research analyst writing for Roche France.\n\n"
        f'QUESTION TO ANSWER:\n"{question}"\n\n'
        "Write a market-research report answering that question from the material below. "
        "Aim for the depth of a 3-5 page document: full paragraphs, not bullet fragments, "
        "wherever the section calls for prose.\n\n"
        "Use ONLY this material. Never invent a study, a company, a number or a person.\n\n"
        "CITATIONS ARE MANDATORY. Cite the numbered items after each claim as [n] or "
        "[n][m]. An uncited claim will be discarded.\n\n"
        "Two sections are already computed for you from the underlying records — do not "
        "recount or contradict them, interpret them:\n"
        f"  VOICE DISTRIBUTION: {_voice_summary(material.voices)}\n"
        f"  VOLUME: {_volume_summary(material.volume)}\n\n"
        "Be specific. Name the drug, the trial, the congress, the institution. A sentence "
        "that would be true of any market in any month is not worth writing. Where the "
        "material is thin, say so plainly instead of padding.\n\n"
        "Output EXACTLY these sections, with these markers, and nothing else:\n\n"
        "##EXEC_SUMMARY##\n"
        "3-4 paragraphs answering the question directly, for a reader who will read "
        "nothing else.\n\n"
        "##SO_WHAT##\n"
        "2-3 paragraphs on the strategic implication for Roche France — what follows from "
        "these findings, not a restatement of them.\n\n"
        "##WHAT_IS_SAID##\n"
        "4-6 paragraphs on the substance of the conversation: the positions taken, where "
        "they agree and diverge, the arguments and evidence used, and the tone.\n\n"
        "##VOICES##\n"
        "2-3 paragraphs interpreting the voice distribution above: who is driving this "
        "conversation, who is absent, and what that imbalance means.\n\n"
        "##VOLUME##\n"
        "1-2 paragraphs interpreting the volume figures above, including the direction of "
        "travel and any caveat about date coverage.\n\n"
        "##SUBTOPICS##\n"
        "4-6 lines starting '- ': the sub-topics worth tracking next, each with why.\n\n"
        "##KEY_POSTS##\n"
        "Up to 8 lines, each exactly '[n] one sentence on why this item matters'.\n\n"
        f"MATERIAL ({material.total} items):\n{material.numbered()}"
    )


def _resolve_citations(text: str, items: list[dict]) -> list[dict]:
    seen: set[int] = set()
    out: list[dict] = []
    for match in _CITATION_RE.finditer(text or ""):
        idx = int(match.group(1)) - 1
        if idx in seen or not (0 <= idx < len(items)):
            continue
        seen.add(idx)
        item = items[idx]
        out.append({
            "n": idx + 1,
            "kind": item["kind"],
            "author": item.get("author") or item.get("source_name") or "",
            "url": item.get("url") or "",
            "source_name": item.get("source_name") or "",
            "date": item.get("date") or "",
            "quote": (item.get("text") or "")[:220],
        })
    return out


def parse_report(raw: str, material: Material) -> dict:
    sections = {
        name.lower(): extract_section(raw, name)
        for name in ("EXEC_SUMMARY", "SO_WHAT", "WHAT_IS_SAID", "VOICES",
                     "VOLUME", "SUBTOPICS", "KEY_POSTS")
    }

    key_posts: list[dict] = []
    for line in (sections["key_posts"] or "").splitlines():
        match = _CITATION_RE.search(line)
        if not match:
            continue
        idx = int(match.group(1)) - 1
        if not (0 <= idx < len(material.items)):
            continue
        item = material.items[idx]
        key_posts.append({
            **{k: item.get(k) for k in
               ("kind", "author", "url", "source_name", "date", "text", "engagement")},
            "why": _CITATION_RE.sub("", line, count=1).strip(" -–—:").strip(),
        })

    return {
        "exec_summary": trim_incomplete(sections["exec_summary"]),
        "so_what": trim_incomplete(sections["so_what"]),
        "what_is_said": trim_incomplete(sections["what_is_said"]),
        "voices_note": trim_incomplete(sections["voices"]),
        "volume_note": trim_incomplete(sections["volume"]),
        "subtopics": parse_bullets(sections["subtopics"]),
        "key_posts": key_posts,
        "main_authors": material.authors,
        "voice_rows": material.voices.as_rows(),
        "voice_exact_share": round(material.voices.exact_share * 100),
        "volume": material.volume,
        "sources": _resolve_citations(" ".join(sections.values()), material.items),
        "item_count": material.total,
    }


# ── Rendering ─────────────────────────────────────────────

def _esc(value) -> str:
    return _html.escape(str(value or ""))


def _paragraphs(text: str) -> str:
    blocks = [p.strip() for p in (text or "").split("\n") if p.strip()]
    if not blocks:
        return "<div class='empty-card'>Not enough material for this section.</div>"
    return "".join(f"<p class='body'>{_esc(p)}</p>" for p in blocks)


_EXTRA_CSS = """
.body { font-size: 12px; line-height: 1.65; color: #222; margin: 0 0 9px 0; }
.voice-table { border-collapse: collapse; width: 100%; margin-bottom: 6px; }
.voice-table td { font-size: 11px; padding: 3px 6px; }
.voice-bar { background: #1f4eaa; height: 12px; border-radius: 2px; min-width: 3px; }
.caveat { font-size: 10px; color: #8a6d3b; background: #fcf8e3; border: 1px solid #f3e2b8;
          border-radius: 3px; padding: 6px 9px; margin-bottom: 10px; }
.src { font-size: 9px; color: #1f4eaa; margin-top: 2px; }
.post-card { background: #f7f7fb; border: 1px solid #e0e0e8; border-left: 4px solid #1f4eaa;
             padding: 9px 12px; margin-bottom: 7px; border-radius: 3px; }
.post-card .who { font-size: 10px; font-weight: bold; color: #1f4eaa; text-transform: uppercase; }
.post-card .link { font-size: 9px; color: #666; word-break: break-all; }
"""


def _voice_table(rows: list[dict], exact_share: int) -> str:
    if not rows:
        return "<div class='empty-card'>No attributable voices in this material.</div>"
    body = "".join(
        f"<tr><td style='width:34%'>{_esc(r['label'])}</td>"
        f"<td style='width:46%'><div class='voice-bar' style='width:{max(r['percent'],2)}%'></div></td>"
        f"<td style='width:20%'><b>{r['mentions']}</b> ({r['percent']}%)</td></tr>"
        for r in rows
    )
    caveat = (
        f"<div class='caveat'>{exact_share}% of these voices are identified from tracked "
        "records (KOL targets and curated sources). The remainder is inferred from the "
        "author name and should be read as indicative.</div>"
    )
    return f"<table class='voice-table'>{body}</table>{caveat}"


def _volume_block(volume: dict) -> str:
    kinds = "".join(
        f"<tr><td style='width:60%'>{_esc(k)}</td><td><b>{v}</b></td></tr>"
        for k, v in (volume.get("by_kind") or {}).items()
    )
    coverage = volume.get("date_coverage", 0)
    caveat = ""
    if coverage < 100:
        caveat = (
            f"<div class='caveat'>{volume.get('dated', 0)} of {volume.get('total', 0)} "
            f"mentions carry a usable publication date ({coverage}%). The weekly trend "
            "below describes only that subset — search-sourced posts often arrive "
            "without a date.</div>"
        )
    weeks = volume.get("per_week") or {}
    trend = "".join(
        f"<tr><td style='width:60%'>week of {_esc(w)}</td><td><b>{c}</b></td></tr>"
        for w, c in list(weeks.items())[-8:]
    )
    return (
        f"<table class='voice-table'>{kinds}</table>{caveat}"
        + (f"<table class='voice-table'>{trend}</table>" if trend else "")
    )


def render_html(question: str, report: dict, now: datetime, window_days: int) -> str:
    from app.services.pdf_generator import _BASE_CSS

    today = now.date().isoformat()
    since = (now - timedelta(days=window_days)).date().isoformat()

    posts = "".join(
        "<div class='post-card'>"
        f"<div class='who'>{_esc(p.get('author') or p.get('source_name'))} · {_esc(p.get('kind'))}"
        f"{' · ' + _esc(p.get('date')) if p.get('date') else ''}</div>"
        f"<p class='body'>{_esc(p.get('why'))}</p>"
        f"<p class='body'><em>&ldquo;{_esc((p.get('text') or '')[:200])}&rdquo;</em></p>"
        + (f"<div class='link'>{_esc(p.get('url'))}</div>" if p.get("url") else "")
        + "</div>"
        for p in report["key_posts"]
    ) or "<div class='empty-card'>No individual items stood out.</div>"

    subtopics = "".join(f"<li>{_esc(s)}</li>" for s in report["subtopics"])
    subtopics = (f"<ul class='sum-list'>{subtopics}</ul>" if subtopics
                 else "<div class='empty-card'>None identified.</div>")

    sources = "".join(
        f"<li>[{s['n']}] {_esc(s['author'] or s['source_name'])} — {_esc(s['kind'])}"
        + (f"<div class='link'>{_esc(s['url'])}</div>" if s["url"] else "")
        + "</li>"
        for s in report["sources"]
    )
    sources = (f"<ul class='sum-list'>{sources}</ul>" if sources
               else "<div class='empty-card'>No sources cited.</div>")

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>{_BASE_CSS}{_EXTRA_CSS}</style></head><body>
<div class="header">
  <h1>Market Research Report</h1>
  <div class="subtitle">{_esc(question)}</div>
  <div class="meta"><strong>Report date:</strong> {today} &nbsp;·&nbsp;
    <strong>Period:</strong> {since} to {today} &nbsp;·&nbsp;
    <strong>Items analysed:</strong> {report['item_count']}</div>
</div>
<div class="section-title">1. Executive summary</div>{_paragraphs(report['exec_summary'])}
<div class="section-title">2. So what — strategic implications</div>
<div class="sowhat-card">{_paragraphs(report['so_what'])}</div>
<div class="section-title">3. What is being said</div>{_paragraphs(report['what_is_said'])}
<div class="section-title">4. Voice distribution</div>
{_voice_table(report['voice_rows'], report['voice_exact_share'])}
{_paragraphs(report['voices_note'])}
<div class="section-title">5. Volume of mentions</div>
{_volume_block(report['volume'])}
{_paragraphs(report['volume_note'])}
<div class="section-title">6. Key sub-topics to consider</div>{subtopics}
<div class="section-title">7. Main voices on this question</div>
{_authors_table(report.get('main_authors') or [])}
<div class="section-title">Key articles &amp; posts</div>{posts}
<div class="section-title">Sources</div>{sources}
<div class="footer">Generated by PharmaRadar &nbsp;·&nbsp; {today} &nbsp;·&nbsp; Confidential</div>
</body></html>"""


def _authors_table(authors: list[dict]) -> str:
    """Main speakers, with the untracked ones called out.

    An untracked author high in this list is the actionable part: someone
    shaping the conversation who is not yet in the target list.
    """
    if not authors:
        return "<p class='muted'>No attributable authors in this window.</p>"
    rows = []
    for entry in authors:
        badge = ("" if entry.get("tracked")
                 else " <span class='tag'>not tracked</span>")
        platforms = ", ".join(entry.get("platforms") or []) or "—"
        engagement = entry.get("engagement") or 0
        rows.append(
            f"<tr><td>{_html.escape(str(entry['author']))}{badge}</td>"
            f"<td class='num'>{entry.get('mentions', 0)}</td>"
            f"<td class='num'>{engagement if engagement else '—'}</td>"
            f"<td>{_html.escape(platforms)}</td></tr>"
        )
    return ("<table class='voices'><thead><tr><th>Author</th><th>Mentions</th>"
            "<th>Engagement</th><th>Where</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def render_pdf(question: str, report: dict, now: datetime, window_days: int,
               slug: str) -> str | None:
    from weasyprint import HTML

    from app.services.pdf_generator import _validate_pdf

    settings = get_settings()
    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_dir = Path(settings.reports_dir) / "market_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"Market_Research_{slug}_{stamp}.pdf"
    pdf_path = out_dir / filename

    HTML(string=render_html(question, report, now, window_days)).write_pdf(str(pdf_path))
    _validate_pdf(pdf_path)

    if settings.vercel_blob_token:
        try:
            from app.services.vercel_blob_storage import upload_market_report_pdf
            return upload_market_report_pdf(
                pdf_path.read_bytes(), slug, stamp, settings.vercel_blob_token
            )
        except Exception as exc:
            logger.warning("market_report.blob_upload_failed", error=str(exc)[:200])
    return f"/api/reports/local/market_research/{filename}"


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug[:limit].rstrip("-") or "query")


# ── Orchestration ─────────────────────────────────────────

def build(question: str, *, terms: list[str] | None = None,
          window_days: int = DEFAULT_WINDOW_DAYS,
          language: str | None = "fr",
          live_research: bool = True) -> dict:
    """Build one market-research report end to end. Runs inside a Celery task.

    `live_research` is what makes the tab dynamic: the question is searched
    before anything is gathered, so a topic nobody has scraped before still
    produces a report. Search is unmetered, so this costs wall-clock only. Pass
    False for a stored topic that already has its own collection pipeline.
    """
    import asyncio

    from app.services.llm_router import call_llm
    from app.services.question import expand

    now = datetime.now(timezone.utc)
    plan = expand(question, language=language) if terms is None else {
        "terms": terms, "queries": [question]}

    async def _fetch() -> tuple[Material, int]:
        from app.database import CelerySessionLocal
        async with CelerySessionLocal() as session:
            found = 0
            if live_research:
                # Best-effort: a search failure must not lose the stored material.
                try:
                    found = await research(session, question, plan["queries"],
                                           language=language)
                except Exception as exc:                    # noqa: BLE001
                    logger.warning("market_report.research_skipped", error=str(exc)[:160])
            return (await gather(session, question, terms=plan["terms"],
                                 window_days=window_days, language=language), found)

    material, researched = asyncio.run(_fetch())

    base = {
        "question": question,
        "researched": researched,
        "search_terms": plan["terms"],
        "generated_at": now.isoformat(),
        "window_days": window_days,
        "language": language,
        "item_count": material.total,
        "pdf_url": None,
    }

    if not material.items:
        empty = parse_report("", material)
        return {
            **base, **empty,
            "error": (
                f"No material found for this question in the last {window_days} days. "
                "Run a search or a social scan first, or widen the period."
            ),
        }

    raw = call_llm(
        [{"role": "user", "content": build_prompt(question, material)}],
        temperature=0.3,
        max_tokens=MAX_TOKENS,
    )
    report = parse_report(raw, material)

    pdf_url, pdf_error = None, None
    try:
        pdf_url = render_pdf(question, report, now, window_days, slugify(question))
    except Exception as exc:
        pdf_error = str(exc)[:200]
        logger.warning("market_report.pdf_failed", error=pdf_error)

    return {**base, **report, "pdf_url": pdf_url, "error": pdf_error}
