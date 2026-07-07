"""Shared helpers for Celery task → RunLog counter updates."""
from __future__ import annotations

import asyncio


def patch_run(run_id: int, **fields) -> None:
    """Atomically apply column updates to RunLog(id=run_id).

    Uses a single asyncio.run() so asyncpg connections stay on one event loop.
    Silently skips cancelled / finished rows — never overwrites a terminal status.
    """
    if not fields:
        return

    async def _update():
        from app.database import CelerySessionLocal
        from app.models import RunLog, RunStatus
        async with CelerySessionLocal() as sess:
            run = await sess.get(RunLog, run_id)
            if not run or run.status != RunStatus.running:
                return
            for k, v in fields.items():
                if k.startswith("+"):
                    # Increment semantics: "+new_posts_found" → add v to current
                    col = k[1:]
                    setattr(run, col, (getattr(run, col) or 0) + v)
                else:
                    setattr(run, k, v)
            await sess.commit()

    try:
        asyncio.run(_update())
    except Exception:
        pass  # progress updates are best-effort; don't crash the task
