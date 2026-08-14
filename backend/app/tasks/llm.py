"""LLM tasks — run on the 'llm' queue."""
import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.llm.extract_insights",
    queue="llm",
    max_retries=4,
    default_retry_delay=30,
    acks_late=True,
)
def extract_insights(self, post_id: int, run_id: int) -> dict:
    """Run LLM insight extraction on a single scraped post."""
    from app.services.extractor import ExtractorService
    from app.services.run_context import RunContext
    from app.tasks.utils import patch_run

    log = logger.bind(post_id=post_id, run_id=run_id, task_id=self.request.id)
    log.info("extract_insights.started")
    try:
        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        result = ExtractorService().extract(post_id=post_id, ctx=ctx)
        saved = result.get("insights_saved", 0)
        log.info("extract_insights.done", insights=saved)
        patch_run(run_id, **{"+insights_extracted": saved, "+llm_calls_used": 1})
        return result
    except Exception as exc:
        log.warning("extract_insights.retry", exc=str(exc), retries=self.request.retries)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    name="app.tasks.llm.extract_target_posts",
    queue="llm",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
    soft_time_limit=600,
    time_limit=700,
)
def extract_target_posts(self, target_id: int, run_id: int) -> dict:
    """Extract insights for all unprocessed posts of a target. Runs synchronously
    so generate_summary always sees completed insights."""
    import asyncio
    from app.services.extractor import ExtractorService
    from app.services.run_context import RunContext
    from app.tasks.utils import patch_run

    log = logger.bind(target_id=target_id, run_id=run_id, task_id=self.request.id)
    log.info("extract_target_posts.started")

    async def _get_unextracted_ids():
        from app.database import CelerySessionLocal
        from app.models import ScrapedPost, ExtractedInsight
        from sqlalchemy import select
        async with CelerySessionLocal() as sess:
            extracted_subq = select(ExtractedInsight.scraped_post_id).where(
                ExtractedInsight.target_id == target_id
            ).scalar_subquery()
            rows = await sess.execute(
                select(ScrapedPost.id)
                .where(ScrapedPost.target_id == target_id)
                .where(~ScrapedPost.id.in_(extracted_subq))
                .order_by(ScrapedPost.scraped_at.desc())
                .limit(25)
            )
            return rows.scalars().all()

    try:
        post_ids = asyncio.run(_get_unextracted_ids())
        if not post_ids:
            log.info("extract_target_posts.nothing_to_extract")
            return {"extracted": 0, "insights_saved": 0}

        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        extractor = ExtractorService()
        total_insights = 0
        for post_id in post_ids:
            result = extractor.extract(post_id=post_id, ctx=ctx)
            saved = result.get("insights_saved", 0)
            total_insights += saved
            patch_run(run_id, **{"+insights_extracted": saved, "+llm_calls_used": 1})

        log.info("extract_target_posts.done", posts=len(post_ids), insights=total_insights)
        return {"extracted": len(post_ids), "insights_saved": total_insights}
    except Exception as exc:
        log.warning("extract_target_posts.retry", exc=str(exc))
        raise self.retry(exc=exc)


# ── Global synthesis (dashboard) ──────────────────────────
# Merges the three EXISTING stored artifacts — latest KOL brief, latest
# social/all-population brief, latest done report per active burning topic —
# in ONE llm_router pass. Never re-reads raw posts and never re-generates the
# underlying briefs (cheap + fast by design). Result lives in Redis
# (global_synth:latest has no TTL — it's "the last synthesis" until replaced).

GLOBAL_SYNTH_STATUS_KEY = "global_synth:status"
GLOBAL_SYNTH_RESULT_KEY = "global_synth:latest"
_NO_DATA = "No data this period."


def _gs_redis():
    import redis as _redis
    from app.config import get_settings
    return _redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)


def set_global_synth_status(**fields) -> None:
    import json
    try:
        _gs_redis().set(GLOBAL_SYNTH_STATUS_KEY, json.dumps(fields), ex=7200)
    except Exception:
        pass


@celery_app.task(
    bind=True,
    name="app.tasks.llm.generate_global_synthesis",
    queue="llm",
    max_retries=0,
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def generate_global_synthesis(self) -> dict:
    import asyncio
    import json
    from datetime import datetime, timezone

    log = logger.bind(task_id=self.request.id)
    log.info("global_synthesis.started")
    set_global_synth_status(status="running", started_at=datetime.now(timezone.utc).isoformat())

    try:
        result = _build_global_synthesis()
        try:
            _gs_redis().set(GLOBAL_SYNTH_RESULT_KEY, json.dumps(result))
        except Exception:
            pass
        set_global_synth_status(status="done")
        log.info("global_synthesis.done", pdf=bool(result.get("pdf_url")))
        return {"status": "done"}
    except Exception as exc:
        set_global_synth_status(status="failed", error=str(exc)[:300])
        log.error("global_synthesis.failed", exc=str(exc)[:300])
        raise


def _points_text(brief: dict | None) -> str:
    if not brief or not brief.get("points"):
        return _NO_DATA
    return "\n".join(f"- {p.get('text', '')}" for p in brief["points"] if p.get("text"))


def _build_global_synthesis() -> dict:
    import asyncio
    import json
    from datetime import datetime, timezone

    from app.services.llm_router import call_llm
    from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete
    from app.tasks.burning_topics import _parse_picks_loose

    # 1) Stored briefs (Redis global cache keys written by the dashboard endpoints)
    kol_brief = social_brief = None
    try:
        r = _gs_redis()
        raw = r.get("kol_brief:v4")
        kol_brief = json.loads(raw) if raw else None
        raw = r.get("social_brief:v4")
        social_brief = json.loads(raw) if raw else None
    except Exception:
        pass

    # 2) Latest done report per ACTIVE burning topic (stored artifacts, no raw posts)
    async def _topic_reports():
        from sqlalchemy import desc, select
        from app.database import CelerySessionLocal
        from app.models import BurningTopic, BurningTopicReport
        async with CelerySessionLocal() as sess:
            rows = await sess.execute(
                select(BurningTopicReport, BurningTopic.name)
                .join(BurningTopic, BurningTopicReport.topic_id == BurningTopic.id)
                .where(BurningTopic.is_active.is_(True),
                       BurningTopicReport.status == "done")
                .distinct(BurningTopicReport.topic_id)
                .order_by(BurningTopicReport.topic_id, desc(BurningTopicReport.created_at))
            )
            out = []
            for report, name in rows.all():
                try:
                    posts = json.loads(report.important_posts or "[]")
                except (TypeError, ValueError):
                    posts = []
                out.append({
                    "topic": name,
                    "summary": report.summary_md or "",
                    "so_what": report.so_what or "",
                    "posts": posts if isinstance(posts, list) else [],
                })
            return out

    topic_reports = asyncio.run(_topic_reports())

    topics_text = "\n\n".join(
        f"TOPIC: {t['topic']}\nSUMMARY: {t['summary'][:800]}\nSO WHAT: {t['so_what'][:400]}"
        for t in topic_reports
    ) or _NO_DATA

    # Candidate posts pool = the already-curated important_posts from topic reports
    candidates = []
    for t in topic_reports:
        for p in t["posts"]:
            if isinstance(p, dict) and p.get("url"):
                candidates.append(p)
    candidates = candidates[:20]
    candidates_text = "\n".join(
        f"[{i}] {p.get('author') or '?'} | eng:{p.get('engagement', 0)} | {(p.get('title') or '')[:120]}"
        for i, p in enumerate(candidates, 1)
    ) or _NO_DATA

    prompt = (
        "You are the senior pharma intelligence lead for Roche France writing the GLOBAL "
        "synthesis that merges three existing report layers. Use ONLY the material below — "
        "never invent data; where a section says 'No data this period.', say exactly that "
        "in the corresponding output section.\n\n"
        f"LATEST KOL BRIEF:\n{_points_text(kol_brief)}\n\n"
        f"LATEST ALL-POPULATION (SOCIAL) BRIEF:\n{_points_text(social_brief)}\n\n"
        f"BURNING TOPIC REPORTS:\n{topics_text}\n\n"
        f"IMPORTANT POSTS POOL:\n{candidates_text}\n\n"
        "Be specific: name the person, company, drug, trial or congress. A sentence "
        "that could appear unchanged in last month's report is not worth writing.\n\n"
        "Output EXACTLY these sections with these markers and nothing else:\n"
        "##EXEC_SUMMARY##\n2-3 short paragraphs merging all three layers.\n"
        "##KOL_TAKEAWAYS##\n2-4 lines, one takeaway per line starting '- '.\n"
        "##POPULATION_TAKEAWAYS##\n2-4 lines, one takeaway per line starting '- '.\n"
        "##TOPIC_TAKEAWAYS##\n2-4 lines, one takeaway per line starting '- '.\n"
        "##SO_WHAT##\nOne paragraph on the strategic shift behind the findings — the "
        "implication, not a restatement.\n"
        "##RECOMMENDATIONS##\n3-5 lines starting '- '. Each must be an action a named "
        "team can own this month, beginning with a verb, and naming the finding that "
        "drives it.\n"
        "##IMPORTANT_POSTS##\nUp to 5 lines like '[3] why it matters' referencing the pool numbers "
        "(write 'No data this period.' if the pool is empty)."
    )

    raw = call_llm([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=4096)

    important = []
    for pick in _parse_picks_loose(extract_section(raw, "IMPORTANT_POSTS")):
        idx = pick["id"] - 1
        if 0 <= idx < len(candidates):
            item = dict(candidates[idx])
            item["why"] = pick["why"]
            important.append(item)

    now = datetime.now(timezone.utc)
    result = {
        "exec_summary": trim_incomplete(extract_section(raw, "EXEC_SUMMARY")),
        "kol_takeaways": parse_bullets(extract_section(raw, "KOL_TAKEAWAYS")),
        "population_takeaways": parse_bullets(extract_section(raw, "POPULATION_TAKEAWAYS")),
        "topic_takeaways": parse_bullets(extract_section(raw, "TOPIC_TAKEAWAYS")),
        "so_what": trim_incomplete(extract_section(raw, "SO_WHAT")),
        "recommendations": parse_bullets(extract_section(raw, "RECOMMENDATIONS")),
        "important_posts": important,
        "sections_present": {
            "kol": bool(kol_brief and kol_brief.get("points")),
            "population": bool(social_brief and social_brief.get("points")),
            "burning_topics": len(topic_reports),
        },
        "generated_at": now.isoformat(),
        "pdf_url": None,
    }

    result["pdf_url"] = _render_global_synthesis_pdf(result, now)
    return result


def _render_global_synthesis_pdf(result: dict, now) -> str | None:
    import html as _html
    from pathlib import Path

    from weasyprint import HTML
    from app.config import get_settings
    from app.services.pdf_generator import _BASE_CSS, _validate_pdf

    settings = get_settings()
    stamp = now.strftime("%Y-%m-%d_%H%M")
    today = now.date().isoformat()

    def _bullets(items: list[str]) -> str:
        lis = "".join(f"<li>{_html.escape(i)}</li>" for i in items)
        return f"<ul class='sum-list'>{lis}</ul>" if lis else "<div class='empty-card'>No data this period.</div>"

    posts_html = ""
    for p in result["important_posts"]:
        posts_html += (
            "<div class='recap-card'>"
            f"<div class='label'>{_html.escape(p.get('author') or '?')} · engagement {p.get('engagement', 0)}</div>"
            f"<div class='body'><strong>{_html.escape((p.get('title') or '')[:160])}</strong><br>"
            f"{_html.escape(p.get('why') or '')}<br><small>{_html.escape(p.get('url') or '')}</small></div></div>"
        )
    posts_html = posts_html or "<div class='empty-card'>No data this period.</div>"

    exec_html = _html.escape(result["exec_summary"] or "No data this period.").replace("\n", "<br>")
    so_what = _html.escape(result["so_what"] or "").replace("\n", "<br>")

    html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>{_BASE_CSS}</style></head><body>
<div class="header">
  <h1>PharmaRadar Global Synthesis</h1>
  <div class="subtitle">KOL + All-Population + Burning Topics</div>
  <div class="meta"><strong>Report Date:</strong> {today}</div>
</div>
<div class="section-title">Executive summary</div>
<div class="recap-card"><div class="body">{exec_html}</div></div>
<div class="section-title">KOL takeaways</div>{_bullets(result["kol_takeaways"])}
<div class="section-title">All-population takeaways</div>{_bullets(result["population_takeaways"])}
<div class="section-title">Burning-topic takeaways</div>{_bullets(result["topic_takeaways"])}
<div class="section-title">So what for pharma</div>
<div class="sowhat-card"><div class="body">{so_what or "<em>No analyst note.</em>"}</div></div>
<div class="section-title">Recommendations</div>{_bullets(result.get("recommendations") or [])}
<div class="section-title">Important posts</div>{posts_html}
<div class="footer">Generated by PharmaRadar &nbsp;·&nbsp; {today} &nbsp;·&nbsp; Confidential</div>
</body></html>"""

    out_dir = Path(settings.reports_dir) / "global_synthesis"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"Global_Synthesis_{stamp}.pdf"
    HTML(string=html_doc).write_pdf(str(pdf_path))
    _validate_pdf(pdf_path)

    if settings.vercel_blob_token:
        try:
            from app.services.vercel_blob_storage import upload_global_synthesis_pdf
            return upload_global_synthesis_pdf(pdf_path.read_bytes(), stamp, settings.vercel_blob_token)
        except Exception as exc:
            logger.warning("global_synthesis.blob_upload_failed", error=str(exc)[:200])
    # Blob-less dev: serve through the local fallback endpoint
    return f"/api/reports/local/global_synthesis/{pdf_path.name}"


@celery_app.task(
    bind=True,
    name="app.tasks.llm.generate_summary",
    queue="llm",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def generate_summary(self, target_id: int, run_id: int) -> dict:
    """Generate PersonSummary (bullets + so-what) for a target after extraction."""
    from app.services.extractor import ExtractorService
    from app.services.run_context import RunContext

    log = logger.bind(target_id=target_id, run_id=run_id, task_id=self.request.id)
    log.info("generate_summary.started")
    from app.tasks.utils import patch_run
    try:
        ctx = RunContext(run_id=run_id, task_id=self.request.id)
        result = ExtractorService().summarise(target_id=target_id, run_id=run_id, ctx=ctx)
        log.info("generate_summary.done")
        patch_run(run_id, **{"+llm_calls_used": 1})
        return result
    except Exception as exc:
        log.warning("generate_summary.retry", exc=str(exc))
        raise self.retry(exc=exc)
