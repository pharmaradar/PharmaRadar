"""Social post identity — why the same post appeared five times in one screen.

`social_posts.content_hash` was named for content and computed from the URL.
That is a stable identity only while the URL is stable, and Facebook's is not:
it serves `pfbid…` tokens that rotate between scrapes, so every scan produced a
fresh hash for a post already stored and the unique constraint never fired.

Measured on the live table: 139 of 975 texted posts (14%) were duplicates,
**133 of them Facebook**; Instagram, whose URLs are stable, had none. Beyond the
visible repetition it inflated post counts, skewed trend ranking, and fed the
same corporate message to the LLM several times as if it were several sources.

The key is now `platform | author | normalised text`. What these tests pin is
mostly what it must NOT collapse — over-merging hides real reach, which is a
quieter failure than showing a duplicate.
"""
import pytest

from app.services.deduplicator import social_content_key, sha256_hash

ROCHE = ("Behind every #LungCancer diagnosis is a person, a family, a community "
         "navigating the challenges together. This #WorldLungCancerDay we recognise "
         "everyone impacted by lung cancer.")


# ── The bug ───────────────────────────────────────────────

def test_a_rotated_facebook_url_yields_the_same_key():
    """THE regression. Facebook rotates pfbid between scrapes; keying on the URL
    re-inserted the post every single scan."""
    a = social_content_key("facebook", "roche", ROCHE,
                           "https://www.facebook.com/roche/posts/pfbid098BkxwGJWzoRr8wK9TV")
    b = social_content_key("facebook", "roche", ROCHE,
                           "https://www.facebook.com/roche/posts/pfbid02CVfwaxcvYgfAvU5oDU")
    assert a == b


def test_the_key_is_not_merely_the_url_hash():
    """Guards against a revert to sha256_hash(post_url), which is what the column
    held despite its name."""
    url = "https://www.facebook.com/roche/posts/pfbid098"
    assert social_content_key("facebook", "roche", ROCHE, url) != sha256_hash(url)


def test_whitespace_and_case_differences_do_not_create_a_new_post():
    """Scrapers return the same caption with different line wrapping."""
    a = social_content_key("facebook", "roche", ROCHE, "u1")
    b = social_content_key("facebook", "Roche", "  " + ROCHE.replace(" ", "  ") + "\n", "u2")
    assert a == b


# ── What it must NOT collapse ─────────────────────────────

def test_two_organisations_posting_the_same_wording_stay_separate():
    """Press releases get republished verbatim. Two organisations saying the same
    thing is two data points about reach, not one duplicated post."""
    assert (social_content_key("facebook", "roche", ROCHE, "u1")
            != social_content_key("facebook", "bristolmyerssquibb", ROCHE, "u2"))


def test_one_organisation_on_two_networks_stays_separate():
    """Fondation ARC posts the same content to Facebook and Instagram; each has
    its own audience and engagement."""
    assert (social_content_key("facebook", "fondationarc", ROCHE, "u1")
            != social_content_key("instagram", "fondationarc", ROCHE, "u2"))


def test_different_posts_by_one_author_stay_separate():
    assert (social_content_key("facebook", "roche", "First announcement about the trial results", "u1")
            != social_content_key("facebook", "roche", "Second, unrelated announcement entirely", "u2"))


# ── Posts with nothing to key on ──────────────────────────

def test_image_only_posts_fall_back_to_their_url():
    """Hashing an empty caption would merge every image-only post from an account
    into a single row — a far worse loss than the duplicates being fixed."""
    a = social_content_key("instagram", "clinic", "📷", "https://instagram.com/p/AAA")
    b = social_content_key("instagram", "clinic", "📷", "https://instagram.com/p/BBB")
    assert a != b


@pytest.mark.parametrize("text", [None, "", "   ", "Nice!", "#lungcancer"])
def test_short_captions_use_the_url(text):
    a = social_content_key("instagram", "clinic", text, "https://instagram.com/p/AAA")
    b = social_content_key("instagram", "clinic", text, "https://instagram.com/p/BBB")
    assert a != b


def test_a_post_long_enough_to_identify_uses_its_text():
    """The threshold has to be low enough that ordinary captions are keyed on
    content, or Facebook duplicates come straight back."""
    text = "Le cancer du poumon est l'un des plus frequents en France cette annee"
    assert (social_content_key("facebook", "x", text, "u1")
            == social_content_key("facebook", "x", text, "u2"))


# ── Shape ─────────────────────────────────────────────────

def test_the_key_is_a_sha256_hex_digest():
    """It populates a String(64) unique column."""
    key = social_content_key("facebook", "roche", ROCHE, "u")
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


@pytest.mark.parametrize("platform,author", [(None, None), ("", ""), (None, "roche")])
def test_missing_platform_or_author_does_not_raise(platform, author):
    assert len(social_content_key(platform, author, ROCHE, "u")) == 64


def test_a_post_with_neither_text_nor_url_still_produces_a_key():
    """A NOT NULL column must always get a value, however empty the row."""
    assert len(social_content_key("facebook", "x", None, None)) == 64
