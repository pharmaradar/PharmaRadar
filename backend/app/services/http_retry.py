"""Retrying fetch for the free official APIs, and a record of what failed.

Every keyless lane in the platform — Europe PMC, ClinicalTrials.gov, OpenAlex,
BDPM, Transparence Santé, the French RSS feeds — opened a bare urllib request
with no retry. A single transient 503 therefore lost whatever that call was
fetching, and the caller returned an empty list.

That is worse than it sounds, because empty is also the normal answer. When
Europe PMC returned 503 on 2026-08-18 (verified: 503 on even `query=cancer`),
`search_publications` logged a warning, returned `[]`, and the nightly sweep
reported success with zero new publications — indistinguishable from a quiet
week. Silent failure is the failure mode this platform can least afford: nobody
looks for a report that never says anything is wrong.

Two things here. `fetch_json` retries the errors that are worth retrying, and
`SourceHealth` lets a sweep report "the source was unavailable" separately from
"the source had nothing", so a run that collected nothing because an API was
down says so.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_UA = "PharmaRadar/1.0 (pharma intelligence; contact via platform administrator)"

# Transient by nature: the service is up but momentarily refusing. 429 is
# included because these are shared public APIs with per-IP throttling, and the
# right response to their throttle is to wait, not to drop the work.
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Three attempts over roughly 6 seconds. Long enough to ride out a blip, short
# enough that a genuinely dead source does not stall a sweep across 50 targets —
# at that width, a 30-second ladder per call would outlive the task's own limit.
_ATTEMPTS = 3
_BACKOFF_SECONDS = (1.0, 4.0)


class SourceUnavailable(RuntimeError):
    """The source could not be reached, as distinct from having no results."""


def fetch_json(url: str, *, timeout: int = 30, user_agent: str = DEFAULT_UA,
               attempts: int = _ATTEMPTS) -> dict:
    """GET JSON, retrying transient failures.

    Raises SourceUnavailable when every attempt fails, so a caller can tell a
    dead source from an empty result instead of returning [] for both.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in _RETRY_STATUS:
                # 404 or 400 will fail identically forever; retrying only
                # delays the sweep.
                raise SourceUnavailable(f"HTTP {exc.code} for {url[:120]}") from exc
        except Exception as exc:                      # noqa: BLE001 - timeouts, DNS, resets
            last = exc

        if attempt < attempts - 1:
            wait = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
            logger.info("http_retry.retrying", attempt=attempt + 1, wait=wait,
                        url=url[:100], error=str(last)[:120])
            time.sleep(wait)

    raise SourceUnavailable(f"{type(last).__name__}: {str(last)[:140]}") from last


@dataclass
class SourceHealth:
    """What a sweep managed to reach, so an empty run can explain itself."""

    reached: int = 0
    unavailable: int = 0
    sources_down: set[str] = field(default_factory=set)

    def ok(self, source: str) -> None:
        self.reached += 1
        self.sources_down.discard(source)

    def failed(self, source: str, error: str = "") -> None:
        self.unavailable += 1
        self.sources_down.add(source)
        logger.warning("source.unavailable", source=source, error=error[:160])

    @property
    def degraded(self) -> bool:
        return self.unavailable > 0

    def as_dict(self) -> dict:
        return {
            "reached": self.reached,
            "unavailable": self.unavailable,
            "sources_down": sorted(self.sources_down),
            # The field a human actually reads. Without it, "0 publications"
            # from a dead API looks exactly like "0 publications" from a quiet
            # week, and only one of those needs attention.
            "degraded": self.degraded,
        }
