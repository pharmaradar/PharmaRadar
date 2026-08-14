"""Account tracking: the pieces that fail silently if they are wrong."""
from app.routers.accounts import _normalise_handle
from app.services.tinyfish_social import account_query, by_exact_author


class TestHandleNormalisation:
    """People paste profile URLs as often as they type handles.

    Storing both forms for one account defeats UNIQUE (platform, handle): the
    same account gets tracked twice, scanned twice and counted twice.
    """

    def test_strips_at_sign(self):
        assert _normalise_handle("twitter", "@GustaveRoussy") == "GustaveRoussy"

    def test_extracts_slug_from_linkedin_company_url(self):
        assert _normalise_handle(
            "linkedin", "https://fr.linkedin.com/company/ifct") == "ifct"

    def test_extracts_slug_from_linkedin_person_url(self):
        assert _normalise_handle(
            "linkedin", "https://fr.linkedin.com/in/benjamin-besse-1") == "benjamin-besse-1"

    def test_extracts_handle_from_instagram_url(self):
        assert _normalise_handle(
            "instagram", "https://www.instagram.com/unicancer/") == "unicancer"

    def test_drops_query_string(self):
        assert _normalise_handle(
            "facebook", "www.facebook.com/roche?locale=fr_FR") == "roche"

    def test_url_and_handle_forms_collapse_to_one_value(self):
        """The property that actually matters — both spellings dedupe."""
        assert (_normalise_handle("instagram", "https://www.instagram.com/unicancer/")
                == _normalise_handle("instagram", "@unicancer"))

    def test_blank_stays_blank(self):
        assert _normalise_handle("twitter", "   ") == ""


class TestAccountQuery:
    """The account lane must never issue an unpinned search.

    `_search_variants` always includes a discovery lane with no account pin.
    Reusing it here with an empty term would make the first query a bare
    `site:x.com`, and whoever happened to rank would be filed under the tracked
    account — inventing activity that account never had.
    """

    def test_twitter_is_pinned_to_the_account_path(self):
        assert account_query("twitter", "@GustaveRoussy") == "site:x.com/GustaveRoussy"

    def test_linkedin_puts_the_handle_in_the_term_not_the_path(self):
        # site: matches on prefix, so /posts/ifct also returns
        # ifct-institut-de-formation-continue-des-therapeutes.
        query = account_query("linkedin", "ifct")
        assert query == "ifct site:fr.linkedin.com/posts"
        assert "/posts/ifct" not in query

    def test_no_query_is_unpinned(self):
        for platform in ("twitter", "linkedin", "instagram"):
            query = account_query(platform, "someaccount")
            assert query and "someaccount" in query

    def test_facebook_has_no_free_lane(self):
        """Measured: Facebook account search through the web index returns zero.
        Returning None makes the caller fall through to Apify rather than issue
        a query that cannot work."""
        assert account_query("facebook", "roche") is None

    def test_blank_handle_yields_no_query(self):
        assert account_query("twitter", "  ") is None


class TestExactAuthorFilter:
    def test_prefix_lookalike_is_rejected(self):
        posts = [
            {"post_url": "https://fr.linkedin.com/posts/ifct_jad2025-activity-1"},
            {"post_url": "https://fr.linkedin.com/posts/ifct-institut-de-formation_x-2"},
        ]
        kept = by_exact_author(posts, ["ifct"])
        assert [p["post_url"] for p in kept] == [posts[0]["post_url"]]

    def test_author_is_stamped_on_kept_posts(self):
        posts = [{"post_url": "https://x.com/Inserm/status/1"}]
        assert by_exact_author(posts, ["Inserm"])[0]["author"] == "inserm"

    def test_unrelated_account_is_dropped(self):
        posts = [{"post_url": "https://x.com/MDAnderson/status/1"}]
        assert by_exact_author(posts, ["Inserm"]) == []
