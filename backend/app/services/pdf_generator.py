"""WeasyPrint PDF generation service — v1-quality layout."""
from __future__ import annotations

import asyncio
import html as _html
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import structlog

from app.config import get_settings
from app.services.run_context import RunContext

logger = structlog.get_logger(__name__)
settings = get_settings()

# Weekly run cadence — the daily summary's "findings this week" section covers this many days
SUMMARY_WINDOW_DAYS = 7

# Lower rank = higher priority in PDFs
_CATEGORY_RANK = {
    "roche": 0, "other_pharma": 1, "drug_approval": 2, "clinical_trial": 3,
    "pricing": 4, "oncology": 5, "research": 6, "policy": 7,
    "conference": 8, "interview": 9, "other": 10,
}

_SOURCE_LABELS = {
    "twitter.com": "X / Twitter", "x.com": "X / Twitter",
    "linkedin.com": "LinkedIn", "substack.com": "Substack",
    "statnews.com": "STAT News", "endpoints.news": "Endpoints News",
    "fiercepharma.com": "FiercePharma", "biopharmadive.com": "BioPharma Dive",
    "reuters.com": "Reuters", "bloomberg.com": "Bloomberg",
    "forbes.com": "Forbes", "nature.com": "Nature",
    "nejm.org": "NEJM", "medscape.com": "Medscape",
    "esmo.org": "ESMO", "researchgate.net": "ResearchGate",
}


def _pretty_source(post) -> str:
    if getattr(post, "source_name", None):
        return post.source_name
    url = post.source_url or ""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    host = host.lower().lstrip("www.")
    for key, label in _SOURCE_LABELS.items():
        if key in host:
            return label
    return host or "Web"


def _format_date(post) -> str:
    if getattr(post, "published_date", None):
        return post.published_date
    if post.scraped_at:
        return post.scraped_at.date().isoformat()
    return "—"


def _insight_rank(insight) -> int:
    cats = (insight.category or "other").lower().split("|")
    return min(_CATEGORY_RANK.get(c.strip(), 10) for c in cats)


def _bullets_to_html(bullets_json: str) -> str:
    """Render summary_bullets JSON → HTML list. Handles both dict and string bullet formats."""
    if not bullets_json:
        return ""
    try:
        bullets = json.loads(bullets_json)
    except (ValueError, TypeError):
        return ""

    items = []
    for b in bullets[:10]:
        if isinstance(b, dict):
            text = (b.get("text") or "").strip()
        elif isinstance(b, str):
            text = b.strip()
        else:
            continue
        if text:
            items.append(f"<li>{_html.escape(text)}</li>")

    return "<ul class='sum-list'>" + "".join(items) + "</ul>" if items else ""


def _validate_pdf(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"PDF not written: {path}")
    if path.stat().st_size < 1024:
        raise ValueError(f"PDF too small ({path.stat().st_size} bytes): {path}")


# ── SVG chart builders (WeasyPrint renders inline SVG natively) ──────────────

_CHART_COLORS = [
    "#1f4eaa", "#e94560", "#22c55e", "#f59e0b",
    "#8b5cf6", "#06b6d4", "#f97316", "#ec4899",
    "#10b981", "#3b82f6",
]

_SENTIMENT_COLORS = {"positive": "#22c55e", "neutral": "#94a3b8", "negative": "#ef4444"}


def _css_hbar(items: list[tuple[str, int]], title: str) -> str:
    """Horizontal bar chart using pure CSS — renders correctly in WeasyPrint."""
    if not items:
        return ""
    max_val = max(v for _, v in items) or 1
    rows = ""
    for i, (label, val) in enumerate(items):
        pct = max(2, int(val / max_val * 100))
        color = _CHART_COLORS[i % len(_CHART_COLORS)]
        rows += (
            f'<tr>'
            f'<td style="text-align:right;padding-right:8px;font-size:10px;color:#444;'
            f'white-space:nowrap;width:130px">{_html.escape(label[:22])}</td>'
            f'<td style="width:160px;padding-right:6px">'
            f'<div style="background:{color};height:14px;width:{pct}%;border-radius:3px;'
            f'min-width:4px"></div></td>'
            f'<td style="font-size:10px;color:#555;font-weight:bold">{val}</td>'
            f'</tr>'
        )
    return (
        f'<div style="margin-bottom:14px">'
        f'<div style="font-size:10px;font-weight:bold;color:#555;text-transform:uppercase;'
        f'letter-spacing:1px;margin-bottom:6px">{_html.escape(title)}</div>'
        f'<table style="border-collapse:collapse;width:100%">{rows}</table>'
        f'</div>'
    )


def _css_sentiment_bar(counts: dict[str, int]) -> str:
    """Stacked sentiment bar using table cells — correct in WeasyPrint."""
    total = sum(counts.values()) or 1
    segments = []
    for label in ("positive", "neutral", "negative"):
        val = counts.get(label, 0)
        if val == 0:
            continue
        pct = round(val / total * 100)
        color = _SENTIMENT_COLORS[label]
        segments.append(
            f'<td style="width:{pct}%;background:{color};height:20px;'
            f'text-align:center;font-size:9px;color:white;font-weight:bold;'
            f'vertical-align:middle">'
            f'{label.title()} {pct}%</td>'
        )
    legend = "".join(
        f'<span style="display:inline-block;width:9px;height:9px;background:{_SENTIMENT_COLORS[k]};'
        f'border-radius:2px;margin-right:3px;vertical-align:middle"></span>'
        f'<span style="font-size:10px;color:#555;margin-right:10px">'
        f'{k.title()} ({counts.get(k, 0)})</span>'
        for k in ("positive", "neutral", "negative")
    )
    return (
        f'<div style="margin-bottom:14px">'
        f'<div style="font-size:10px;font-weight:bold;color:#555;text-transform:uppercase;'
        f'letter-spacing:1px;margin-bottom:6px">Sentiment Breakdown</div>'
        f'<table style="border-collapse:collapse;width:100%;border-radius:4px;overflow:hidden">'
        f'<tr>{"".join(segments)}</tr></table>'
        f'<div style="margin-top:5px">{legend}</div>'
        f'</div>'
    )


def _css_trending_topics(topics: list[tuple[str, int]], max_items: int = 10) -> str:
    """Trending topics with CSS bar — renders cleanly in WeasyPrint."""
    if not topics:
        return ""
    top = topics[:max_items]
    max_val = top[0][1] if top else 1
    rows = ""
    for i, (topic, count) in enumerate(top):
        pct = max(2, int(count / max_val * 100))
        color = _CHART_COLORS[i % len(_CHART_COLORS)]
        rows += (
            f'<tr>'
            f'<td style="font-size:10px;color:#333;padding-bottom:5px;width:75%">'
            f'{_html.escape(topic[:60])}'
            f'<div style="background:#e5e7eb;border-radius:2px;height:5px;margin-top:2px">'
            f'<div style="background:{color};width:{pct}%;height:5px;border-radius:2px"></div>'
            f'</div></td>'
            f'<td style="text-align:right;font-size:10px;font-weight:bold;'
            f'color:{color};padding-left:8px;vertical-align:top">{count}</td>'
            f'</tr>'
        )
    return (
        f'<div style="margin-bottom:14px">'
        f'<div style="font-size:10px;font-weight:bold;color:#555;text-transform:uppercase;'
        f'letter-spacing:1px;margin-bottom:6px">Trending Topics</div>'
        f'<table style="border-collapse:collapse;width:100%">{rows}</table>'
        f'</div>'
    )


def _build_analytics_section(all_insights: list) -> str:
    """Analytics section: categories, sentiment, trending topics.
    Uses table-based layout so WeasyPrint renders columns without overlap."""
    from collections import Counter

    cat_counts: Counter = Counter()
    sent_counts: Counter = Counter({"positive": 0, "neutral": 0, "negative": 0})
    topic_counts: Counter = Counter()

    for ins, *_ in all_insights:
        cat = (ins.category or "other").replace("_", " ").title()
        cat_counts[cat] += 1
        sent = (ins.sentiment or "neutral").lower()
        if sent in sent_counts:
            sent_counts[sent] += 1
        if ins.topic:
            topic_counts[ins.topic] += 1

    total = len(all_insights)
    if total == 0:
        return ""

    cat_html   = _css_hbar(cat_counts.most_common(8), "Most Discussed Categories")
    sent_html  = _css_sentiment_bar(dict(sent_counts))
    topic_html = _css_trending_topics(topic_counts.most_common(10))

    # Fixed two-column table: WeasyPrint handles <table> layout reliably
    return f"""
<div style="page-break-inside:avoid;margin-bottom:24px;
  background:#f7f8fc;border:1px solid #e0e0e8;border-radius:8px;padding:16px 18px">
  <div style="font-size:12px;font-weight:bold;color:#1a1a2e;text-transform:uppercase;
    letter-spacing:1px;border-bottom:2px solid #e94560;padding-bottom:5px;margin-bottom:12px">
    Intelligence Analytics — {total} insight(s)
  </div>
  <table style="border-collapse:collapse;width:100%">
    <tr>
      <td style="width:48%;vertical-align:top;padding-right:16px">
        {cat_html}
        {sent_html}
      </td>
      <td style="width:4%"></td>
      <td style="width:48%;vertical-align:top">
        {topic_html}
      </td>
    </tr>
  </table>
</div>"""


# ── CSS shared by individual + daily ──────────────────────────────────────────
_BASE_CSS = """
@page { margin: 40px; }
body { font-family: Georgia, 'Times New Roman', serif; color: #1a1a2e; background: #fff; font-size: 13px; }
a { color: #1f4eaa; text-decoration: none; }
a:hover { text-decoration: underline; }
.header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white;
  padding: 28px 32px; border-radius: 8px; margin-bottom: 24px; }
.header h1 { margin: 0 0 4px 0; font-size: 22px; letter-spacing: 1px; font-weight: bold; }
.header .subtitle { font-size: 12px; opacity: 0.6; margin-bottom: 14px; }
.header .meta { font-size: 12px; opacity: 0.85; line-height: 2; }
.section-title { font-size: 13px; font-weight: bold; color: #1a1a2e; text-transform: uppercase;
  letter-spacing: 1px; border-bottom: 2px solid #e94560; padding-bottom: 6px; margin: 24px 0 12px; }
.recap-card { background: #f7f7fb; border: 1px solid #e0e0e8; border-left: 4px solid #1f4eaa;
  border-radius: 6px; padding: 14px 18px; margin-bottom: 14px; page-break-inside: avoid; }
.recap-card .label { font-size: 10px; font-weight: bold; color: #1f4eaa; text-transform: uppercase;
  letter-spacing: 1px; margin-bottom: 6px; }
.recap-card .stamp { font-size: 10px; color: #888; margin-bottom: 8px; }
.sum-list { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.6; color: #222; }
.sum-list li { margin-bottom: 3px; }
.sowhat-card { background: #fff8f0; border: 1px solid #f3d9b8; border-left: 4px solid #e07b00;
  border-radius: 6px; padding: 14px 18px; margin-bottom: 22px; page-break-inside: avoid; }
.sowhat-card .label { font-size: 10px; font-weight: bold; color: #b35900; text-transform: uppercase;
  letter-spacing: 1px; margin-bottom: 6px; }
.sowhat-card .body { font-size: 12px; line-height: 1.6; color: #3a2a10; }
.empty-card { background: #f7f7fb; border: 1px solid #e0e0e8; border-left: 4px solid #999;
  padding: 12px 18px; border-radius: 6px; font-size: 12px; color: #666; font-style: italic; margin-bottom: 22px; }
.insight { border: 1px solid #e0e0e0; border-left: 4px solid #e94560; padding: 14px 18px;
  margin-bottom: 14px; border-radius: 4px; background: #fafafa; page-break-inside: avoid; }
.insight .topic { font-weight: bold; font-size: 13px; color: #1a1a2e; margin-bottom: 4px; }
.insight .post-date { font-size: 11px; color: #777; margin-bottom: 6px; }
.insight .context { font-size: 11.5px; color: #444; line-height: 1.55; margin-bottom: 8px;
  padding: 6px 10px; background: #eef3fb; border-radius: 4px; border-left: 3px solid #1f4eaa; }
.insight .context .lab { font-size: 9px; font-weight: bold; color: #1f4eaa;
  text-transform: uppercase; letter-spacing: 1px; margin-right: 6px; }
.insight .statement { font-size: 12px; color: #333; line-height: 1.7; margin-bottom: 10px;
  font-style: italic; padding: 8px 12px; background: #f0f0f5; border-radius: 4px; }
.meta-row { font-size: 11px; color: #666; }
.meta-row .badge { padding: 2px 9px; border-radius: 10px; font-weight: bold; font-size: 10px;
  text-transform: uppercase; margin-right: 8px; }
.positive { background: #d4edda; color: #155724; }
.negative { background: #f8d7da; color: #721c24; }
.neutral  { background: #e2e3e5; color: #383d41; }
.cat { background: #e8eaf6; color: #3949ab; }
.no-findings { text-align: center; color: #999; font-style: italic; padding: 40px 0; font-size: 13px; }
.footer { margin-top: 40px; font-size: 10px; color: #aaa; text-align: center;
  border-top: 1px solid #eee; padding-top: 14px; }
"""

_DAILY_EXTRA_CSS = """
.person-block { margin-bottom: 22px; page-break-before: always; }
.person-block:first-of-type { page-break-before: auto; }
.person-header { background: linear-gradient(135deg, #1f4eaa 0%, #2563b3 100%);
  color: white; border-left: 6px solid #0d3274; padding: 14px 18px;
  border-radius: 6px; margin-bottom: 12px; }
.person-header h2 { margin: 0; font-size: 16px; color: white; }
.person-header .notes { font-size: 11px; color: #cfdcef; margin-top: 4px; }
.person-recap { background: #f7f7fb; border: 1px solid #e0e0e8; border-left: 4px solid #1f4eaa;
  padding: 10px 14px; border-radius: 4px; margin-bottom: 10px; font-size: 11px; line-height: 1.55; }
.person-recap .lab { font-weight: bold; color: #1f4eaa; font-size: 10px;
  text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
.person-sowhat { background: #fff8f0; border: 1px solid #f3d9b8; border-left: 4px solid #e07b00;
  padding: 8px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 11px; line-height: 1.55; color: #3a2a10; }
.person-sowhat .lab { font-weight: bold; color: #b35900; font-size: 10px;
  text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
"""


def _insight_card_html(insight, post) -> str:
    ctx = (getattr(insight, "context", "") or "").strip()
    context_block = (
        f'<div class="context"><span class="lab">Context</span>{_html.escape(ctx)}</div>'
        if ctx else ""
    )
    badge = (insight.sentiment or "neutral").lower()
    if badge not in ("positive", "negative", "neutral"):
        badge = "neutral"
    cat = (insight.category or "other").replace("_", " ").title()
    url = post.source_url or "" if post else ""
    src_label = _pretty_source(post) if post else "Web"
    safe_url = _html.escape(url, quote=True)
    url_link = (
        f'<a href="{safe_url}" style="display:block;margin-top:4px;word-break:break-all;font-size:10px;color:#999;">'
        f'{_html.escape(url)}</a>'
        if url else ""
    )
    return (
        f'<div class="insight">'
        f'<div class="topic">{_html.escape(insight.topic or "General finding")}</div>'
        f'<div class="post-date">Posted: {_html.escape(_format_date(post) if post else "—")}</div>'
        f'{context_block}'
        f'<div class="statement">&ldquo;{_html.escape(insight.what_they_said or "")}&rdquo;</div>'
        f'<div class="meta-row">'
        f'<span class="badge {badge}">{badge.capitalize()}</span>'
        f'<span class="badge cat">{_html.escape(cat)}</span>'
        f'&nbsp;Source: <strong>{_html.escape(src_label)}</strong>'
        f'{url_link}'
        f'</div>'
        f'</div>'
    )


# ── Individual per-target report ───────────────────────────────────────────────

class PDFService:
    def generate_target_report(self, target_id: int, run_id: int, ctx: RunContext) -> dict:
        return asyncio.run(self._target_async(target_id, run_id))

    async def _target_async(self, target_id: int, run_id: int) -> dict:
        from app.database import CelerySessionLocal
        from app.models import Target, ExtractedInsight, PersonSummary, ScrapedPost
        from sqlalchemy import select

        async with CelerySessionLocal() as sess:
            target = await sess.get(Target, target_id)
            if not target:
                return {"error": "target_not_found"}

            # All insights for this target (sorted by priority)
            ins_rows = await sess.execute(
                select(ExtractedInsight, ScrapedPost)
                .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
                .where(ExtractedInsight.target_id == target_id)
                .order_by(ExtractedInsight.extracted_at.desc())
                .limit(100)
            )
            insight_posts = ins_rows.all()
            insight_posts = sorted(insight_posts, key=lambda r: _insight_rank(r[0]))

            # Most recent summary (any run)
            sum_row = await sess.execute(
                select(PersonSummary)
                .where(PersonSummary.target_id == target_id)
                .order_by(PersonSummary.generated_at.desc())
                .limit(1)
            )
            summary = sum_row.scalar_one_or_none()

        today = date.today().isoformat()

        # Build insight cards
        insights_html = "".join(_insight_card_html(ins, post) for ins, post in insight_posts)
        if not insights_html:
            insights_html = (
                f'<div class="no-findings">No insights found for {_html.escape(target.name)} yet.<br>'
                f'<small style="font-size:11px;">The pipeline will populate findings when new content is scraped and extracted.</small></div>'
            )

        # Build recap + so-what
        if summary and (summary.summary_bullets or summary.so_what_pharma):
            stamp = summary.generated_at.strftime("%Y-%m-%d") if summary.generated_at else today
            bullets_html = _bullets_to_html(summary.summary_bullets or "[]")
            recap_html = (
                f'<div class="recap-card">'
                f'<div class="label">90-day recap — {summary.insights_count or 0} insight(s) analysed</div>'
                f'<div class="stamp">Generated {stamp}</div>'
                f'{bullets_html or "<em>No bullets yet.</em>"}'
                f'</div>'
            )
            sowhat_body = _html.escape(summary.so_what_pharma or "").replace("\n", "<br>")
            sowhat_html = (
                f'<div class="sowhat-card"><div class="label">Implications for pharma</div>'
                f'<div class="body">{sowhat_body or "<em>No analyst note yet.</em>"}</div></div>'
            )
        else:
            recap_html = '<div class="empty-card">No 90-day recap yet — insights need to be extracted first.</div>'
            sowhat_html = '<div class="empty-card">No analyst note yet.</div>'

        sources = {_pretty_source(p) for _, p in insight_posts}
        count = len(insight_posts)

        # Analytics charts for this target
        analytics_html = _build_analytics_section([(ins, post) for ins, post in insight_posts]) if insight_posts else ""

        html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{_BASE_CSS}</style>
</head><body>
<div class="header">
  <h1>PharmaRadar Intelligence Report</h1>
  <div class="subtitle">Pharma Intelligence Monitoring System</div>
  <div class="meta">
    <strong>Person:</strong> {_html.escape(target.name)}<br>
    <strong>Report Date:</strong> {today}<br>
    <strong>Sources Checked:</strong> {_html.escape(", ".join(sorted(sources)) if sources else "Web")}<br>
    <strong>Findings:</strong> {count} insight(s)
  </div>
</div>
<div class="section-title">Recent activity — last 90 days</div>
{recap_html}
<div class="section-title">So what for pharma</div>
{sowhat_html}
{analytics_html}
<div class="section-title">All findings — {count} insight(s)</div>
{insights_html}
<div class="footer">Generated by PharmaRadar &nbsp;·&nbsp; {today} &nbsp;·&nbsp; Confidential</div>
</body></html>"""

        safe_name = target.name.replace(" ", "_").replace("/", "_")
        out_dir = Path(settings.reports_dir) / today / safe_name
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / f"{safe_name}_{today}.pdf"

        from weasyprint import HTML
        HTML(string=html_doc).write_pdf(str(pdf_path))
        _validate_pdf(pdf_path)

        # Upload to Vercel Blob if token configured
        vercel_url = None
        if settings.vercel_blob_token:
            try:
                from app.services.vercel_blob_storage import upload_pdf_to_vercel_blob
                pdf_binary = pdf_path.read_bytes()
                vercel_url = upload_pdf_to_vercel_blob(
                    pdf_binary=pdf_binary,
                    target_name=safe_name,
                    run_date=date.fromisoformat(today),
                    vercel_token=settings.vercel_blob_token,
                )
                logger.info("pdf.target_uploaded_to_blob", target=target.name, url=vercel_url)
            except Exception as e:
                logger.warning("pdf.target_blob_upload_failed", target=target.name, error=str(e))
                # Continue with local path on upload failure — don't block the run

        result = {"path": str(pdf_path)}
        if vercel_url:
            result["vercel_url"] = vercel_url
        logger.info("pdf.target_generated", target=target.name, path=str(pdf_path), insights=count)
        return result

    # ── Daily summary ──────────────────────────────────────────────────────────

    def generate_daily_summary(self, run_id: int, ctx: RunContext) -> dict:
        return asyncio.run(self._daily_async(run_id))

    async def _daily_async(self, run_id: int) -> dict:
        from app.database import CelerySessionLocal
        from app.models import PersonSummary, Target, ExtractedInsight, ScrapedPost
        from sqlalchemy import select

        # Weekly cadence — findings section covers the last 7 days (run once a week)
        window_cutoff = datetime.now(timezone.utc) - timedelta(days=SUMMARY_WINDOW_DAYS)

        async with CelerySessionLocal() as sess:
            # Insights from the last 7 days (this week's findings)
            ins_rows = await sess.execute(
                select(ExtractedInsight, ScrapedPost, Target)
                .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
                .join(Target, ExtractedInsight.target_id == Target.id)
                .where(ExtractedInsight.extracted_at >= window_cutoff)
                .order_by(Target.name, ExtractedInsight.extracted_at)
            )
            all_insights = ins_rows.all()

            # Fallback: if nothing was extracted in the last 7 days (e.g. regenerating
            # an old run from existing data), use all existing insights so the PDF is
            # never empty when data exists — no re-scrape needed.
            if not all_insights:
                ins_rows = await sess.execute(
                    select(ExtractedInsight, ScrapedPost, Target)
                    .join(ScrapedPost, ExtractedInsight.scraped_post_id == ScrapedPost.id)
                    .join(Target, ExtractedInsight.target_id == Target.id)
                    .order_by(Target.name, ExtractedInsight.extracted_at)
                )
                all_insights = ins_rows.all()

            # Latest PersonSummary per target
            sum_rows = await sess.execute(
                select(PersonSummary).order_by(PersonSummary.generated_at.desc())
            )
            summaries: dict[int, PersonSummary] = {}
            for ps in sum_rows.scalars().all():
                if ps.target_id not in summaries:
                    summaries[ps.target_id] = ps

            # All active targets (render all, not just those with today's insights)
            tgt_rows = await sess.execute(
                select(Target).where(Target.active == True).order_by(Target.name)
            )
            all_targets = tgt_rows.scalars().all()

        today = date.today().isoformat()

        # Group insights by target
        from collections import defaultdict
        by_target: dict[int, list] = defaultdict(list)
        target_map: dict[int, Target] = {}
        for ins, post, tgt in all_insights:
            by_target[tgt.id].append((ins, post))
            target_map[tgt.id] = tgt
        for tgt in all_targets:
            target_map[tgt.id] = tgt

        # Build per-target blocks
        categories_seen: set[str] = set()
        person_blocks = ""
        empty_kols: list[str] = []
        for tgt in all_targets:
            items = sorted(by_target.get(tgt.id, []), key=lambda r: _insight_rank(r[0]))
            ps = summaries.get(tgt.id)

            cards = ""
            for ins, post in items:
                if ins.category:
                    categories_seen.add(ins.category)
                cards += _insight_card_html(ins, post)

            recap_block = ""
            if ps and ps.summary_bullets:
                bullets_html = _bullets_to_html(ps.summary_bullets)
                recap_block += (
                    f'<div class="person-recap"><div class="lab">90-day recap</div>'
                    f'{bullets_html}</div>'
                )
            if ps and ps.so_what_pharma:
                sowhat_body = _html.escape(ps.so_what_pharma).replace("\n", "<br>")
                recap_block += (
                    f'<div class="person-sowhat"><div class="lab">So what for pharma</div>'
                    f'{sowhat_body}</div>'
                )

            if not cards and not recap_block:
                # No insights and no summary — list the name at the end instead of dropping it
                empty_kols.append(tgt.name)
                continue

            notes = _html.escape(tgt.notes or "KOL")
            count = len(items)

            # Mini analytics chart per KOL (only when they have ≥3 insights)
            kol_analytics = _build_analytics_section(items) if len(items) >= 3 else ""

            person_blocks += (
                f'<div class="person-block">'
                f'<div class="person-header">'
                f'<h2>{_html.escape(tgt.name)}</h2>'
                f'<div class="notes">{notes} &nbsp;·&nbsp; {count} finding(s)</div>'
                f'</div>'
                f'{recap_block}'
                f'{kol_analytics}'
                f'{cards or "<div class=\'empty-card\'>No new findings this week — summary above from prior runs.</div>"}'
                f'</div>'
            )

        # KOLs with no findings/summary — listed by name at the very end
        empty_block = ""
        if empty_kols:
            names_html = "".join(f'<li>{_html.escape(n)}</li>' for n in sorted(empty_kols))
            empty_block = (
                f'<div class="empty-kols">'
                f'<h2>No data this week</h2>'
                f'<div class="lab">{len(empty_kols)} KOL(s) with no new findings or summary this week</div>'
                f'<ul>{names_html}</ul>'
                f'</div>'
            )

        if not person_blocks and not empty_block:
            person_blocks = '<div class="no-findings">No insights or summaries found for this run.</div>'

        kol_count = len([t for t in all_targets if by_target.get(t.id) or summaries.get(t.id)])
        total_insights = len(all_insights)
        cats_str = ", ".join(sorted(categories_seen)) or "—"

        # Cross-KOL analytics — placed right after the header on page 1
        cross_analytics = _build_analytics_section(all_insights) if all_insights else ""

        # Executive synthesis (LLM) — takeaway + so-what + most impactful findings
        synth_html = self._build_synthesis_html(all_insights)

        html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
{_BASE_CSS}
{_DAILY_EXTRA_CSS}
/* Analytics section inside daily PDF */
.analytics-section {{ page-break-inside: avoid; margin-bottom: 20px; }}
.empty-kols {{ margin-top: 28px; padding-top: 16px; border-top: 1px solid #d1d9e6; page-break-inside: avoid; }}
.empty-kols h2 {{ font-size: 15px; color: #64748b; margin: 0 0 4px; }}
.empty-kols .lab {{ font-size: 11px; color: #94a3b8; margin-bottom: 8px; }}
.empty-kols ul {{ columns: 2; column-gap: 28px; margin: 0; padding-left: 18px; }}
.empty-kols li {{ font-size: 12px; color: #475569; margin-bottom: 3px; break-inside: avoid; }}
.synthesis {{ margin-bottom: 22px; padding: 14px 16px; border: 1px solid #c7d2e6; border-radius: 8px; background: #f5f8fc; page-break-inside: avoid; }}
.synthesis h2 {{ font-size: 14px; color: #1a3a5f; margin: 0 0 8px; }}
.synthesis .lab {{ font-size: 10px; font-weight: bold; text-transform: uppercase; letter-spacing: .06em; color: #64748b; margin: 10px 0 3px; }}
.synthesis p {{ font-size: 12px; color: #334155; margin: 0 0 4px; line-height: 1.5; }}
.synthesis .sowhat {{ background: #eaf1fb; border-left: 3px solid #2563eb; padding: 8px 10px; border-radius: 4px; margin-top: 6px; }}
.synthesis ul {{ margin: 4px 0 0; padding-left: 16px; }}
.synthesis li {{ font-size: 12px; color: #334155; margin-bottom: 4px; }}
.synthesis li b {{ color: #1a3a5f; }}
</style>
</head><body>
<div class="header">
  <h1>PharmaRadar Weekly Intelligence Summary</h1>
  <div class="subtitle">Pharma Intelligence Monitoring System</div>
  <div class="meta">
    <strong>Date:</strong> {today}<br>
    <strong>KOLs with findings:</strong> {kol_count}<br>
    <strong>Total insights:</strong> {total_insights}<br>
    <strong>Categories:</strong> {_html.escape(cats_str)}
  </div>
</div>

{synth_html}

{cross_analytics}

{person_blocks}
{empty_block}
<div class="footer">PharmaRadar Summary &nbsp;·&nbsp; {today} &nbsp;·&nbsp; Confidential</div>
</body></html>"""

        out_dir = Path(settings.reports_dir) / today
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / f"Daily_Summary_{today}.pdf"

        from weasyprint import HTML
        HTML(string=html_doc).write_pdf(str(pdf_path))
        _validate_pdf(pdf_path)

        # Upload to Vercel Blob if token configured
        vercel_url = None
        if settings.vercel_blob_token:
            try:
                from app.services.vercel_blob_storage import upload_daily_summary_to_vercel_blob
                pdf_binary = pdf_path.read_bytes()
                vercel_url = upload_daily_summary_to_vercel_blob(
                    pdf_binary=pdf_binary,
                    run_date=date.fromisoformat(today),
                    vercel_token=settings.vercel_blob_token,
                )
                logger.info("pdf.daily_uploaded_to_blob", url=vercel_url)
            except Exception as e:
                logger.warning("pdf.daily_blob_upload_failed", error=str(e))
                # Continue with local path on upload failure — don't block the run

        result = {"path": str(pdf_path)}
        if vercel_url:
            result["vercel_url"] = vercel_url
        logger.info("pdf.daily_generated", path=str(pdf_path), insights=total_insights)
        return result

    def _build_synthesis_html(self, all_insights: list) -> str:
        """LLM executive synthesis for the daily PDF: takeaway + so-what + most
        impactful findings. Best-effort — returns '' on any failure so the PDF
        still generates. One LLM call per run."""
        if not all_insights:
            return ""
        try:
            from app.services.llm_router import call_pro
            from app.services.synthesizer import parse_synthesis

            ranked = sorted(all_insights, key=lambda r: _insight_rank(r[0]))[:40]
            listing = "\n".join(
                f"[{ins.id}] ({tgt.name}, {ins.sentiment}) {ins.topic}: "
                f"{(ins.what_they_said or '')[:200]}"
                for ins, _post, tgt in ranked
            )
            prompt = (
                "You are a senior pharma intelligence analyst for Roche France.\n"
                f"Below are this week's {len(ranked)} most important KOL findings "
                "(each prefixed with its [id]).\n\n"
                f"{listing}\n\n"
                "Write a concise executive synthesis. Use EXACTLY this format with these markers:\n"
                "##TAKEAWAY##\n"
                "3-5 sentences: the key themes across your KOLs this week, notable shifts, "
                "drug or competitor mentions, sentiment.\n"
                "##SO_WHAT##\n"
                "2-3 sentences on what this means for Roche France and what to act on.\n"
                "##PICKS##\n"
                "The 3-5 most impactful findings. One per line, format: [id] one-sentence why it matters. "
                "Use the real [id] values above.\n\n"
                "Reference real drug names and KOLs. Be specific."
            )
            raw = call_pro([{"role": "user", "content": prompt}], max_tokens=4000)
            parsed = parse_synthesis(raw)
        except Exception as exc:
            logger.warning("pdf.synthesis_failed", error=str(exc))
            return ""

        if not parsed["takeaway"] and not parsed["picks"]:
            return ""

        ins_by_id = {ins.id: (ins, tgt) for ins, _post, tgt in all_insights}
        parts = ['<div class="synthesis"><h2>Synthesis &amp; takeaway</h2>']
        if parsed["takeaway"]:
            parts.append(f'<div class="lab">Takeaway</div><p>{_html.escape(parsed["takeaway"])}</p>')
        if parsed["so_what"]:
            parts.append(
                f'<div class="lab">So what for Roche?</div>'
                f'<div class="sowhat"><p>{_html.escape(parsed["so_what"])}</p></div>'
            )
        pick_lis = ""
        for pick in parsed["picks"][:5]:
            row = ins_by_id.get(pick["id"])
            if not row:
                continue
            ins, tgt = row
            pick_lis += (
                f'<li><b>{_html.escape(tgt.name)} — {_html.escape(ins.topic or "")}:</b> '
                f'{_html.escape(pick["why"])}</li>'
            )
        if pick_lis:
            parts.append(f'<div class="lab">Most impactful findings</div><ul>{pick_lis}</ul>')
        parts.append('</div>')
        return "".join(parts)
