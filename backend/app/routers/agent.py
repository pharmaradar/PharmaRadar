"""Hermes AI chat endpoint — RAG over extracted insights + social posts."""
import asyncio
import re
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AgentMessage, ExtractedInsight, Target, SocialPost, User
from app.auth import get_current_user

router = APIRouter(prefix="/api/agent", tags=["agent"])

_MAX_CONTEXT_INSIGHTS = 30
_MAX_SOCIAL_CONTEXT = 15

# Words that signal the user wants social/trend data
_SOCIAL_KEYWORDS = re.compile(
    r"\b(social|trend|trending|instagram|linkedin|twitter|facebook|post|hashtag|"
    r"patient|public|people|saying|discuss|viral|engagement|likes|views)\b",
    re.IGNORECASE,
)


async def _fetch_social_context(message: str, db: AsyncSession) -> str | None:
    """Search social_posts for terms from the user message. Returns a formatted
    context block, or None if no relevant posts found."""
    # Extract meaningful words (3+ chars, skip stopwords)
    stopwords = {"what", "about", "the", "and", "for", "are", "that", "this",
                 "with", "from", "have", "tell", "show", "give", "find", "any"}
    words = [w for w in re.findall(r"[a-zA-Z]{3,}", message.lower()) if w not in stopwords]
    if not words:
        return None

    # Build OR conditions for the most specific words (up to 4)
    search_terms = words[:4]
    conditions = []
    for term in search_terms:
        like = f"%{term}%"
        conditions.extend([
            func.lower(SocialPost.text).like(like),
            func.lower(SocialPost.topic).like(like),
            func.lower(SocialPost.hashtags).like(like),
        ])

    rows = await db.execute(
        select(SocialPost)
        .where(or_(*conditions))
        .order_by(desc(SocialPost.scraped_at))
        .limit(_MAX_SOCIAL_CONTEXT)
    )
    posts = rows.scalars().all()
    if not posts:
        return None

    lines = [f"SOCIAL MEDIA DATA ({len(posts)} relevant posts from the database):"]
    for p in posts:
        eng = (p.likes or 0) + (p.comments or 0) + (p.shares or 0)
        date = p.posted_at.strftime("%Y-%m-%d") if p.posted_at else "?"
        text = (p.text or "")[:300].replace("\n", " ")
        lines.append(
            f"[{p.platform}] @{p.author or '?'} | {date} | {eng:,} engagements | "
            f"topic:{p.topic or '-'} | {text}"
        )
    return "\n".join(lines)


class ChatRequest(BaseModel):
    message: str


@router.post("/chat")
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db),
               user: User = Depends(get_current_user)):
    from pathlib import Path
    from app.services.llm_router import call_pro

    # Load agent system prompt
    prompt_path = Path(__file__).parent.parent / "prompts" / "agent.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Retrieve recent KOL insights as RAG context
    rows = await db.execute(
        select(ExtractedInsight, Target)
        .join(Target, ExtractedInsight.target_id == Target.id)
        .order_by(desc(ExtractedInsight.extracted_at))
        .limit(_MAX_CONTEXT_INSIGHTS)
    )
    context_chunks = [
        f"[{target.name}] {ins.topic}: {ins.what_they_said}"
        for ins, target in rows.all()
    ]
    context_text = "\n".join(context_chunks)

    # Always search social posts for the user's query
    social_context = await _fetch_social_context(body.message, db)

    # Load this user's conversation history
    history_rows = await db.execute(
        select(AgentMessage).where(AgentMessage.user_id == user.id)
        .order_by(AgentMessage.created_at).limit(20)
    )
    history = history_rows.scalars().all()
    messages = [{"role": "system", "content": system_prompt}]
    if context_text:
        messages.append({"role": "system", "content": f"RECENT KOL INTELLIGENCE:\n{context_text}"})
    if social_context:
        messages.append({"role": "system", "content": social_context})
    for h in history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": body.message})

    # call_pro is synchronous and uses asyncio.run() internally;
    # run it in a thread pool to avoid "event loop already running" from this async context
    loop = asyncio.get_event_loop()
    try:
        reply = await loop.run_in_executor(None, partial(call_pro, messages, max_tokens=4096))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {str(exc)[:200]}")

    # Persist exchange (scoped to this user)
    db.add(AgentMessage(role="user", content=body.message, user_id=user.id))
    db.add(AgentMessage(role="assistant", content=reply, user_id=user.id))
    await db.commit()

    return {"reply": reply}


@router.get("/history")
async def get_history(db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    rows = await db.execute(
        select(AgentMessage).where(AgentMessage.user_id == user.id)
        .order_by(AgentMessage.created_at).limit(100)
    )
    return [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in rows.scalars().all()]


@router.delete("/history", status_code=204)
async def clear_history(db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    from sqlalchemy import delete
    await db.execute(delete(AgentMessage).where(AgentMessage.user_id == user.id))
    await db.commit()
