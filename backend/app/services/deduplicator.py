"""SHA256 exact deduplication."""
import hashlib
import re


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def sha256_hash(content: str) -> str:
    return hashlib.sha256(_normalise(content).encode()).hexdigest()


# Below this, a post has no content worth keying on — an image with a two-word
# caption, or none at all. Those fall back to the URL.
_MIN_KEYABLE_TEXT = 40


def social_content_key(platform: str | None, author: str | None,
                       text: str | None, url: str | None) -> str:
    """Stable identity for a social post, keyed on what it SAYS.

    `social_posts.content_hash` was populated with `sha256_hash(post_url)` —
    named for content, computed from the address. That works only while the
    address is stable, and Facebook's is not: it hands out `pfbid…` tokens that
    rotate between scrapes, so the same post produced a different hash every
    time and the unique constraint never fired. Measured on the live table, 139
    of 975 texted posts (14%) were duplicates, 133 of them Facebook — Instagram,
    whose URLs are stable, had none. The client saw one Roche post five times in
    a single screen.

    Platform and author are part of the key because the same wording published
    by two organisations, or by one organisation on two networks, is genuinely
    two posts with their own engagement — collapsing those would hide reach
    rather than noise.

    Posts with too little text to identify keep the URL as their key: hashing an
    empty caption would collapse every image-only post from an account into one.
    """
    normalised = _normalise(text or "")
    if len(normalised) >= _MIN_KEYABLE_TEXT:
        return sha256_hash(f"{(platform or '').lower()}|{(author or '').lower()}|{normalised}")
    return sha256_hash(url or "")
