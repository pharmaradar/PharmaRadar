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

        return await self._persist(post_id, parsed, target_name)

    async def _persist(self, post_id: int, parsed: dict,
                       target_name: str | None = None) -> dict:
        """Write one post's analysis. Shared by the single and batched paths so
        batching cannot drift from the behaviour that is already proven."""
        from app.database import CelerySessionLocal
        from app.models import ScrapedPost, ExtractedInsight

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

        logger.info("extractor.done", post_id=post_id,
                    target=target_name or f"post {post_id}", insights=insights_saved)
        return {"insights_saved": insights_saved}

    def summarise(self, target_id: int, run_id: int | None, ctx: RunContext) -> dict:
        return asyncio.run(self._summarise_async(target_id, run_id, ctx))

    async def _summarise_async(self, target_id: int, run_id: int | None, ctx: RunContext) -> dict:
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


# ── Batched extraction ────────────────────────────────────
#
# Extraction is the platform's dominant LLM cost: one call per post, ~23 per
# target, ~1,160 for a 50-KOL run. Batching several posts into one call cuts
# that several-fold, which matters most when the constraint is a prepaid Gemini
# balance rather than wall-clock.
#
# Deliberately small. The AE classifier batches 15 because its per-item output
# is one boolean; extraction emits several structured insights per post, and
# _call_json's docstring records what happens when the reply outgrows the token
# budget on a thinking model — truncated JSON, silently empty summaries. Three
# keeps the reply well inside the budget.
_BATCH_SIZE = 3

# Same 8192 the single path uses, now shared across three posts' output.
_BATCH_MAX_TOKENS = 8192

_BATCH_INSTRUCTIONS = (
    "You will analyse {n} SEPARATE posts, numbered [1]..[{n}].\n"
    "Apply the analysis rules above to EACH post independently — do not merge "
    "them, and do not let one post's content influence another's.\n\n"
    "Return ONE JSON object, no markdown, shaped exactly:\n"
    '{{"results": {{"1": <analysis for post 1>, "2": <analysis for post 2>, ...}}}}\n'
    "Each <analysis> has the same shape the rules above describe "
    "(insights, post_metadata, adverse_event). Include an entry for every "
    "numbered post, even if its insights list is empty.\n"
)


def batch_analyses(results, post_ids: list[int]) -> list[tuple[int, dict]] | None:
    """Pair each post with its analysis, or None if the reply does not cover all.

    Every post must be accounted for. A partial reply is the dangerous case: the
    missing posts would be persisted with nothing, and never retried, because
    the task only looks for posts that have NO insights at all. Returning None
    sends the whole group down the per-post path instead.

    Split out from the batch method so it is testable without a database — the
    method returns early when a post id is missing, which previously made these
    checks look covered when they were never reached.
    """
    if not isinstance(results, dict):
        return None
    out: list[tuple[int, dict]] = []
    for n, post_id in enumerate(post_ids, 1):
        analysis = results.get(str(n), results.get(n))
        if not isinstance(analysis, dict):
            logger.info("extractor.batch_incomplete", missing=n, posts=len(post_ids))
            return None
        out.append((post_id, analysis))
    return out


def _batch_prompt(system_prompt: str, posts: list[tuple[int, str, str]]) -> list[dict]:
    """posts: [(index, target_name, content)] -> chat messages."""
    body = "\n\n".join(
        f"=== POST [{i}] ===\nPerson: {name}\n\nContent:\n{content}"
        for i, name, content in posts)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user",
         "content": _BATCH_INSTRUCTIONS.format(n=len(posts)) + "\n" + body},
    ]


def _batch_method(cls):
    """Attached after the class body so the batching addition stays reviewable
    as one block rather than threaded through the existing method."""

    def extract_batch(self, post_ids: list[int], ctx: RunContext) -> dict | None:
        """Extract several posts in ONE LLM call. None means "use the single path".

        Returns None rather than raising or half-succeeding whenever anything is
        unexpected — an unparseable reply, a missing post, a result set that does
        not cover every post asked for. The caller then falls back to per-post
        extraction, so batching can only ever save calls, never lose insights.
        """
        return asyncio.run(self._extract_batch_async(post_ids, ctx))

    async def _extract_batch_async(self, post_ids: list[int], ctx: RunContext):
        from app.database import CelerySessionLocal
        from app.models import ScrapedPost, Target

        if len(post_ids) < 2:
            return None

        async with CelerySessionLocal() as sess:
            prepared = []
            for n, post_id in enumerate(post_ids, 1):
                post = await sess.get(ScrapedPost, post_id)
                if not post or not post.raw_content:
                    return None
                target = await sess.get(Target, post.target_id)
                prepared.append((n, target.name if target else f"Target {post.target_id}",
                                 post.raw_content[:6000]))

        ctx.increment_llm_calls()
        system_prompt = _load_prompt("extract.txt").replace(
            "{name}", "the person named at the top of each post")

        try:
            parsed = _call_json(_batch_prompt(system_prompt, prepared),
                                max_tokens=_BATCH_MAX_TOKENS,
                                log_event="extractor_batch", posts=len(post_ids))
        except Exception as exc:                       # noqa: BLE001
            logger.warning("extractor.batch_failed", exc=str(exc)[:160])
            return None
        if not isinstance(parsed, dict):
            return None

        results = parsed.get("results")
        if not isinstance(results, dict):
            return None

        analyses = batch_analyses(results, post_ids)
        if analyses is None:
            return None

        saved = 0
        for post_id, analysis in analyses:
            outcome = await self._persist(post_id, analysis)
            saved += outcome.get("insights_saved", 0)

        logger.info("extractor.batch_done", posts=len(post_ids), insights=saved)
        return {"insights_saved": saved, "posts": len(post_ids)}

    cls.extract_batch = extract_batch
    cls._extract_batch_async = _extract_batch_async
    return cls


_batch_method(ExtractorService)
