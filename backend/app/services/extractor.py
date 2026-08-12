"""LLM extraction service: post content → structured insights."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import structlog

from app.services.llm_router import call_pro
from app.services.run_context import RunContext

logger = structlog.get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    m = re.search(r'```(?:json)?\s*\n(.*?)\n?```', s, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: if response starts with { or [ it's already bare JSON
    return s


def _call_json(messages: list[dict], *, max_tokens: int, log_event: str, **log_ctx):
    """call_pro + json.loads with ONE retry.

    gemini-2.5-flash is a thinking model — reasoning tokens come out of the same
    max_tokens budget, so a long prompt can leave too little room and the JSON
    gets truncated mid-structure. That failed silently before: the caller logged
    and returned, the Celery task still reported 'succeeded', and the PDF shipped
    with an empty summary. Observed 2026-08-08 on the insight-rich KOLs
    (ZALCMAN 40 / SCHERPEREEL 29 failed; PUJOL 10 was fine).
    Returns the parsed dict, or None if both attempts fail.
    """
    for attempt in (1, 2):
        try:
            raw = call_pro(messages, max_tokens=max_tokens)
        except Exception as exc:
            logger.warning(f"{log_event}.llm_failed", attempt=attempt, exc=str(exc), **log_ctx)
            if attempt == 2:
                raise
            continue
        try:
            return json.loads(_strip_fences(raw))
        except json.JSONDecodeError:
            logger.warning(f"{log_event}.json_parse_failed", attempt=attempt,
                           raw=raw[:200], **log_ctx)
    return None


class ExtractorService:
    def extract(self, post_id: int, ctx: RunContext) -> dict:
        return asyncio.run(self._extract_async(post_id, ctx))

    async def _extract_async(self, post_id: int, ctx: RunContext) -> dict:
        from app.database import CelerySessionLocal
        from app.models import ScrapedPost, ExtractedInsight, Target

        ctx.increment_llm_calls()

        async with CelerySessionLocal() as sess:
            post = await sess.get(ScrapedPost, post_id)
            if not post or not post.raw_content:
                return {"error": "no_content"}

            # Fetch target name so the LLM knows who it's analysing
            target = await sess.get(Target, post.target_id)
            target_name = target.name if target else f"Target {post.target_id}"

            content = post.raw_content[:12000]

        # Substitute {name} in prompt so LLM has full attribution context
        system_prompt = _load_prompt("extract.txt").replace("{name}", target_name)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Person: {target_name}\n\nContent:\n{content}"},
        ]

        try:
            parsed = _call_json(messages, max_tokens=8192,
                                log_event="extractor", post_id=post_id)
        except Exception as exc:
            return {"insights_saved": 0, "error": str(exc)}
        if parsed is None:
            return {"insights_saved": 0, "error": "json_parse_failed"}

        meta = parsed.get("post_metadata", {})
        ae = parsed.get("adverse_event") or {}

        async with CelerySessionLocal() as sess:
            post = await sess.get(ScrapedPost, post_id)
            if post:
                if meta.get("published_date"):
                    post.published_date = meta["published_date"]
                if meta.get("title"):
                    post.title = meta["title"]
                if meta.get("source_name"):
                    post.source_name = meta["source_name"]

                # AE classification rides the same LLM call (cost matters).
                # Only write a definite bool — a missing/malformed block leaves
                # the post NULL for the backfill sweep to classify later.
                if isinstance(ae.get("is_adverse_event"), bool):
                    post.is_adverse_event = ae["is_adverse_event"]
                    post.ae_reason = (ae.get("ae_reason") or None) if ae["is_adverse_event"] else None

                insights_saved = 0
                new_insights = []
                for item in parsed.get("insights", []):
                    ins = ExtractedInsight(
                        scraped_post_id=post.id,
                        target_id=post.target_id,
                        topic=item.get("topic"),
                        context=item.get("context"),
                        what_they_said=item.get("what_they_said"),
                        sentiment=item.get("sentiment"),
                        category=item.get("category"),
                        window_tag="primary",
                    )
                    sess.add(ins)
                    new_insights.append(ins)
                    insights_saved += 1

                await sess.commit()

                # Generate embeddings for new insights (non-blocking)
                if new_insights:
                    try:
                        from app.services.embedder import embed_texts
                        texts = [
                            f"{i.topic}: {i.what_they_said or ''}"
                            for i in new_insights
                        ]
                        loop = asyncio.get_event_loop()
                        embeddings = await loop.run_in_executor(None, embed_texts, texts)
                        for ins, emb in zip(new_insights, embeddings):
                            if emb:
                                ins.embedding = emb
                        await sess.commit()
                    except Exception as emb_exc:
                        logger.warning("extractor.embed_failed", exc=str(emb_exc))

        logger.info("extractor.done", post_id=post_id, target=target_name, insights=insights_saved)
        return {"insights_saved": insights_saved}

    def summarise(self, target_id: int, run_id: int, ctx: RunContext) -> dict:
        return asyncio.run(self._summarise_async(target_id, run_id, ctx))

    async def _summarise_async(self, target_id: int, run_id: int, ctx: RunContext) -> dict:
        from app.database import CelerySessionLocal
        from app.models import ExtractedInsight, PersonSummary, Target
        from sqlalchemy import select

        ctx.increment_llm_calls()

        async with CelerySessionLocal() as sess:
            target = await sess.get(Target, target_id)
            target_name = target.name if target else f"Target {target_id}"

            from app.services.ae_filter import insight_not_ae
            rows = await sess.execute(
                select(ExtractedInsight)
                .where(ExtractedInsight.target_id == target_id)
                .where(insight_not_ae())
                .order_by(ExtractedInsight.extracted_at.desc())
                # 25, not 50: the prompt grows with this and the reply has to fit
                # in max_tokens alongside the model's reasoning tokens. Bullets are
                # capped well below 25 anyway, so the extra findings only crowded
                # out the answer. See _call_json.
                .limit(25)
            )
            insights = rows.scalars().all()

        if not insights:
            return {"bullets": 0, "so_what_saved": False}

        # Number each finding so the LLM can cite refs (matches v1 prompt format)
        numbered_findings = "\n\n".join(
            f"[{i+1}] TOPIC: {ins.topic}\n"
            f"CONTEXT: {ins.context}\n"
            f"STATEMENT: {ins.what_they_said}\n"
            f"SENTIMENT: {ins.sentiment}"
            for i, ins in enumerate(insights)
        )

        raw_prompt = _load_prompt("summarize.txt")
        system_prompt = raw_prompt.replace("{name}", target_name).replace("{findings_block}", numbered_findings)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Summarise {target_name}'s pharma intelligence profile based on the {len(insights)} findings above."},
        ]

        try:
            parsed = _call_json(messages, max_tokens=8192,
                                log_event="summarise", target_id=target_id)
        except Exception as exc:
            return {"error": str(exc)}
        if parsed is None:
            return {"error": "json_parse_failed"}

        bullets = parsed.get("bullets", [])

        async with CelerySessionLocal() as sess:
            summary = PersonSummary(
                target_id=target_id,
                run_id=run_id,
                summary_bullets=json.dumps(bullets),
                so_what_pharma=parsed.get("so_what_pharma", ""),
                insights_count=len(insights),
            )
            sess.add(summary)
            await sess.commit()

        logger.info("summarise.done", target=target_name, bullets=len(bullets))
        return {"bullets": len(bullets), "so_what_saved": True}
