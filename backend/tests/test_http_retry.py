"""Retrying the free official APIs, and telling "down" from "empty".

Every keyless lane — Europe PMC, ClinicalTrials.gov, OpenAlex, BDPM,
Transparence Santé, the French feeds — opened a bare urllib request with no
retry, so one transient 503 lost whatever that call was fetching and the caller
returned an empty list.

Empty is also the normal answer, and that is what makes it dangerous. Europe PMC
returned 503 to every query including `query=cancer` on 2026-08-18 (and
recovered minutes later). During that window the nightly sweep would have logged
a warning, returned [], and reported success with zero new publications —
indistinguishable from a quiet week for a source that normally yields hundreds.
"""
import urllib.error

import pytest

from app.services import http_retry
from app.services.http_retry import SourceHealth, SourceUnavailable, fetch_json


class _Resp:
    def __init__(self, body=b'{"ok": true}'): self._body = body
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Never actually wait through the backoff in tests."""
    monkeypatch.setattr(http_retry.time, "sleep", lambda _s: None)


# ── Retrying ──────────────────────────────────────────────

def test_a_transient_failure_is_retried_and_succeeds(monkeypatch):
    """THE case: a 503 from a shared public API is a blip, not an answer."""
    calls = {"n": 0}

    def flaky(request, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return _Resp()

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", flaky)
    assert fetch_json("http://x")["ok"] is True
    assert calls["n"] == 3


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_transient_status_codes_are_retried(monkeypatch, code):
    calls = {"n": 0}

    def always(request, timeout):
        calls["n"] += 1
        raise _http_error(code)

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", always)
    with pytest.raises(SourceUnavailable):
        fetch_json("http://x")
    assert calls["n"] > 1, f"{code} should have been retried"


@pytest.mark.parametrize("code", [400, 404, 410])
def test_permanent_status_codes_are_not_retried(monkeypatch, code):
    """A 404 will fail identically forever; retrying only stalls a sweep that
    may be running across fifty targets."""
    calls = {"n": 0}

    def always(request, timeout):
        calls["n"] += 1
        raise _http_error(code)

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", always)
    with pytest.raises(SourceUnavailable):
        fetch_json("http://x")
    assert calls["n"] == 1


def test_a_timeout_is_retried(monkeypatch):
    calls = {"n": 0}

    def slow(request, timeout):
        calls["n"] += 1
        raise TimeoutError("read timed out")

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", slow)
    with pytest.raises(SourceUnavailable):
        fetch_json("http://x")
    assert calls["n"] > 1


def test_the_retry_ladder_stays_short_enough_for_a_wide_sweep():
    """A sweep runs this per target. A long ladder per call would outlive the
    task's own time limit before the sweep finished."""
    assert http_retry._ATTEMPTS <= 4
    assert sum(http_retry._BACKOFF_SECONDS) <= 15


# ── Down is not empty ─────────────────────────────────────

def test_an_unreachable_source_raises_rather_than_returning_empty(monkeypatch):
    """The whole point. [] means "this author has no papers"; an exception means
    "we could not ask". Returning [] for both is what made an outage look like a
    quiet week."""
    monkeypatch.setattr(http_retry.urllib.request, "urlopen",
                        lambda request, timeout: (_ for _ in ()).throw(_http_error(503)))
    with pytest.raises(SourceUnavailable):
        fetch_json("http://x")


def test_a_genuinely_empty_result_is_not_an_error(monkeypatch):
    monkeypatch.setattr(http_retry.urllib.request, "urlopen",
                        lambda request, timeout: _Resp(b'{"resultList": {"result": []}}'))
    assert fetch_json("http://x") == {"resultList": {"result": []}}


# ── Reporting it ──────────────────────────────────────────

def test_a_sweep_that_reached_everything_is_not_degraded():
    health = SourceHealth()
    health.ok("europepmc")
    health.ok("clinicaltrials")
    assert health.degraded is False
    assert health.as_dict()["sources_down"] == []


def test_a_sweep_with_a_dead_source_reports_degraded():
    """So "0 publications" carries the reason with it. Without this the run
    result is identical whether the API was down or the week was quiet, and only
    one of those needs anyone's attention."""
    health = SourceHealth()
    health.ok("clinicaltrials")
    health.failed("europepmc", "HTTP 503")
    assert health.degraded is True
    assert health.as_dict()["sources_down"] == ["europepmc"]
    assert health.as_dict()["reached"] == 1


def test_a_source_that_recovers_is_no_longer_listed_as_down():
    """Europe PMC recovered within minutes of the outage that prompted this."""
    health = SourceHealth()
    health.failed("europepmc", "HTTP 503")
    health.ok("europepmc")
    assert health.as_dict()["sources_down"] == []
