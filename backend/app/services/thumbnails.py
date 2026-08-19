"""Whether a stored social thumbnail can still be displayed.

Instagram (and Facebook, same CDN) hand out **signed, expiring** image URLs. The
`oe` query parameter is the expiry as a hex Unix timestamp, and once it passes
the CDN answers 403. So what we stored was never an address — it was a receipt
with a date on it.

Measured on the live table: of 91 Instagram thumbnails, **56 had already
expired** and 35 were still live, which is why the Social page showed a grid of
blank grey rectangles. It decays daily, so the picture only gets worse.

Reading `oe` lets the API decide this for free — no HEAD request, no image
proxy, no waiting for the browser to fail. A URL known to be dead is simply not
sent, so the UI never renders a frame around a picture that cannot load.

This does not make old images come back. Persisting the bytes at ingest would,
and is deliberately NOT done here: the Vercel Blob store is PUBLIC, these images
show identifiable people, and copying them into an unauthenticated bucket is a
GDPR decision for the client rather than a caching tactic.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger(__name__)

# Meta's CDN signs URLs with `oe` (expiry, hex Unix seconds). Other hosts do not,
# and their URLs are treated as durable.
_EXPIRY_PARAM = "oe"

# Treat a URL as dead slightly before its stated expiry: it may sit in a cached
# API response for a few minutes before the browser requests it.
_EXPIRY_MARGIN_SECONDS = 300


def expiry_of(url: str | None) -> datetime | None:
    """When this signed URL stops working, or None if it carries no expiry."""
    if not url:
        return None
    try:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        raw = (params.get(_EXPIRY_PARAM) or [None])[0]
        if not raw:
            return None
        return datetime.fromtimestamp(int(raw, 16), timezone.utc)
    except (ValueError, TypeError):
        # Unparseable expiry means unknown, not expired — see is_expired.
        return None


def is_expired(url: str | None, *, now: datetime | None = None) -> bool:
    """True only when the URL demonstrably will not load.

    Unknown is not expired. A URL with no `oe` parameter, or one we cannot
    parse, is left alone: hiding a working image because the signature format
    changed would be a worse failure than showing one broken one.
    """
    expires = expiry_of(url)
    if expires is None:
        return False
    now = now or datetime.now(timezone.utc)
    return expires.timestamp() - _EXPIRY_MARGIN_SECONDS <= now.timestamp()


def usable_thumbnail(url: str | None, *, now: datetime | None = None) -> str | None:
    """The thumbnail to send to the client, or None if it cannot be displayed."""
    if not url:
        return None
    return None if is_expired(url, now=now) else url
