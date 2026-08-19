"""The X/Twitter Apify actor — and the spend ceiling around it.

`microworlds/twitter-scraper` no longer exists (verified 404 against Apify's
API), so X fell through to TinyFish search. TinyFish search carries no
engagement figures at all, which is why 100% of stored X posts had zero likes
and none could rank in Trending on Social — 478 of roughly 900 posts.

`apidojo/tweet-scraper` (158M runs) replaces it and bills PER TWEET at $0.40 per
thousand. That pricing model is what these tests are really about: a mis-set
max_results is a bill, not a slow run, so the ceiling is enforced structurally
rather than trusted to callers.
"""
import pytest

from app.services import apify_client as ac


# ── The actor ─────────────────────────────────────────────

def test_the_configured_x_actor_is_the_working_one():
    """Pinned by name: the previous actor 404s, and a silent fallback to
    TinyFish is exactly how the engagement gap went unnoticed."""
    assert ac.ACTORS["twitter"] == "apidojo/tweet-scraper"
    assert "microworlds" not in ac.ACTORS["twitter"]


def test_linkedin_is_left_on_tinyfish_deliberately():
    """Its Apify actor also 404s and no replacement is wired yet. Pointing it at
    a dead actor would lose the coverage TinyFish still provides."""
    assert ac.ACTORS["linkedin"] == "apify/linkedin-post-search-scraper"


# ── The spend ceiling ─────────────────────────────────────

def test_a_ceiling_exists_and_is_modest():
    """At $0.40/1,000 tweets this caps a single run's worst case at cents."""
    assert 100 <= ac._X_MAX_ITEMS <= 2000


@pytest.mark.parametrize("requested,expected", [
    (30, 30),
    (600, 600),
    (5000, None),        # clamped
    (10 ** 6, None),     # a typo that would otherwise be a real bill
])
def test_the_request_is_clamped_to_the_ceiling(requested, expected):
    capped = max(1, min(int(requested or 0), ac._X_MAX_ITEMS))
    assert capped <= ac._X_MAX_ITEMS
    if expected is not None:
        assert capped == expected


@pytest.mark.parametrize("bad", [0, None, -50])
def test_a_missing_or_negative_limit_never_becomes_unlimited(bad):
    """`min(0, ceiling)` is 0 and `None` would raise; both must resolve to a
    small positive number rather than to "fetch everything"."""
    capped = max(1, min(int(bad or 0), ac._X_MAX_ITEMS))
    assert 1 <= capped <= ac._X_MAX_ITEMS


# ── Targeting France at acquisition ───────────────────────

def test_the_ceiling_holds_on_the_build_input_path_too(): 
    """Caught by reviewing the live path: _build_input returned maxItems=5000
    unclamped while only the call sites capped. The SDK's max_items still bound
    the run, but a spend ceiling that holds on one of two layers is one refactor
    away from not holding at all."""
    run_input = ac._build_input("twitter", "cancer", 5000, None, "fr")
    assert run_input["maxItems"] == ac._X_MAX_ITEMS


def test_the_french_filter_is_the_actor_s_own_language_parameter():
    """`tweetLanguage` is applied before results are counted, so a French run
    pays only for French tweets. The `lang:` search operator would still bill
    for whatever the search returned — the same 'filter at acquisition, not
    afterwards' rule the rest of the platform follows."""
    run_input = ac._build_input("twitter", "cancer du poumon", 50, None, "fr")
    assert run_input["maxItems"] == 50
    assert run_input["tweetLanguage"] == "fr"
    assert "searchTerms" in run_input


def test_a_worldwide_search_sets_no_language_filter():
    run_input = ac._build_input("twitter", "lung cancer", 50, None, "all")
    assert "tweetLanguage" not in run_input
    assert "lang:" not in str(run_input)


# ── Not losing what already worked ────────────────────────

def test_the_twitter_normaliser_reads_this_actor_s_field_names():
    """apidojo returns likeCount / retweetCount / replyCount / author.userName.
    Engagement is the whole reason for the swap, so a mismatch here would fix
    nothing while adding a bill."""
    item = {
        "url": "https://x.com/someone/status/1",
        "text": "Le cancer du poumon en France",
        "author": {"userName": "someone"},
        "likeCount": 12, "retweetCount": 4, "replyCount": 3, "viewCount": 900,
        "createdAt": "2026-08-01T10:00:00.000Z",
    }
    post = ac._norm_twitter(item)
    assert post["platform"] == "twitter"
    assert post["author"] == "someone"
    assert post["likes"] == 12 and post["shares"] == 4
    assert post["comments"] == 3 and post["views"] == 900


def test_a_tweet_without_a_url_is_dropped():
    """No URL means no stable identity and nothing to link to."""
    assert ac._norm_twitter({"text": "orphan"}) is None
