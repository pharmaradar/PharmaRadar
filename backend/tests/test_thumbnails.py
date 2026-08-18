"""Expiring social thumbnails — why the Social grid filled with grey boxes.

Meta signs its CDN image URLs and stamps the expiry into the `oe` query
parameter as a hex Unix timestamp. Once it passes the CDN returns 403. So a
stored thumbnail URL is a receipt with a date on it, not an address.

Measured on the live table when this was written: 91 Instagram thumbnails
stored, **56 already expired**, 35 live — and only 35 of 122 Instagram posts
(29%) could render an image at all. The frontend made it look worse by hiding
the failed <img> while keeping its 128px grey wrapper, so every dead URL became
a blank rectangle.

Reading `oe` decides this with no network call, which is what lets the API omit
a dead URL instead of letting the browser discover it.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import thumbnails as th


def _instagram_url(expires_at: datetime) -> str:
    """A realistic signed CDN URL with `oe` as hex seconds."""
    oe = format(int(expires_at.timestamp()), "X")
    return ("https://scontent-waw2-1.cdninstagram.com/v/t51.82787-15/"
            f"772869166_18566162215070075_761311649678493946_n.jpg"
            f"?stp=dst-jpg_e35&_nc_ht=scontent-waw2-1.cdninstagram.com&oe={oe}&oh=abc")


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


# ── Reading the expiry ────────────────────────────────────

def test_the_expiry_is_read_from_the_signed_url():
    when = NOW + timedelta(days=2)
    assert th.expiry_of(_instagram_url(when)).timestamp() == pytest.approx(when.timestamp(), abs=1)


def test_a_url_without_an_expiry_parameter_has_none():
    assert th.expiry_of("https://example.com/image.jpg") is None


@pytest.mark.parametrize("url", [None, "", "not a url", "https://x/?oe=ZZZZ"])
def test_unreadable_urls_yield_no_expiry(url):
    assert th.expiry_of(url) is None


# ── Deciding what to send ─────────────────────────────────

def test_an_expired_thumbnail_is_withheld():
    """THE bug: 56 of 91 stored Instagram thumbnails were already dead, and each
    one rendered as an empty grey frame."""
    dead = _instagram_url(NOW - timedelta(days=1))
    assert th.is_expired(dead, now=NOW) is True
    assert th.usable_thumbnail(dead, now=NOW) is None


def test_a_live_thumbnail_is_sent_through_unchanged():
    live = _instagram_url(NOW + timedelta(days=3))
    assert th.is_expired(live, now=NOW) is False
    assert th.usable_thumbnail(live, now=NOW) == live


def test_a_thumbnail_expiring_within_the_margin_is_treated_as_dead():
    """A response can sit in a cache for minutes before the browser asks for the
    image; sending one that dies in between just moves the blank box."""
    nearly = _instagram_url(NOW + timedelta(seconds=60))
    assert th.is_expired(nearly, now=NOW) is True


def test_an_unsigned_url_is_never_suppressed():
    """Unknown is not expired. Hiding a working image because a CDN changed its
    signature format would be a worse failure than showing one broken one."""
    plain = "https://pbs.twimg.com/media/abc.jpg"
    assert th.is_expired(plain, now=NOW) is False
    assert th.usable_thumbnail(plain, now=NOW) == plain


def test_a_missing_thumbnail_stays_missing():
    assert th.usable_thumbnail(None) is None
    assert th.usable_thumbnail("") is None


def test_a_malformed_expiry_does_not_suppress_the_image():
    """Same principle: a parse failure is not evidence the image is dead."""
    assert th.is_expired("https://scontent.cdninstagram.com/x.jpg?oe=nothex", now=NOW) is False


# ── The distinction the UI depends on ─────────────────────

def test_never_had_an_image_is_distinguishable_from_image_expired():
    """The card says "no longer available" for one and shows nothing for the
    other. Collapsing them would either invent a loss or hide a real one."""
    never = None
    expired = _instagram_url(NOW - timedelta(days=5))

    # Both send no image, but the API's `thumbnail_expired` flag separates them:
    # "had a url AND it is now unusable" vs "never had one".
    def expired_flag(url):
        return bool(url) and not th.usable_thumbnail(url, now=NOW)

    assert th.usable_thumbnail(never, now=NOW) is None
    assert th.usable_thumbnail(expired, now=NOW) is None
    assert expired_flag(never) is False
    assert expired_flag(expired) is True
