"""Shared helpers for Celery task → RunLog counter updates."""
from __future__ import annotations

import asyncio


def patch_run(run_id: int | None, **fields) -> None:
    """Atomically apply column updates to RunLog(id=run_id).

    Uses a single asyncio.run() so asyncpg connections stay on one event loop.
    Silently skips cancelled / finished rows — never overwrites a terminal status.
    `run_id=None` means the caller isn't part of a scrape run at all (e.g. an
    on-demand summary) — skip without a DB round trip rather than querying for
    a row that was never going to exist.
    """
    if not fields or run_id is None:
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
