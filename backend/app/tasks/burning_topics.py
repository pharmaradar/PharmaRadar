"""Shared Burning Topics and Congress report task.

Both report types use the same four phases: query stored posts, run one
TinyFish discovery search, synthesize through llm_router, and render/upload a
PDF. The report row is the stop/idempotency flag so Celery redelivery is safe.
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
_TEXT_SNIPPET = 400


# DB helpers: every Celery phase gets its own async session/event loop.
async def _load(report_id: int):
    from app.database import CelerySessionLocal
    from app.models import BurningTopic, BurningTopicReport, Congress

    async with CelerySessionLocal() as sess:
        report = await sess.get(BurningTopicReport, report_id)
        if not report:
            return None, None, None
        topic = await sess.get(BurningTopic, report.topic_id) if report.topic_id is not None else None
        congress = await sess.get(Congress, report.congress_id) if report.congress_id is not None else None
        return report, topic, congress


async def _set_fields(report_id: int, **fields) -> bool:
    from app.database import CelerySessionLocal
    from app.models import BurningTopicReport

    async with CelerySessionLocal() as sess:
        report = await sess.get(BurningTopicReport, report_id)
        if not report:
            return False
        for key, value in fields.items():
            setattr(report, key, value)
        await sess.commit()
        return True


def _aborted(report_id: int) -> bool:
    """Abort when a report is deleted or explicitly marked failed."""
    async def _check():
        report, _, _ = await _load(report_id)
        return report is None or report.status == "failed"

    try:
        return asyncio.run(_check())
    except Exception:
        return False


# Scope and query helpers -----------------------------------------------------
def _loads(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


async def _load_congress_questions(congress_id: int) -> list:
    from sqlalchemy import select
    from app.database import CelerySessionLocal
    from app.models import CongressQuestion

    async with CelerySessionLocal() as sess:
        rows = await sess.execute(
            select(CongressQuestion)
            .where(CongressQuestion.congress_id == congress_id)
            .order_by(CongressQuestion.created_at, CongressQuestion.id)
        )
        return list(rows.scalars().all())


def _topic_terms(topic) -> tuple[list[str], list[str]]:
    terms = [topic.name.strip()]
    terms.extend(item.strip() for item in _loads(topic.restriction_terms)
                 if isinstance(item, str) and item.strip())
    exclusions = [item.strip() for item in _loads(topic.exclusion_words)
                  if isinstance(item, str) and item.strip()]
    return list(dict.fromkeys(terms)), list(dict.fromkeys(exclusions))


def _congress_terms(congress) -> tuple[list[str], list[str]]:
    terms = [congress.name.strip()]
    for value in _loads(congress.hashtags):
        if not isinstance(value, str) or not value.strip():
            continue
        raw = value.strip()
        terms.append(raw)
        if raw.startswith("#") and len(raw) > 1:
            terms.append(raw[1:])
    return list(dict.fromkeys(term for term in terms if term)), []


def _scope_terms(topic=None, congress=None) -> tuple[list[str], list[str]]:
    if congress is not None:
        return _congress_terms(congress)
    return _topic_terms(topic)


def _scope_window(topic=None, congress=None, window_days: int | None = None) -> tuple[datetime, datetime]:
    """Report window. A congress has fixed dates; a topic uses `window_days` when
    the caller chose one for this run, otherwise the topic's own setting."""
    if congress is not None:
        start = datetime.combine(congress.start_date, datetime.min.time(), tzinfo=timezone.utc)
        end_date = congress.end_date + timedelta(days=1)
        end = datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc)
        return start, end
    end = datetime.now(timezone.utc)
    days = window_days or (topic.period_days if topic is not None else None) or 30
    return end - timedelta(days=days), end


def _match_any(terms: list[str], *columns):
    from sqlalchemy import func, or_

    conditions = []
    for term in terms:
        like = f"%{term.lower()}%"
        conditions.extend(
            func.lower(func.coalesce(column, "")).like(like) for column in columns
        )
    return or_(*conditions)


def _exclude(query, exclusions: list[str], *columns):
    from sqlalchemy import func

    for word in exclusions:
        like = f"%{word.lower()}%"
        for column in columns:
            query = query.where(~func.lower(func.coalesce(column, "")).like(like))
    return query


def _scraped_window_condition(congress, start: datetime, end: datetime):
    from sqlalchemy import and_, or_
    from app.models import ScrapedPost

    if congress is None:
        return ScrapedPost.scraped_at >= start

    # published_date is an ISO string in the existing table. Posts without a
    # publish date fall back to their scrape timestamp for congress windows.
    start_text = congress.start_date.isoformat()
    end_text = (congress.end_date + timedelta(days=1)).isoformat()
    return or_(
        and_(
            ScrapedPost.published_date.is_not(None),
            ScrapedPost.published_date >= start_text,
            ScrapedPost.published_date < end_text,
        ),
        and_(
            ScrapedPost.published_date.is_(None),
            ScrapedPost.scraped_at >= start,
            ScrapedPost.scraped_at < end,
        ),
    )


async def _gather_db_posts(topic=None, congress=None, window_days: int | None = None) -> list[dict]:
    """Gather matching KOL and social rows for the selected report scope."""
    from sqlalchemy import desc, func, select
    from app.database import CelerySessionLocal
    from app.models import ScrapedPost, SocialPost, Target

    terms, exclusions = _scope_terms(topic, congress)
    start, end = _scope_window(topic, congress, window_days)
    candidates: list[dict] = []

    async with CelerySessionLocal() as sess:
        from app.services.ae_filter import post_not_ae, social_not_ae
        kol_query = (
            select(ScrapedPost, Target.name)
            .join(Target, ScrapedPost.target_id == Target.id)
            .where(_scraped_window_condition(congress, start, end))
            .where(_match_any(terms, ScrapedPost.raw_content, ScrapedPost.title))
            .where(post_not_ae())
        )
        kol_query = _exclude(kol_query, exclusions, ScrapedPost.raw_content, ScrapedPost.title)
        kol_query = kol_query.order_by(desc(ScrapedPost.scraped_at)).limit(_MAX_KOL_POSTS)
        for post, target_name in (await sess.execute(kol_query)).all():
            candidates.append({
                "kind": "kol",
                "platform": post.source_type or "web",
                "author": target_name,
                "url": post.source_url,
                "title": post.title,
                "text": (post.raw_content or "")[:_TEXT_SNIPPET],
                "date": post.published_date or (
                    post.scraped_at.date().isoformat() if post.scraped_at else ""
                ),
                "engagement": (post.likes or 0) + (post.views or 0),
            })

        engagement = (
            func.coalesce(SocialPost.likes, 0)
            + func.coalesce(SocialPost.comments, 0)
            + func.coalesce(SocialPost.shares, 0)
        )
        social_date = func.coalesce(SocialPost.posted_at, SocialPost.scraped_at)
        social_query = (
            select(SocialPost)
            .where(social_date >= start)
            .where(social_date < end)
            .where(_match_any(terms, SocialPost.text, SocialPost.topic, SocialPost.hashtags))
            .where(social_not_ae())
        )
        if topic is not None and topic.language_filter:
            social_query = social_query.where(SocialPost.language == topic.language_filter)
        social_query = _exclude(
            social_query, exclusions, SocialPost.text, SocialPost.topic, SocialPost.hashtags
        )
        social_query = social_query.order_by(desc(engagement), desc(SocialPost.scraped_at)).limit(
            _MAX_SOCIAL_POSTS
        )
        for post in (await sess.execute(social_query)).scalars().all():
            posted = post.posted_at or post.scraped_at
            candidates.append({
                "kind": "social",
                "platform": post.platform,
                "author": post.author,
                "url": post.post_url,
                "title": None,
                "text": (post.text or "")[:_TEXT_SNIPPET],
                "date": posted.date().isoformat() if posted else "",
                "engagement": (post.likes or 0) + (post.comments or 0) + (post.shares or 0),
            })

    return candidates


# Discovery -------------------------------------------------------------------
def _scope_search_query(topic=None, congress=None) -> str:
    if congress is not None:
        tags = [value for value in _loads(congress.hashtags) if isinstance(value, str) and value.strip()]
        return " ".join([congress.name.strip(), *tags])
    return topic.name.strip()


def _web_context(topic=None, exclusions: list[str] | None = None, congress=None) -> list[dict]:
    """Run one best-effort TinyFish discovery search for the report scope.

    Burning topics are the client's own French-market topics, so their web
    context is searched at the French locale. Congresses are the deliberate
    exception: ASCO/ESMO/AACR are international events, and pinning them to
    French sources returns nothing — the report would still be written and
    marked done, just empty. See services/fr_sources.Scope.
    """
    try:
        from app.services.fr_sources import Scope
        from app.services.scraper import _tf_search_discovery
        scope = Scope.GLOBAL.value if congress is not None else Scope.FR.value
        hits = _tf_search_discovery(_scope_search_query(topic, congress), scope=scope) or []
    except Exception as exc:
        logger.warning("report.tinyfish_failed", error=str(exc)[:200])
        return []

    exclusions = exclusions or []
    output: list[dict] = []
    for hit in hits:
        url = hit.get("url") or ""
        title = hit.get("title") or ""
        snippet = hit.get("snippet") or ""
        if not url:
            continue
        searchable = f"{title} {snippet}".lower()
        if any(word.lower() in searchable for word in exclusions):
            continue
        output.append({
            "kind": "web",
            "platform": "web",
            "author": None,
            "url": url,
            "title": title or url,
            "text": snippet[:_TEXT_SNIPPET],
            "date": "",
            "engagement": 0,
        })
        if len(output) >= _MAX_WEB_RESULTS:
            break
    return output


# Synthesis -------------------------------------------------------------------
def _compute_main_authors(candidates: list[dict]) -> list[dict]:
    stats: dict[str, dict] = {}
    pool = [item for item in candidates if item["kind"] in ("kol", "social")] or candidates
    for item in pool:
        author = (item.get("author") or "").strip()
        if not author:
            continue
        key = author.lower()
        current = stats.setdefault(key, {
            "author": author, "posts": 0, "engagement": 0, "platforms": set()
        })
        current["posts"] += 1
        current["engagement"] += int(item.get("engagement") or 0)
        current["platforms"].add(item.get("platform") or "web")
    ranked = sorted(stats.values(), key=lambda item: (-item["posts"], -item["engagement"]))[:8]
    for item in ranked:
        item["platforms"] = sorted(item["platforms"])
        item["note"] = None
    return ranked


_PICK_ANY_RE = re.compile(r"\[(\d+)\]\s*(.+)")


def _parse_picks_loose(text: str) -> list[dict]:
    picks = []
    for line in (text or "").splitlines():
        match = _PICK_ANY_RE.search(line)
        if match:
            picks.append({"id": int(match.group(1)), "why": match.group(2).strip()})
    return picks


def _extract_numbered_section(raw: str, name: str) -> str:
    """Parse dynamic QUESTION_1-style markers; shared parser allows letters only."""
    match = re.search(
        rf"##{re.escape(name)}##\s*(.*?)(?=##[A-Z0-9_]+##|$)",
        raw or "",
        re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _voice_and_volume(candidates: list[dict]) -> tuple[dict, dict]:
    """Count who is speaking and how much, from the rows themselves.

    Asking the model for these numbers produces confident figures with nothing
    behind them, so they are computed and handed to it instead. `kind == "kol"`
    is exact: those rows come from a ScrapedPost joined to a tracked Target.
    See services/voice_profile for what the other buckets can and cannot know.
    """
    from app.services.market_report import compute_volume
    from app.services.voice_profile import build_breakdown

    voices = build_breakdown([
        {
            "author": item.get("author"),
            "url": item.get("url") or "",
            "is_tracked_kol": item.get("kind") == "kol",
            "target_type": "kol" if item.get("kind") == "kol" else None,
        }
        for item in candidates
    ])
    dated = []
    for item in candidates:
        stamp = None
        raw = (item.get("date") or "").strip()
        if raw:
            try:
                stamp = datetime.fromisoformat(raw[:10]).replace(tzinfo=timezone.utc)
            except ValueError:
                stamp = None
        dated.append({
            "kind": item.get("kind") or "item",
            "at": stamp,
            "engagement": item.get("engagement") or 0,
        })
    return voices, compute_volume(dated, 0)


def _synthesize(topic, candidates: list[dict], congress=None,
                window_days: int | None = None) -> dict:
    from app.services.llm_router import call_llm
    from app.services.synthesizer import extract_section, parse_bullets, trim_incomplete

    voices, volume = _voice_and_volume(candidates)
    voice_line = ", ".join(
        f"{row['label']}: {row['mentions']} ({row['percent']}%)" for row in voices.as_rows()
    ) or "no attributable voices"
    volume_line = (
        f"{volume['total']} mentions ("
        + ", ".join(f"{k}: {v}" for k, v in volume["by_kind"].items())
        + f"); {volume['dated']} carry a usable date ({volume['date_coverage']}% coverage)"
    )
    computed = (
        "\n\nTwo sections are already COMPUTED from the underlying rows — interpret "
        "them, do not recount or contradict them:\n"
        f"  VOICE DISTRIBUTION: {voice_line}\n"
        f"  VOLUME: {volume_line}\n"
    )

    lines = []
    for index, item in enumerate(candidates, 1):
        head = (
            f"[{index}] ({item['kind']}/{item['platform']}) {item.get('author') or '?'} "
            f"| {item['date'] or '?'} | eng:{item['engagement']}"
        )
        body = f"{(item.get('title') or '')[:120]} - {item['text']}".strip(" -")
        lines.append(f"{head}\n    {body}")

    if congress is None:
        lang_note = (
            f" Prefer {topic.language_filter}-language content and note when findings are "
            "language-specific."
            if topic.language_filter else ""
        )
        system = (
            "You are a pharma intelligence analyst for Roche France writing a burning-topic "
            "report. Base everything strictly on the numbered posts provided; never invent "
            f"posts, authors or numbers.{lang_note} Output EXACTLY these sections with "
            "these markers and nothing else:\n"
            "##SUMMARY##\n2-3 short paragraphs on what is happening around this topic.\n"
            "##KEY_FINDINGS##\n5-8 lines, one specific finding per line, starting with '- '.\n"
            "##SO_WHAT##\nOne paragraph: implications and recommended focus for pharma/Roche.\n"
            "##WHAT_IS_SAID##\n4-6 paragraphs on the substance of the conversation: the "
            "positions taken, where they agree and diverge, the arguments used, the tone.\n"
            "##VOICES##\n2-3 paragraphs interpreting the voice distribution given above: "
            "who drives this conversation, who is absent, and what that imbalance means.\n"
            "##VOLUME##\n1-2 paragraphs interpreting the volume figures given above, "
            "including the direction of travel and any caveat about date coverage.\n"
            "##SUBTOPICS##\n4-6 lines starting '- ': sub-topics worth tracking next, with why.\n"
            "##IMPORTANT_POSTS##\n5-8 lines like '[12] one line on why this post matters'.\n"
            "##MAIN_AUTHORS##\nOne line per notable author: '- Author Name - their role in this conversation'."
        )
        user = (
            f"TOPIC: {topic.name}\nDESCRIPTION: {topic.description or '-'}\n"
            f"PERIOD: last {window_days or topic.period_days} days\n\nPOSTS/ARTICLES ({len(candidates)}):\n"
            + computed
            + "\n".join(lines)
        )
        questions = []
    else:
        # Questions are loaded explicitly so the task does not rely on lazy
        # relationships after the session that loaded the Congress is closed.
        questions = asyncio.run(_load_congress_questions(congress.id))
        question_markers = "\n".join(
            f"##QUESTION_{index}##\nOne direct answer to: {question.question_text}"
            for index, question in enumerate(questions, 1)
        )
        system = (
            "You are a pharma intelligence analyst writing a congress monitoring report. "
            "Base every answer strictly on the numbered posts and articles provided; never "
            "invent posts, authors, dates or numbers. Answer every configured question in "
            "its own marker section. Then provide the standard report sections. If the data "
            "does not support an answer, say that directly. Output EXACTLY these sections and "
            "nothing else:\n"
            f"{question_markers}\n"
            "##SUMMARY##\n2-3 short paragraphs of the main learnings.\n"
            "##KEY_FINDINGS##\n5-8 specific learnings, one per line starting '- '.\n"
            "##SO_WHAT##\nOne paragraph explaining implications for pharma/Roche.\n"
            "##WHAT_IS_SAID##\n4-6 paragraphs on the substance of the conversation: the "
            "positions taken, where they agree and diverge, the arguments used, the tone.\n"
            "##VOICES##\n2-3 paragraphs interpreting the voice distribution given above: "
            "who drives this conversation, who is absent, and what that imbalance means.\n"
            "##VOLUME##\n1-2 paragraphs interpreting the volume figures given above, "
            "including the direction of travel and any caveat about date coverage.\n"
            "##SUBTOPICS##\n4-6 lines starting '- ': sub-topics worth tracking next, with why.\n"
            "##IMPORTANT_POSTS##\n5-8 lines like '[12] why this post or article matters'.\n"
            "##MAIN_AUTHORS##\nOne line per notable author: '- Author Name - their role in this conversation'."
        )
        question_text = "\n".join(
            f"[{index}] {question.question_text}" for index, question in enumerate(questions, 1)
        ) or "No questions configured."
        user = (
            f"CONGRESS: {congress.name}\n"
            f"DATE WINDOW: {congress.start_date.isoformat()} to {congress.end_date.isoformat()}\n"
            f"DISEASE AREA: {congress.disease_area or '-'}\n\n"
            f"CONFIGURED QUESTIONS:\n{question_text}\n"
            + computed
            + f"\nPOSTS/ARTICLES ({len(candidates)}):\n" + "\n".join(lines)
        )

    raw = call_llm(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3,
        # More sections now, and gemini-2.5-flash spends this same budget on
        # reasoning — 4096 truncated the tail sections.
        max_tokens=8192,
    )

    key_findings = parse_bullets(extract_section(raw, "KEY_FINDINGS"))
    important_posts = []
    for pick in _parse_picks_loose(extract_section(raw, "IMPORTANT_POSTS")):
        index = pick["id"] - 1
        if 0 <= index < len(candidates):
            item = candidates[index]
            important_posts.append({
                "url": item["url"],
                "title": item.get("title") or (item["text"][:100] if item["text"] else item["url"]),
                "author": item.get("author"),
                "engagement": item["engagement"],
                "platform": item["platform"],
                "kind": item["kind"],
                "why": pick["why"],
            })

    main_authors = _compute_main_authors(candidates)
    for note_line in parse_bullets(extract_section(raw, "MAIN_AUTHORS")):
        # LLMs alternate between "Name — note", "Name – note" and "Name - note".
        # Split only on a SPACED separator so hyphenated author names
        # ("Jean-Pierre") never get chopped mid-name.
        name, note = note_line, ""
        for sep in (" — ", " – ", " - "):
            if sep in note_line:
                name, note = note_line.split(sep, 1)
                break
        name = name.strip().lower()
        if not name:
            continue
        for author in main_authors:
            if name.startswith(author["author"].lower()) or author["author"].lower() in name:
                author["note"] = author["note"] or note.strip() or None
                break

    question_answers = []
    for index, question in enumerate(questions, 1):
        question_answers.append({
            "question_id": question.id,
            "question": question.question_text,
            "answer": trim_incomplete(_extract_numbered_section(raw, f"QUESTION_{index}")),
        })

    return {
        "summary_md": trim_incomplete(extract_section(raw, "SUMMARY")),
        "key_findings": key_findings,
        "so_what": trim_incomplete(extract_section(raw, "SO_WHAT")),
        "important_posts": important_posts,
        "main_authors": main_authors,
        "question_answers": question_answers,
        # Market-research sections. The prose comes from the model; the voice and
        # volume figures are the ones computed above and merely interpreted by it.
        "what_is_said": trim_incomplete(extract_section(raw, "WHAT_IS_SAID")),
        "voices_note": trim_incomplete(extract_section(raw, "VOICES")),
        "volume_note": trim_incomplete(extract_section(raw, "VOLUME")),
        "subtopics": parse_bullets(extract_section(raw, "SUBTOPICS")),
        "voice_rows": voices.as_rows(),
        "voice_exact_share": round(voices.exact_share * 100),
        "volume": volume,
        "item_count": len(candidates),
    }


# PDF -------------------------------------------------------------------------
def _pdf_local_path(topic, congress, report_id: int):
    from pathlib import Path
    from app.config import get_settings

    if congress is not None:
        output_dir = Path(get_settings().reports_dir) / "congresses"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"congress_{congress.id}_report_{report_id}.pdf"
    output_dir = Path(get_settings().reports_dir) / "burning_topics"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"topic_{topic.id}_report_{report_id}.pdf"


def _summary_to_html(md: str) -> str:
    import html as html_module

    text = html_module.escape(md or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    paragraphs = [part.strip().replace("\n", "<br>") for part in text.split("\n\n") if part.strip()]
    return "".join(f"<p>{part}</p>" for part in paragraphs)


def _render_pdf(topic, report_id: int, fields: dict, congress=None) -> str | None:
    import html as html_module
    from weasyprint import HTML
    from app.config import get_settings
    from app.services.pdf_generator import _BASE_CSS, _validate_pdf

    settings = get_settings()
    today = datetime.now(timezone.utc).date().isoformat()
    subject_name = congress.name if congress is not None else topic.name
    period = (
        f"{congress.start_date.isoformat()} to {congress.end_date.isoformat()}"
        if congress is not None else f"last {topic.period_days} days"
    )

    findings_html = "".join(
        f"<li>{html_module.escape(finding)}</li>" for finding in fields["key_findings"]
    )
    findings_html = (
        f"<ul class='sum-list'>{findings_html}</ul>"
        if findings_html else "<div class='empty-card'>No key findings.</div>"
    )

    question_html = ""
    for item in fields.get("question_answers", []):
        question_html += (
            "<div class='recap-card'><div class='label'>"
            f"{html_module.escape(item.get('question') or 'Question')}"
            "</div><div class='body'>"
            f"{html_module.escape(item.get('answer') or 'No answer recorded.')}</div></div>"
        )
    question_html = question_html or "<div class='empty-card'>No questions configured.</div>"

    posts_html = ""
    for post in fields["important_posts"]:
        label = (
            f"{post.get('author') or '?'} - {post.get('platform') or ''} - "
            f"engagement {post.get('engagement', 0)}"
        )
        posts_html += (
            "<div class='recap-card'>"
            f"<div class='label'>{html_module.escape(label)}</div>"
            f"<div class='body'><strong>{html_module.escape((post.get('title') or '')[:160])}</strong><br>"
            f"{html_module.escape(post.get('why') or '')}<br>"
            f"<small>{html_module.escape(post.get('url') or '')}</small></div></div>"
        )
    posts_html = posts_html or "<div class='empty-card'>No highlighted posts or articles.</div>"

    authors_html = ""
    for author in fields["main_authors"]:
        note = f" - {html_module.escape(author['note'])}" if author.get("note") else ""
        authors_html += (
            f"<li><strong>{html_module.escape(author['author'])}</strong> "
            f"({author['posts']} post(s), engagement {author['engagement']}, "
            f"{html_module.escape(', '.join(author['platforms']))}){note}</li>"
        )
    authors_html = (
        f"<ul class='sum-list'>{authors_html}</ul>"
        if authors_html else "<div class='empty-card'>No recurring authors identified.</div>"
    )

    so_what = html_module.escape(fields["so_what"] or "").replace("\n", "<br>")
    questions_section = (
        f"<div class='section-title'>Congress questions</div>{question_html}"
        if congress is not None else ""
    )

    def _prose(text: str) -> str:
        blocks = [b.strip() for b in (text or "").split("\n") if b.strip()]
        return "".join(
            f"<div class='body' style='margin-bottom:8px'>{html_module.escape(b)}</div>"
            for b in blocks
        ) or "<div class='empty-card'>Not enough material for this section.</div>"

    voice_rows = fields.get("voice_rows") or []
    if voice_rows:
        bars = "".join(
            f"<tr><td style='font-size:11px;padding:3px 6px;width:34%'>"
            f"{html_module.escape(row['label'])}</td>"
            f"<td style='width:46%;padding:3px 6px'><div style='background:#1f4eaa;height:12px;"
            f"border-radius:2px;width:{max(row['percent'], 2)}%'></div></td>"
            f"<td style='font-size:11px;padding:3px 6px'><b>{row['mentions']}</b> "
            f"({row['percent']}%)</td></tr>"
            for row in voice_rows
        )
        voices_html = (
            f"<table style='border-collapse:collapse;width:100%'>{bars}</table>"
            f"<div class='empty-card' style='border-left-color:#e0a800'>"
            f"{fields.get('voice_exact_share', 0)}% of these voices are identified from "
            "tracked records (KOL targets, curated sources). The rest is inferred from the "
            "author name and should be read as indicative.</div>"
        )
    else:
        voices_html = "<div class='empty-card'>No attributable voices in this material.</div>"

    volume = fields.get("volume") or {}
    if volume.get("total"):
        kinds = "".join(
            f"<tr><td style='font-size:11px;padding:3px 6px;width:60%'>"
            f"{html_module.escape(str(k))}</td><td style='font-size:11px'><b>{v}</b></td></tr>"
            for k, v in (volume.get("by_kind") or {}).items()
        )
        coverage = volume.get("date_coverage", 0)
        note = (
            f"<div class='empty-card' style='border-left-color:#e0a800'>"
            f"{volume.get('dated', 0)} of {volume.get('total', 0)} mentions carry a usable "
            f"date ({coverage}%). Any trend covers only that subset.</div>"
            if coverage < 100 else ""
        )
        volume_html = f"<table style='border-collapse:collapse;width:100%'>{kinds}</table>{note}"
    else:
        volume_html = "<div class='empty-card'>No mentions in this window.</div>"

    subtopics = fields.get("subtopics") or []
    subtopics_html = (
        "<ul class='sum-list'>"
        + "".join(f"<li>{html_module.escape(item)}</li>" for item in subtopics)
        + "</ul>"
        if subtopics else "<div class='empty-card'>None identified.</div>"
    )
    html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>{_BASE_CSS}</style></head><body>
<div class="header">
  <h1>PharmaRadar {'Congress' if congress is not None else 'Burning Topic'} Report</h1>
  <div class="subtitle">Pharma Intelligence Monitoring System</div>
  <div class="meta"><strong>Subject:</strong> {html_module.escape(subject_name)}<br>
    <strong>Period:</strong> {html_module.escape(period)}<br>
    <strong>Report Date:</strong> {today}</div>
</div>
{questions_section}
<div class="section-title">Main learnings</div>
<div class="recap-card"><div class="body">{_summary_to_html(fields['summary_md']) or '<em>No summary.</em>'}</div></div>
<div class="section-title">Key findings</div>{findings_html}
<div class="section-title">So what for pharma</div>
<div class="sowhat-card"><div class="body">{so_what or '<em>No analyst note.</em>'}</div></div>
<div class="section-title">What is being said</div>{_prose(fields.get('what_is_said'))}
<div class="section-title">Voice distribution</div>{voices_html}
{_prose(fields.get('voices_note')) if fields.get('voices_note') else ''}
<div class="section-title">Volume of mentions</div>{volume_html}
{_prose(fields.get('volume_note')) if fields.get('volume_note') else ''}
<div class="section-title">Key sub-topics to consider</div>{subtopics_html}
<div class="section-title">Posts and articles</div>{posts_html}
<div class="section-title">Main authors</div>{authors_html}
<div class="footer">Generated by PharmaRadar - {today} - Confidential</div>
</body></html>"""

    pdf_path = _pdf_local_path(topic, congress, report_id)
    HTML(string=html_doc).write_pdf(str(pdf_path))
    _validate_pdf(pdf_path)

    if settings.vercel_blob_token:
        try:
            from app.services.vercel_blob_storage import upload_burning_topic_pdf
            name = congress.name if congress is not None else topic.name
            prefix = "congress" if congress is not None else "topic"
            slug = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:60] or prefix
            return upload_burning_topic_pdf(
                pdf_binary=pdf_path.read_bytes(),
                topic_slug=f"{prefix}_{congress.id if congress is not None else topic.id}_{slug}",
                report_id=report_id,
                vercel_token=settings.vercel_blob_token,
            )
        except Exception as exc:
            logger.warning("report.blob_upload_failed", report_id=report_id, error=str(exc)[:200])
    return None


# Task ------------------------------------------------------------------------
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
    log.info("report.started")

    report, topic, congress = asyncio.run(_load(report_id))
    if not report or (not topic and not congress):
        log.warning("report.missing_row")
        return {"skipped": "report_or_scope_missing"}
    if report.status in ("done", "failed"):
        # done → acks_late redelivery of finished work; failed → the stale-report
        # reaper (or a manual cancel) already resolved this row while the message
        # sat in the queue — running it now would zombie-complete a report the
        # user was told to regenerate.
        return {"skipped": f"already_{report.status}"}

    asyncio.run(_set_fields(report_id, status="running"))

    try:
        _, exclusions = _scope_terms(topic, congress)
        window_days = getattr(report, "window_days", None) or None
        candidates = asyncio.run(_gather_db_posts(topic, congress, window_days))
        log.info("report.db_posts", count=len(candidates), congress=bool(congress))
        if _aborted(report_id):
            return {"aborted": "after_db_query"}

        web = _web_context(topic, exclusions, congress)
        candidates += web
        log.info("report.web_results", count=len(web), congress=bool(congress))
        if _aborted(report_id):
            return {"aborted": "after_web_search"}

        if not candidates:
            name = congress.name if congress is not None else topic.name
            period = (
                f"{congress.start_date.isoformat()} to {congress.end_date.isoformat()}"
                if congress is not None else f"the last {topic.period_days} days"
            )
            question_answers = [
                {
                    "question_id": question.id,
                    "question": question.question_text,
                    "answer": "No matching posts or articles were found in the configured date window.",
                }
                for question in asyncio.run(_load_congress_questions(congress.id))
            ] if congress is not None else []
            asyncio.run(_set_fields(
                report_id,
                status="done",
                summary_md=f"No posts or articles matching '{name}' were found in {period}. "
                           "Try running a scrape first or widening the configured date window.",
                key_findings="[]",
                so_what=None,
                important_posts="[]",
                main_authors="[]",
                question_answers=json.dumps(question_answers),
                what_is_said=None,
                voices_note=None,
                volume_note=None,
                subtopics="[]",
                voice_rows="[]",
                volume="{}",
                item_count=0,
                voice_exact_share=0,
            ))
            return {"status": "done", "candidates": 0}

        fields = _synthesize(topic, candidates, congress, window_days)
        if _aborted(report_id):
            return {"aborted": "after_synthesis"}

        pdf_url = _render_pdf(topic, report_id, fields, congress)
        asyncio.run(_set_fields(
            report_id,
            status="done",
            summary_md=fields["summary_md"],
            key_findings=json.dumps(fields["key_findings"]),
            so_what=fields["so_what"],
            important_posts=json.dumps(fields["important_posts"]),
            main_authors=json.dumps(fields["main_authors"]),
            question_answers=json.dumps(fields["question_answers"]),
            what_is_said=fields["what_is_said"],
            voices_note=fields["voices_note"],
            volume_note=fields["volume_note"],
            subtopics=json.dumps(fields["subtopics"]),
            voice_rows=json.dumps(fields["voice_rows"]),
            volume=json.dumps(fields["volume"]),
            item_count=fields["item_count"],
            voice_exact_share=fields["voice_exact_share"],
            window_days=(window_days or (topic.period_days if topic is not None else 0)),
            pdf_url=pdf_url,
        ))
        log.info("report.done", candidates=len(candidates), pdf=bool(pdf_url), congress=bool(congress))
        return {"status": "done", "candidates": len(candidates), "pdf_url": pdf_url}

    except SoftTimeLimitExceeded:
        asyncio.run(_set_fields(report_id, status="failed", summary_md="Report generation timed out."))
        log.warning("report.soft_timeout")
        return {"status": "failed", "reason": "timeout"}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            log.warning("report.retry", error=str(exc)[:300])
            raise self.retry(exc=exc)
        asyncio.run(_set_fields(
            report_id, status="failed", summary_md=f"Report generation failed: {str(exc)[:400]}"
        ))
        log.error("report.failed", error=str(exc)[:300])
        raise

