"""Downloadable synthesis reports — KOL, Competitor, and the combined view.

The client asked for three PDFs at the top of the dashboard, all in the same
shape: *Main Information*, *"So What"*, *Key Articles & Posts*. They also asked
for a deeper level of analysis with actionable recommendations, and for every
section to be traceable to the sources it came from.

One builder serves all three scopes rather than three near-identical ones — the
only thing that varies is which targets feed it and how the analyst is framed:

    kol           targets with target_type='kol'
    competitor    targets with target_type='competitor'
    comprehensive both, plus a section on what the comparison implies

Structure is emitted with section markers (``##MAIN##``) instead of JSON. A long
JSON reply that gets truncated fails to parse and yields nothing; a truncated
marker document simply loses its last section. Same convention as the dashboard
briefs — see services/synthesizer.py.

Every insight handed to the model is numbered, and the model cites those numbers.
Citations are resolved back to real rows afterwards, so a "source" in the report
is always a row that exists — the model is never asked to reproduce a URL.
"""
from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from app.config import get_settings
from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete

logger = structlog.get_logger(__name__)

# Reporting window — the client's standard is the last 30 days.
WINDOW_DAYS = 30

# Insights sent to the model. High enough to be representative, low enough that
# the prompt plus its reasoning stay inside the token budget.
MAX_INSIGHTS = 60

# gemini-2.5-flash is a thinking model: reasoning tokens come out of the same
# budget as the answer, so this is deliberately generous. A short cap here is
# what silently truncated other LLM calls in this codebase.
MAX_TOKENS = 8192

SCOPES = ("kol", "competitor", "comprehensive")

_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class ScopeSpec:
    """How one report scope is framed and which targets feed it."""

    key: str
    title: str
    subtitle: str
    target_types: tuple[str, ...]
    analyst: str
    focus: str


_SPECS: dict[str, ScopeSpec] = {
    "kol": ScopeSpec(
        key="kol",
        title="KOL Synthesis",
        subtitle="What French key opinion leaders said in the last 30 days",
        target_types=("kol",),
        analyst="the senior medical-affairs lead for Roche France",
        focus=(
            "what the KOLs are saying, where opinion is shifting, which of them is "
            "driving the conversation, and what medical affairs should do about it"
        ),
    ),
    "competitor": ScopeSpec(
        key="competitor",
        title="Competitor Synthesis",
        subtitle="Competitor messaging in the French market, last 30 days",
        target_types=("competitor",),
        analyst="the competitive-intelligence lead for Roche France",
        focus=(
            "what each competitor is claiming, launching or signalling in France, "
            "how their messaging is positioned against Roche, and what to counter"
        ),
    ),
    "comprehensive": ScopeSpec(
        key="comprehensive",
        title="Comprehensive Synthesis",
        subtitle="KOL and competitor intelligence combined, last 30 days",
        target_types=("kol", "competitor"),
        analyst="the pharma intelligence lead for Roche France",
        focus=(
            "how KOL opinion and competitor messaging line up or diverge, where a "
            "competitor is shaping what KOLs discuss, and what Roche should do next"
        ),
    ),
}


def spec_for(scope: str) -> ScopeSpec:
    if scope not in _SPECS:
        raise ValueError(f"unknown synthesis scope: {scope!r} (expected one of {SCOPES})")
    return _SPECS[scope]


# ── Data ──────────────────────────────────────────────────

async def _load_insights(session, spec: ScopeSpec, window_start: datetime) -> list[dict]:
    """Recent insights for this scope, newest first, flattened for prompting."""
    from sqlalchemy import desc, select

    from app.models import ExtractedInsight, ScrapedPost, Target
    # post_not_ae, not insight_not_ae: this query joins ScrapedPost, so the
    # column is directly available. insight_not_ae adds a correlated NOT EXISTS
    # for queries that do *not* join the post, and combining it with this join
    # auto-correlates the subquery away entirely. Same regulatory guarantee —
    # an insight extracted from an adverse-event post is never shown.
    from app.services.ae_filter import post_not_ae

    # Provenance lives on ScrapedPost, not on the insight — the insight carries
    # only what was said. The join is inner because a report must be able to link
    # every finding back to a source document.
    rows = await session.execute(
        select(ExtractedInsight, Target.name, Target.target_type, ScrapedPost)
        .join(Target, ExtractedInsight.target_id == Target.id)
        .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
        .where(ExtractedInsight.extracted_at >= window_start)
        .where(post_not_ae())
        .where(Target.target_type.in_(spec.target_types))
        .order_by(desc(ExtractedInsight.extracted_at))
        .limit(MAX_INSIGHTS)
    )
    out: list[dict] = []
    for insight, name, target_type, post in rows.all():
        out.append({
            "id": insight.id,
            "target": name,
            "target_type": target_type,
            "topic": insight.topic or "",
            "said": (insight.what_they_said or "").strip(),
            "sentiment": insight.sentiment or "neutral",
            "category": insight.category or "",
            "url": post.source_url or "",
            "source_name": post.source_name or post.domain or "",
            "source_scope": post.source_scope or "",
            "date": post.published_date or (
                insight.extracted_at.date().isoformat() if insight.extracted_at else ""
            ),
        })
    return out


def _numbered(insights: list[dict]) -> str:
    """One line per insight, numbered so the model can cite it as [n]."""
    lines = []
    for i, ins in enumerate(insights, 1):
        who = f"{ins['target']} ({ins['target_type']})"
        lines.append(
            f"[{i}] {who} | {ins['date']} | topic:{ins['topic']} | "
            f"sentiment:{ins['sentiment']} | \"{ins['said'][:240]}\""
        )
    return "\n".join(lines)


def _resolve_citations(text: str, insights: list[dict]) -> list[dict]:
    """Map the [n] markers in a section back to the insights they refer to.

    This is what makes "linked to its main sources" true rather than claimed: the
    model only ever emits an index, and the URL comes from the row.
    """
    seen: set[int] = set()
    sources: list[dict] = []
    for match in _CITATION_RE.finditer(text or ""):
        idx = int(match.group(1)) - 1
        if idx in seen or not (0 <= idx < len(insights)):
            continue
        seen.add(idx)
        ins = insights[idx]
        sources.append({
            "n": idx + 1,
            "target": ins["target"],
            "topic": ins["topic"],
            "url": ins["url"],
            "source_name": ins["source_name"],
            "date": ins["date"],
            "quote": ins["said"][:220],
        })
    return sources


# ── Prompt ────────────────────────────────────────────────

def build_prompt(spec: ScopeSpec, insights: list[dict]) -> str:
    return (
        f"You are {spec.analyst}. Write a synthesis for the leadership team covering "
        f"the last {WINDOW_DAYS} days.\n\n"
        f"Concentrate on {spec.focus}.\n\n"
        "Every numbered statement below is real, monitored content. Use ONLY this "
        "material — never invent a fact, a company, a trial or a number.\n\n"
        "CITATIONS ARE MANDATORY. After each claim, cite the statements it came "
        "from as [n] or [n][m], using the numbers below. A claim with no citation "
        "will be discarded.\n\n"
        "Be specific and useful. Name the person or company, the drug, the trial, "
        "the congress. Avoid sentences that would be true of any pharma market in "
        "any month — if a sentence could appear in last year's report unchanged, "
        "rewrite it.\n\n"
        "Output EXACTLY these sections, with these markers, and nothing else:\n\n"
        "##MAIN##\n"
        "5-8 lines, one finding per line starting '- '. The most important things "
        "that happened, each with its citation.\n\n"
        "##SO_WHAT##\n"
        "2-3 short paragraphs: what this means for Roche France strategically — the "
        "shift behind the findings, not a restatement of them. Cite as you go.\n\n"
        "##RECOMMENDATIONS##\n"
        "3-5 lines, one per line starting '- '. Each must be an action someone can "
        "own this month: what to do, and which finding drives it. Start with a verb.\n\n"
        "##WATCH##\n"
        "2-4 lines starting '- ': what to monitor next, and the signal that would "
        "mean it is happening.\n\n"
        "##KEY_POSTS##\n"
        "Up to 8 lines, each exactly '[n] one sentence on why this article or post "
        "matters'. Pick the highest-signal statements from the list.\n\n"
        f"MONITORED STATEMENTS ({len(insights)}):\n{_numbered(insights)}"
    )


def parse_report(raw: str, insights: list[dict]) -> dict:
    """Turn the marker document into the report structure, resolving citations."""
    main = extract_section(raw, "MAIN")
    so_what = extract_section(raw, "SO_WHAT")
    recommendations = extract_section(raw, "RECOMMENDATIONS")
    watch = extract_section(raw, "WATCH")
    key_posts = extract_section(raw, "KEY_POSTS")

    # Every referenced insight, across all sections, as the report's source list.
    cited_everywhere = _resolve_citations(
        " ".join([main, so_what, recommendations, watch, key_posts]), insights
    )

    key: list[dict] = []
    for line in (key_posts or "").splitlines():
        match = _CITATION_RE.search(line)
        if not match:
            continue
        idx = int(match.group(1)) - 1
        if not (0 <= idx < len(insights)):
            continue
        ins = insights[idx]
        key.append({
            **ins,
            "why": _CITATION_RE.sub("", line, count=1).strip(" -–—:").strip(),
        })

    return {
        "main": parse_bullets(main),
        "so_what": trim_incomplete(so_what),
        "recommendations": parse_bullets(recommendations),
        "watch": parse_bullets(watch),
        "key_posts": key,
        "sources": cited_everywhere,
    }


# ── Rendering ─────────────────────────────────────────────

def _esc(value: str) -> str:
    return _html.escape(value or "")


def _bullet_list(items: list[str], sources: list[dict]) -> str:
    """Render bullets, turning each [n] marker into a visible source reference."""
    if not items:
        return "<div class='empty-card'>No data in the last 30 days.</div>"
    by_index = {s["n"]: s for s in sources}
    rendered = []
    for item in items:
        cited = [int(m.group(1)) for m in _CITATION_RE.finditer(item)]
        text = _esc(_CITATION_RE.sub("", item).strip())
        refs = ""
        if cited:
            labels = []
            for n in cited:
                source = by_index.get(n)
                if source:
                    label = source["source_name"] or source["target"]
                    labels.append(f"[{n}] {_esc(label)}")
            if labels:
                refs = f"<div class='src'>{' · '.join(labels)}</div>"
        rendered.append(f"<li>{text}{refs}</li>")
    return f"<ul class='sum-list'>{''.join(rendered)}</ul>"


_EXTRA_CSS = """
.src { font-size: 9px; color: #1f4eaa; margin-top: 2px; }
.post-card { background: #f7f7fb; border: 1px solid #e0e0e8; border-left: 4px solid #1f4eaa;
             padding: 9px 12px; margin-bottom: 7px; border-radius: 3px; }
.post-card .who { font-size: 10px; font-weight: bold; color: #1f4eaa;
                  text-transform: uppercase; letter-spacing: .5px; }
.post-card .why { font-size: 12px; color: #222; line-height: 1.5; margin: 3px 0; }
.post-card .link { font-size: 9px; color: #666; word-break: break-all; }
.rec-card { background: #f2f8f4; border: 1px solid #cfe6d8; border-left: 4px solid #1f8a4c;
            padding: 10px 14px; margin-bottom: 10px; border-radius: 3px; }
.rec-card .label { font-size: 10px; font-weight: bold; color: #1f8a4c;
                   text-transform: uppercase; letter-spacing: .5px; margin-bottom: 5px; }
"""


def render_html(spec: ScopeSpec, report: dict, now: datetime, insight_count: int) -> str:
    from app.services.pdf_generator import _BASE_CSS

    today = now.date().isoformat()
    window_from = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    sources = report["sources"]

    posts_html = "".join(
        "<div class='post-card'>"
        f"<div class='who'>{_esc(p['target'])} · {_esc(p['source_name'] or p['topic'])}"
        f"{' · ' + _esc(p['date']) if p['date'] else ''}</div>"
        f"<div class='why'>{_esc(p['why'])}</div>"
        f"<div class='why'><em>&ldquo;{_esc(p['said'][:200])}&rdquo;</em></div>"
        + (f"<div class='link'>{_esc(p['url'])}</div>" if p["url"] else "")
        + "</div>"
        for p in report["key_posts"]
    ) or "<div class='empty-card'>No data in the last 30 days.</div>"

    so_what = _esc(report["so_what"]).replace("\n", "<br>")
    recs = report["recommendations"]
    recs_html = (
        "<div class='rec-card'><div class='label'>Actions this month</div>"
        + _bullet_list(recs, sources) + "</div>"
    ) if recs else "<div class='empty-card'>No recommendations generated.</div>"

    sources_html = "".join(
        f"<li>[{s['n']}] {_esc(s['target'])} — {_esc(s['source_name'] or s['topic'])}"
        + (f"<div class='link'>{_esc(s['url'])}</div>" if s["url"] else "")
        + "</li>"
        for s in sources
    )
    sources_block = (
        f"<ul class='sum-list'>{sources_html}</ul>" if sources_html
        else "<div class='empty-card'>No sources cited.</div>"
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>{_BASE_CSS}{_EXTRA_CSS}</style></head><body>
<div class="header">
  <h1>PharmaRadar {_esc(spec.title)}</h1>
  <div class="subtitle">{_esc(spec.subtitle)}</div>
  <div class="meta"><strong>Report date:</strong> {today} &nbsp;·&nbsp;
    <strong>Period:</strong> {window_from} to {today} &nbsp;·&nbsp;
    <strong>Statements analysed:</strong> {insight_count}</div>
</div>
<div class="section-title">Main information</div>
{_bullet_list(report["main"], sources)}
<div class="section-title">So what</div>
<div class="sowhat-card"><div class="body">{so_what or "<em>No analyst note.</em>"}</div></div>
<div class="section-title">Recommendations</div>
{recs_html}
<div class="section-title">What to watch</div>
{_bullet_list(report["watch"], sources)}
<div class="section-title">Key articles &amp; posts</div>
{posts_html}
<div class="section-title">Sources</div>
{sources_block}
<div class="footer">Generated by PharmaRadar &nbsp;·&nbsp; {today} &nbsp;·&nbsp; Confidential</div>
</body></html>"""


def render_pdf(spec: ScopeSpec, report: dict, now: datetime, insight_count: int) -> str | None:
    """Write the PDF and return a URL (Vercel Blob, or the local fallback route)."""
    from weasyprint import HTML

    from app.services.pdf_generator import _validate_pdf

    settings = get_settings()
    stamp = now.strftime("%Y-%m-%d_%H%M")
    out_dir = Path(settings.reports_dir) / "synthesis"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{spec.title.replace(' ', '_')}_{stamp}.pdf"
    pdf_path = out_dir / filename

    HTML(string=render_html(spec, report, now, insight_count)).write_pdf(str(pdf_path))
    _validate_pdf(pdf_path)

    if settings.vercel_blob_token:
        try:
            from app.services.vercel_blob_storage import upload_synthesis_pdf
            return upload_synthesis_pdf(
                pdf_path.read_bytes(), spec.key, stamp, settings.vercel_blob_token
            )
        except Exception as exc:
            logger.warning("synthesis.blob_upload_failed", scope=spec.key, error=str(exc)[:200])
    return f"/api/reports/local/synthesis/{filename}"


# ── Redis state ───────────────────────────────────────────
# The dashboard shows the last stored report without regenerating it, so both the
# status and the result are kept per scope.

STATUS_KEY = "synthesis:{scope}:status"
RESULT_KEY = "synthesis:{scope}:result"
_RESULT_TTL = 30 * 24 * 3600


def _redis():
    import redis as _redis_lib
    return _redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=2)


def set_status(scope: str, **fields) -> None:
    try:
        _redis().set(STATUS_KEY.format(scope=scope), json.dumps(fields), ex=86400)
    except Exception:
        pass


def get_state(scope: str) -> dict:
    status: dict = {"status": "idle"}
    result = None
    try:
        r = _redis()
        raw = r.get(STATUS_KEY.format(scope=scope))
        if raw:
            status = json.loads(raw)
        raw = r.get(RESULT_KEY.format(scope=scope))
        if raw:
            result = json.loads(raw)
    except Exception:
        pass
    return {"status": status.get("status", "idle"), "error": status.get("error"), "result": result}


def store_result(scope: str, result: dict) -> None:
    try:
        _redis().set(RESULT_KEY.format(scope=scope), json.dumps(result), ex=_RESULT_TTL)
    except Exception:
        pass


# ── Orchestration ─────────────────────────────────────────

def build(scope: str) -> dict:
    """Build one synthesis report end to end. Runs inside a Celery task."""
    import asyncio

    from app.services.llm_router import call_llm

    spec = spec_for(scope)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)

    async def _fetch() -> list[dict]:
        from app.database import CelerySessionLocal
        async with CelerySessionLocal() as session:
            return await _load_insights(session, spec, window_start)

    insights = asyncio.run(_fetch())
    if not insights:
        return {
            "scope": scope,
            "title": spec.title,
            "generated_at": now.isoformat(),
            "insight_count": 0,
            "pdf_url": None,
            "error": f"No {spec.key} insights in the last {WINDOW_DAYS} days — "
                     "add targets of this type and run a scrape.",
            **{k: v for k, v in parse_report("", []).items()},
        }

    raw = call_llm(
        [{"role": "user", "content": build_prompt(spec, insights)}],
        temperature=0.3,
        max_tokens=MAX_TOKENS,
    )
    report = parse_report(raw, insights)

    pdf_url = None
    pdf_error = None
    try:
        pdf_url = render_pdf(spec, report, now, len(insights))
    except Exception as exc:
        pdf_error = str(exc)[:200]
        logger.warning("synthesis.pdf_failed", scope=scope, error=pdf_error)

    return {
        "scope": scope,
        "title": spec.title,
        "generated_at": now.isoformat(),
        "insight_count": len(insights),
        "pdf_url": pdf_url,
        "error": pdf_error,
        **report,
    }
