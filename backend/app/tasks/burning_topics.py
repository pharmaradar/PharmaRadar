"""Burning Topics report task — runs on the 'scrape' queue.

Routed to worker-scrape because the TinyFish step spawns a headless browser
(~300-500MB) and that worker has the 2GB ceiling; the LLM + WeasyPrint parts
run fine anywhere (same codebase on every worker).

Pipeline per report row:
  1) query already-scraped posts (scraped_posts + social_posts) matching the
     topic terms within period_days, applying language filter + exclusion words
  2) ONE TinyFish discovery search for fresh web context (best-effort — a
     credit-less TinyFish account degrades to DB-only, never fails the report)
  3) llm_router synthesis → summary / key findings / so what / important posts,
     using the section-marker format (resilient to truncation)
  4) PDF via WeasyPrint (+ Vercel Blob upload when the token is configured)

acks_late is on, so the task must tolerate re-delivery: 'done' rows return
immediately, every phase writes to the same report row, and between phases we
re-check the row still exists (topic deleted mid-run → abort).
"""
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_MAX_KOL_POSTS = 60
_MAX_SOCIAL_POSTS = 40
_MAX_WEB_RESULTS = 8
_TEXT_SNIPPET = 400          # chars of post text shown to the LLM per candidate


# ── DB helpers (each call = its own event loop, standard Celery pattern) ─────

async def _load(report_id: int):
    from app.database import CelerySessionLocal
    from app.models import BurningTopic, BurningTopicReport
    async with CelerySessionLocal() as sess:
        report = await sess.get(BurningTopicReport, report_id)
        if not report:
            return None, None
        topic = await sess.get(BurningTopic, report.topic_id)
        return report, topic


async def _set_fields(report_id: int, **fields) -> bool:
    from app.database import CelerySessionLocal
    from app.models import BurningTopicReport
    async with CelerySessionLocal() as sess:
        report = await sess.get(BurningTopicReport, report_id)
        if not report:
            return False
        for k, v in fields.items():
            setattr(report, k, v)
        await sess.commit()
        return True


def _aborted(report_id: int) -> bool:
    """Stop-flag check between phases: abort when the report row disappeared
    (topic deleted cascades reports) or was flipped to 'failed' externally."""
    async def _check():
        report, _ = await _load(report_id)
        return report is None or report.status == "failed"
    try:
        return asyncio.run(_check())
    except Exception:
        return False


# ── Phase 1: already-scraped data ─────────────────────────

def _topic_terms(topic) -> tuple[list[str], list[str]]:
    def _list(raw):
        try:
            return [s.strip() for s in json.loads(raw or "[]") if isinstance(s, str) and s.strip()]
        except (ValueError, TypeError):
            return []
    terms = [topic.name.strip()] + _list(topic.restriction_terms)
    return terms, _list(topic.exclusion_words)


async def _gather_db_posts(topic) -> list[dict]:
    """KOL scraped_posts + social_posts matching any topic term in the window,
    minus posts containing an exclusion word. Language filter applies to
    social_posts (scraped_posts has no language column) and is repeated to the
    LLM as an instruction."""
    from sqlalchemy import desc, func, or_, select
    from app.database import CelerySessionLocal
    from app.models import ScrapedPost, SocialPost, Target

    terms, exclusions = _topic_terms(topic)
    since = datetime.now(timezone.utc) - timedelta(days=topic.period_days or 30)

    def _match_any(*cols):
        conds = []
        for term in terms:
            like = f"%{term.lower()}%"
            conds.extend(func.lower(func.coalesce(col, "")).like(like) for col in cols)
        return or_(*conds)

    def _exclude(query, *cols):
        for word in exclusions:
            like = f"%{word.lower()}%"
            for col in cols:
                query = query.where(~func.lower(func.coalesce(col, "")).like(like))
        return query

    candidates: list[dict] = []
    async with CelerySessionLocal() as sess:
        kq = (
            select(ScrapedPost, Target.name)
            .join(Target, ScrapedPost.target_id == Target.id)
            .where(ScrapedPost.scraped_at >= since)
            .where(_match_any(ScrapedPost.raw_content, ScrapedPost.title))
        )
        kq = _exclude(kq, ScrapedPost.raw_content, ScrapedPost.title)
        kq = kq.order_by(desc(ScrapedPost.scraped_at)).limit(_MAX_KOL_POSTS)
        for post, target_name in (await sess.execute(kq)).all():
            candidates.append({
                "kind": "kol",
                "platform": post.source_type or "web",
                "author": target_name,
                "url": post.source_url,
                "title": post.title,
                "text": (post.raw_content or "")[:_TEXT_SNIPPET],
                "date": post.published_date or (post.scraped_at.date().isoformat() if post.scraped_at else ""),
                "engagement": (post.likes or 0) + (post.views or 0),
            })

        engagement = (func.coalesce(SocialPost.likes, 0)
                      + func.coalesce(SocialPost.comments, 0)
                      + func.coalesce(SocialPost.shares, 0))
        sq = (
            select(SocialPost)
            .where(func.coalesce(SocialPost.posted_at, SocialPost.scraped_at) >= since)
            .where(_match_any(SocialPost.text, SocialPost.topic, SocialPost.hashtags))
        )
        if topic.language_filter:
            sq = sq.where(SocialPost.language == topic.language_filter)
        sq = _exclude(sq, SocialPost.text, SocialPost.topic, SocialPost.hashtags)
        sq = sq.order_by(desc(engagement), desc(SocialPost.scraped_at)).limit(_MAX_SOCIAL_POSTS)
        for post in (await sess.execute(sq)).scalars().all():
            candidates.append({
                "kind": "social",
                "platform": post.platform,
                "author": post.author,
                "url": post.post_url,
                "title": None,
                "text": (post.text or "")[:_TEXT_SNIPPET],
                "date": post.posted_at.date().isoformat() if post.posted_at else "",
                "engagement": (post.likes or 0) + (post.comments or 0) + (post.shares or 0),
            })

    return candidates


# ── Phase 2: one TinyFish discovery search ────────────────

def _web_context(topic, exclusions: list[str]) -> list[dict]:
    """Best-effort fresh web context. TinyFish CLI subprocess ONLY (via the
    existing scraper helpers). Failure or zero credits → empty list."""
    try:
        from app.services.scraper import _tf_search_discovery
        hits = _tf_search_discovery(topic.name) or []
    except Exception as exc:
        logger.warning("burning_topic.tinyfish_failed", exc=str(exc)[:200])
        return []

    out: list[dict] = []
    for hit in hits:
        url = hit.get("url") or ""
        title = hit.get("title") or ""
        snippet = hit.get("snippet") or ""
        if not url:
            continue
        blob = f"{title} {snippet}".lower()
        if any(w.lower() in blob for w in exclusions):
            continue
        out.append({
            "kind": "web",
            "platform": "web",
            "author": None,
            "url": url,
            "title": title or url,
            "text": snippet[:_TEXT_SNIPPET],
            "date": "",
            "engagement": 0,
        })
        if len(out) >= _MAX_WEB_RESULTS:
            break
    return out


# ── Phase 3: synthesis ────────────────────────────────────

def _compute_main_authors(candidates: list[dict]) -> list[dict]:
    """Deterministic author ranking from matched KOL/social posts — the LLM only
    adds a one-line note per author, it never invents the list."""
    stats: dict[str, dict] = {}
    pool = [c for c in candidates if c["kind"] in ("kol", "social")] or candidates
    for c in pool:
        author = (c.get("author") or "").strip()
        if not author:
            continue
        s = stats.setdefault(author.lower(), {
            "author": author, "posts": 0, "engagement": 0, "platforms": set()})
        s["posts"] += 1
        s["engagement"] += int(c.get("engagement") or 0)
        s["platforms"].add(c.get("platform") or "web")
    ranked = sorted(stats.values(), key=lambda s: (-s["posts"], -s["engagement"]))[:8]
    for s in ranked:
        s["platforms"] = sorted(s["platforms"])
        s["note"] = None
    return ranked


# Like synthesizer.parse_picks but tolerant of prefixes: Gemini often writes
# "- [3] why it matters" and the shared anchored regex misses those lines.
_PICK_ANY_RE = re.compile(r"\[(\d+)\]\s*(.+)")


def _parse_picks_loose(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        m = _PICK_ANY_RE.search(line)
        if m:
            out.append({"id": int(m.group(1)), "why": m.group(2).strip()})
    return out


def _synthesize(topic, candidates: list[dict]) -> dict:
    from app.services.llm_router import call_llm
    from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete

    lines = []
    for i, c in enumerate(candidates, 1):
        head = f"[{i}] ({c['kind']}/{c['platform']}) {c.get('author') or '?'} | {c['date'] or '?'} | eng:{c['engagement']}"
        body = f"{(c.get('title') or '')[:120]} — {c['text']}".strip(" —")
        lines.append(f"{head}\n    {body}")

    lang_note = (f" Prefer {topic.language_filter}-language content and note when findings are "
                 f"language-specific." if topic.language_filter else "")
    system = (
        "You are a pharma intelligence analyst for Roche France writing a burning-topic report. "
        "Base everything strictly on the numbered posts provided — never invent posts, authors or "
        f"numbers.{lang_note} Output EXACTLY these five sections with these markers and nothing else:\n"
        "##SUMMARY##\n2-3 short paragraphs on what is happening around this topic.\n"
        "##KEY_FINDINGS##\n5-8 lines, one specific finding per line, starting with '- '.\n"
        "##SO_WHAT##\nOne paragraph: implications and recommended focus for pharma/Roche.\n"
        "##IMPORTANT_POSTS##\n5-8 lines like '[12] one line on why this post matters' where 12 is the post number.\n"
        "##MAIN_AUTHORS##\nOne line per notable author: '- Author Name — their role in this conversation'."
    )
    user = (
        f"TOPIC: {topic.name}\n"
        f"DESCRIPTION: {topic.description or '-'}\n"
        f"PERIOD: last {topic.period_days} days\n\n"
        f"POSTS/ARTICLES ({len(candidates)}):\n" + "\n".join(lines)
    )

    raw = call_llm(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3,
        max_tokens=4096,
    )

    key_findings = parse_bullets(extract_section(raw, "KEY_FINDINGS"))

    important_posts = []
    for pick in _parse_picks_loose(extract_section(raw, "IMPORTANT_POSTS")):
        idx = pick["id"] - 1
        if 0 <= idx < len(candidates):
            c = candidates[idx]
            important_posts.append({
                "url": c["url"],
                "title": c.get("title") or (c["text"][:100] if c["text"] else c["url"]),
                "author": c.get("author"),
                "engagement": c["engagement"],
                "platform": c["platform"],
                "why": pick["why"],
            })

    main_authors = _compute_main_authors(candidates)
    for note_line in parse_bullets(extract_section(raw, "MAIN_AUTHORS")):
        name, _, note = note_line.partition("—")
        name = name.strip().lower()
        if not name:
            continue
        for author in main_authors:
            if name.startswith(author["author"].lower()) or author["author"].lower() in name:
                author["note"] = author["note"] or note.strip() or None
                break

    return {
        "summary_md": trim_incomplete(extract_section(raw, "SUMMARY")),
        "key_findings": key_findings,
        "so_what": trim_incomplete(extract_section(raw, "SO_WHAT")),
        "important_posts": important_posts,
        "main_authors": main_authors,
    }


# ── Phase 4: PDF + blob upload ────────────────────────────

def _pdf_local_path(topic_id: int, report_id: int):
    from pathlib import Path
    from app.config import get_settings
    out_dir = Path(get_settings().reports_dir) / "burning_topics"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"topic_{topic_id}_report_{report_id}.pdf"


def _summary_to_html(md: str) -> str:
    import html as _html
    import re
    text = _html.escape(md or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    paras = [p.strip().replace("\n", "<br>") for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paras)


def _render_pdf(topic, report_id: int, fields: dict) -> str | None:
    """Write the PDF locally (served by the fallback endpoint) and upload to
    Vercel Blob when configured. Returns the public blob URL or None."""
    import html as _html
    from weasyprint import HTML
    from app.config import get_settings
    from app.services.pdf_generator import _BASE_CSS, _validate_pdf

    settings = get_settings()
    today = datetime.now(timezone.utc).date().isoformat()

    findings_html = "".join(f"<li>{_html.escape(f)}</li>" for f in fields["key_findings"])
    findings_html = f"<ul class='sum-list'>{findings_html}</ul>" if findings_html \
        else "<div class='empty-card'>No key findings.</div>"

    posts_html = ""
    for p in fields["important_posts"]:
        posts_html += (
            "<div class='recap-card'>"
            f"<div class='label'>{_html.escape(p.get('author') or '?')} · {_html.escape(p.get('platform') or '')} · "
            f"engagement {p.get('engagement', 0)}</div>"
            f"<div class='body'><strong>{_html.escape((p.get('title') or '')[:160])}</strong><br>"
            f"{_html.escape(p.get('why') or '')}<br>"
            f"<small>{_html.escape(p.get('url') or '')}</small></div>"
            "</div>"
        )
    posts_html = posts_html or "<div class='empty-card'>No highlighted posts.</div>"

    authors_html = ""
    for a in fields["main_authors"]:
        note = f" — {_html.escape(a['note'])}" if a.get("note") else ""
        authors_html += (
            f"<li><strong>{_html.escape(a['author'])}</strong> "
            f"({a['posts']} post(s), engagement {a['engagement']}, {_html.escape(', '.join(a['platforms']))})"
            f"{note}</li>"
        )
    authors_html = f"<ul class='sum-list'>{authors_html}</ul>" if authors_html \
        else "<div class='empty-card'>No recurring authors identified.</div>"

    so_what = _html.escape(fields["so_what"] or "").replace("\n", "<br>")

    html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{_BASE_CSS}</style>
</head><body>
<div class="header">
  <h1>PharmaRadar Burning Topic Report</h1>
  <div class="subtitle">Pharma Intelligence Monitoring System</div>
  <div class="meta">
    <strong>Topic:</strong> {_html.escape(topic.name)}<br>
    <strong>Period:</strong> last {topic.period_days} days<br>
    <strong>Report Date:</strong> {today}
  </div>
</div>
<div class="section-title">Summary</div>
<div class="recap-card"><div class="body">{_summary_to_html(fields["summary_md"]) or "<em>No summary.</em>"}</div></div>
<div class="section-title">Key findings</div>
{findings_html}
<div class="section-title">So what for pharma</div>
<div class="sowhat-card"><div class="body">{so_what or "<em>No analyst note.</em>"}</div></div>
<div class="section-title">Important posts</div>
{posts_html}
<div class="section-title">Main authors</div>
{authors_html}
<div class="footer">Generated by PharmaRadar &nbsp;·&nbsp; {today} &nbsp;·&nbsp; Confidential</div>
</body></html>"""

    pdf_path = _pdf_local_path(topic.id, report_id)
    HTML(string=html_doc).write_pdf(str(pdf_path))
    _validate_pdf(pdf_path)

    if settings.vercel_blob_token:
        try:
            from app.services.vercel_blob_storage import upload_burning_topic_pdf
            slug = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in topic.name)[:60] or "topic"
            return upload_burning_topic_pdf(
                pdf_binary=pdf_path.read_bytes(),
                topic_slug=f"{topic.id}_{slug}",
                report_id=report_id,
                vercel_token=settings.vercel_blob_token,
            )
        except Exception as exc:
            logger.warning("burning_topic.blob_upload_failed", report_id=report_id, error=str(exc))
    return None


# ── The task ──────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.tasks.burning_topics.generate_topic_report",
    queue="scrape",
    max_retries=1,
    default_retry_delay=60,
    acks_late=True,
)
def generate_topic_report(self, report_id: int) -> dict:
    from celery.exceptions import SoftTimeLimitExceeded

    log = logger.bind(report_id=report_id, task_id=self.request.id)
    log.info("burning_topic_report.started")

    report, topic = asyncio.run(_load(report_id))
    if not report or not topic:
        log.warning("burning_topic_report.missing_row")
        return {"skipped": "report_or_topic_missing"}
    if report.status == "done":
        return {"skipped": "already_done"}   # acks_late re-delivery of a finished task

    asyncio.run(_set_fields(report_id, status="running"))

    try:
        _, exclusions = _topic_terms(topic)

        candidates = asyncio.run(_gather_db_posts(topic))
        log.info("burning_topic_report.db_posts", count=len(candidates))
        if _aborted(report_id):
            return {"aborted": "after_db_query"}

        web = _web_context(topic, exclusions)
        log.info("burning_topic_report.web_results", count=len(web))
        candidates += web
        if _aborted(report_id):
            return {"aborted": "after_web_search"}

        if not candidates:
            asyncio.run(_set_fields(
                report_id, status="done",
                summary_md=(f"No posts or articles matching “{topic.name}” were found in the "
                            f"last {topic.period_days} days. Try a longer period, extra "
                            f"restriction terms, or run a scrape first."),
                key_findings="[]", so_what=None, important_posts="[]", main_authors="[]",
            ))
            log.info("burning_topic_report.no_data")
            return {"status": "done", "candidates": 0}

        fields = _synthesize(topic, candidates)
        if _aborted(report_id):
            return {"aborted": "after_synthesis"}

        pdf_url = _render_pdf(topic, report_id, fields)

        asyncio.run(_set_fields(
            report_id, status="done",
            summary_md=fields["summary_md"],
            key_findings=json.dumps(fields["key_findings"]),
            so_what=fields["so_what"],
            important_posts=json.dumps(fields["important_posts"]),
            main_authors=json.dumps(fields["main_authors"]),
            pdf_url=pdf_url,
        ))
        log.info("burning_topic_report.done", candidates=len(candidates), pdf=bool(pdf_url))
        return {"status": "done", "candidates": len(candidates), "pdf_url": pdf_url}

    except SoftTimeLimitExceeded:
        asyncio.run(_set_fields(report_id, status="failed",
                                summary_md="Report generation timed out."))
        log.warning("burning_topic_report.soft_timeout")
        return {"status": "failed", "reason": "timeout"}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            log.warning("burning_topic_report.retry", exc=str(exc)[:300])
            raise self.retry(exc=exc)
        asyncio.run(_set_fields(report_id, status="failed",
                                summary_md=f"Report generation failed: {str(exc)[:400]}"))
        log.error("burning_topic_report.failed", exc=str(exc)[:300])
        raise
