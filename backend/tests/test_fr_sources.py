"""Source-level France targeting.

The client's requirement is a SOURCE requirement, not a language one: content must
come from French publications, institutions and accounts, rather than French text
filtered out of a worldwide haul after we have paid to fetch it.

These tests guard the mechanism and the two traps that make a naive French pin
either break features (competitor / congress lanes going empty) or cost more
money (starved targets escalating to the agent-only rescue path).
"""
import pytest

from app.routers.discovery import _deep_queries, _localize, _variant_queries
from app.services.fr_sources import (
    FR_SOURCE_DOMAINS,
    FR_X_ACCOUNTS,
    Scope,
    fr_account_groups,
    fr_site_groups,
    is_french_source,
    localize_platform,
    normalize_host,
    site_scope,
    source_category,
)
from app.services.tinyfish_social import _search_variants
from app.tasks.social import _is_pharma_relevant
from app.services.scraper import (
    _billable_steps,
    _search_locale_args,
    _select_candidates,
    _signal_score,
    build_search_queries,
)


# ── The registry ──────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://www.gustaveroussy.fr/fr/actualites",
    "https://presse.curie.fr/communique",          # subdomain of a registry host
    "https://wptest.splf.fr/wp-content/a.pdf",     # subdomain, seen in live results
    "https://francais.medscape.com/article/1",     # French edition on a .com host
    "https://www.lequotidiendumedecin.fr/x",
    "https://www.chu-lyon.fr/service",
    "https://un-hopital-inconnu.fr/page",          # unlisted .fr still counts
    "https://fr.linkedin.com/in/someone",          # French platform locale
])
def test_recognises_french_sources(url):
    assert is_french_source(url) is True


@pytest.mark.parametrize("url", [
    "https://www.sciencedirect.com/science/article/x",
    "https://ascopost.com/issues/1",
    "https://www.boehringer-ingelheim.com/fr-page",   # 'fr' in the path, not the host
    "https://www.linkedin.com/in/someone",            # global locale
    "https://statnews.com/2026/01/01/x",
    "",
])
def test_rejects_non_french_sources(url):
    assert is_french_source(url) is False


def test_source_category_labels_the_registry():
    assert source_category("https://www.gustaveroussy.fr/x") == "cancer_centre"
    assert source_category("https://www.e-cancer.fr/x") == "institution"
    assert source_category("https://egora.fr/x") == "medical_press"
    assert source_category("https://ifct.fr/x") == "learned_society"
    assert source_category("https://roche.fr/x") == "pharma"
    assert source_category("https://fr.linkedin.com/in/x") == "platform_locale"
    # A .fr host outside the registry is French but has no curated category.
    assert source_category("https://un-hopital-inconnu.fr/x") is None
    assert source_category("https://statnews.com/x") is None


def test_normalize_host_strips_scheme_www_and_port():
    assert normalize_host("https://www.Curie.fr:443/page?x=1") == "curie.fr"
    assert normalize_host("curie.fr") == "curie.fr"
    assert normalize_host("") == ""


def test_site_scope_never_emits_a_bare_language_term():
    """The old `(site:.fr OR France OR français)` was satisfied by any page merely
    containing the word 'France' — a content test wearing a source test's clothes.
    Measured: 3/10 French vs 10/10 for a plain site: scope."""
    scope = site_scope(["curie.fr", "e-cancer.fr"])
    assert scope == "(site:curie.fr OR site:e-cancer.fr)"
    assert "France" not in scope and "français" not in scope
    assert site_scope([]) == ""


def test_fr_site_groups_cover_the_registry_in_bounded_chunks():
    groups = fr_site_groups(group_size=6)
    assert groups, "registry must yield at least one site: group"
    assert all(g.startswith("(site:") for g in groups)
    # Every registry domain appears in exactly one group.
    joined = " ".join(groups)
    for domain in FR_SOURCE_DOMAINS:
        assert f"site:{domain}" in joined
    assert fr_site_groups() == fr_site_groups(), "grouping must be deterministic"


def test_localize_platform_maps_to_french_locale():
    assert localize_platform("linkedin.com") == "fr.linkedin.com"
    assert localize_platform("twitter.com") == "twitter.com"


# ── Social accounts ───────────────────────────────────────

def test_french_account_urls_count_as_french_sources():
    """A social post has no .fr domain, so the source is the account."""
    assert is_french_source("https://x.com/GustaveRoussy/status/123") is True
    assert is_french_source("https://twitter.com/leQdM/status/9") is True
    assert is_french_source("https://x.com/SomeRandomUser/status/5") is False
    assert source_category("https://x.com/GustaveRoussy/status/1") == "social_account"


def test_account_groups_batch_handles_and_only_apply_to_x():
    groups = fr_account_groups("twitter", group_size=5)
    assert groups, "X must be account-pinnable"
    assert all(g.startswith("(site:x.com/") for g in groups)
    joined = " ".join(groups)
    for handle in FR_X_ACCOUNTS:
        assert f"site:x.com/{handle}" in joined
    # LinkedIn uses its country locale instead; Instagram is not indexed this way.
    assert fr_account_groups("linkedin") == []
    assert fr_account_groups("instagram") == []


def test_social_variants_pin_the_source_without_a_language_word():
    """The literal word "France" was a content test — measured 0/10 French on X."""
    twitter = _search_variants("twitter", "cancer du poumon", "fr")
    assert not any(" France " in v for v in twitter), "no bare language/geography term"
    assert any("site:x.com/GustaveRoussy" in v for v in twitter)

    linkedin = _search_variants("linkedin", "cancer du poumon", "fr")
    assert any("site:fr.linkedin.com" in v for v in linkedin)


def test_social_variants_keep_one_unpinned_discovery_lane():
    """Pinning every query to the registry means the only authors ever seen are
    already registered, which silently empties Emerging Voices — the one
    mechanism for growing the registry."""
    twitter = _search_variants("twitter", "cancer du poumon", "fr")
    unpinned = [v for v in twitter if "site:x.com/" not in v]
    assert len(unpinned) == 1, "exactly one unpinned discovery lane"

    linkedin = _search_variants("linkedin", "cancer du poumon", "fr")
    assert any(v.endswith("site:linkedin.com") for v in linkedin)


def test_global_social_scope_is_unchanged():
    assert _search_variants("twitter", "lung cancer", "all") == [
        "lung cancer site:twitter.com OR site:x.com"
    ]
    assert _search_variants("linkedin", "lung cancer", "all") == [
        "lung cancer site:linkedin.com"
    ]


def test_curated_source_bypasses_the_keyword_ingest_gate():
    """A post from a source we deliberately chose is on-topic by construction.
    Without the bypass, pinning makes yield worse: an institution's congress
    announcement carries no pharma keyword, so it is paid for and discarded."""
    no_keyword = "Journée portes ouvertes samedi, venez nous rencontrer"
    chosen = {"post_url": "https://x.com/GustaveRoussy/status/1", "text": no_keyword,
              "hashtags": [], "author": "", "topic": ""}
    random_account = {"post_url": "https://x.com/randomguy/status/1", "text": no_keyword,
                      "hashtags": [], "author": "", "topic": ""}
    assert _is_pharma_relevant(chosen) is True
    assert _is_pharma_relevant(random_account) is False
    # The keyword gate still admits on-topic posts from unknown accounts.
    on_topic = {"post_url": "https://x.com/randomguy/status/2",
                "text": "Nouvelle étude sur l'immunothérapie dans le CBNPC",
                "hashtags": [], "author": "", "topic": ""}
    assert _is_pharma_relevant(on_topic) is True


# ── Search construction ───────────────────────────────────

def test_locale_flags_only_under_french_scope():
    """`tinyfish search query` accepts --location/--language and the code never
    passed them, so every search ran at the CLI's US/EN default. Measured on the
    same KOL query: 0/10 French sources without, 3/9 with."""
    assert _search_locale_args("fr") == ["--location", "France", "--language", "fr"]
    assert _search_locale_args(Scope.GLOBAL.value) == []


def test_localize_uses_a_hard_site_scope_not_a_disjunction():
    assert _localize("immunothérapie", "fr") == "immunothérapie site:.fr"
    assert "OR" not in _localize("immunothérapie", "fr")
    assert _localize("lung cancer", "all") == "lung cancer"


@pytest.mark.parametrize("builder", [_variant_queries, _deep_queries])
def test_no_query_carries_two_contradictory_site_scopes(builder):
    """Appending `site:.fr` to a query that already has `site:linkedin.com`
    yields two scopes that cannot both hold, and the search returns nothing."""
    for query in builder("immunothérapie CBNPC", "fr"):
        if "site:.fr" in query:
            platform_scoped = any(
                token in query
                for token in ("site:linkedin.com", "site:fr.linkedin.com",
                              "site:twitter.com", "site:youtube.com")
            )
            assert not platform_scoped, f"contradictory scopes: {query}"


@pytest.mark.parametrize("builder", [_variant_queries, _deep_queries])
def test_french_scope_adds_registry_queries_and_french_linkedin(builder):
    fr = builder("immunothérapie CBNPC", "fr")
    glob = builder("immunothérapie CBNPC", "all")
    assert len(fr) > len(glob), "French scope must add registry-scoped queries"
    assert any("fr.linkedin.com" in q for q in fr)
    assert any("gustaveroussy.fr" in q for q in fr)
    # The global scope must be untouched — competitor and congress lanes use it.
    assert not any("site:.fr" in q for q in glob)
    assert any("site:linkedin.com" in q for q in glob)


def test_build_search_queries_keeps_english_under_french_scope():
    """French KOLs present at ASCO in English; dropping the anglophone queries
    would lose a KOL's own congress coverage."""
    queries = build_search_queries("Benjamin Besse", {}, scope="fr")
    assert any("CBNPC" in q for q in queries), "French clinical vocabulary missing"
    assert any("ESMO OR ASCO" in q for q in queries), "English queries must remain"
    assert any("fr.linkedin.com" in q for q in queries)

    global_queries = build_search_queries("Benjamin Besse", {}, scope="global")
    assert not any("CBNPC" in q for q in global_queries)
    assert any("site:linkedin.com" in q for q in global_queries)


# ── Ranking and slot allocation ───────────────────────────

def test_french_sources_outrank_anglophone_news_under_french_scope():
    """HIGH_SIGNAL_DOMAINS and NEWS_SITES contain no .fr host, so before this a
    French source scored 0 and was the first thing dropped by the candidate cut —
    the pipeline could search France and still store nothing from it."""
    ids = {"twitter": "drbesse"}
    assert _signal_score("https://gustaveroussy.fr/a", ids, "fr") == 9
    assert _signal_score("https://gustaveroussy.fr/a", ids, "global") == 0
    assert _signal_score("https://statnews.com/a", ids, "fr") == 8
    # The target's own handle still wins — that is the KOL's own voice, and it
    # now carries the authorship bonus on top of the handle match, because the
    # URL proves the post is theirs rather than one that merely names them.
    assert _signal_score("https://x.com/drbesse/status/1", ids, "fr") == 16
    assert (_signal_score("https://x.com/drbesse/status/1", ids, "fr")
            > _signal_score("https://gustaveroussy.fr/a", ids, "fr"))


def test_thin_french_supply_stays_french_rather_than_topping_up():
    """The client asked for French sources, so a thin French week returns fewer
    posts rather than filling the remaining slots with global ones.

    This replaces an earlier quota rule that topped the selection up to the cap
    from anywhere. Backfilling defeats the point: the spare slots were always
    filled by the global sources the France scope exists to exclude."""
    candidates = (
        [{"url": f"https://global{i}.com/x", "score": 8} for i in range(20)]
        + [{"url": f"https://centre{i}.fr/y", "score": 9} for i in range(2)]
    )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    selected = _select_candidates(candidates, limit=10, scope="fr")
    assert len(selected) == 2, "French supply is the cap, not a floor"
    assert all(".fr" in c["url"] for c in selected)


def test_zero_french_supply_still_returns_something():
    """The one case where non-French sources are still used.

    A target that ends Pass 1 with nothing escalates to the agent-only rescue,
    which is the single path that bills TinyFish credits. Returning ranked
    global candidates is cheaper than an empty result, so the French-only rule
    yields exactly when there is no French supply at all to be strict about."""
    candidates = [{"url": f"https://global{i}.com/x", "score": 8} for i in range(20)]
    selected = _select_candidates(candidates, limit=10, scope="fr")
    assert len(selected) == 10, "an empty result would escalate to the billed agent"


def test_plentiful_french_supply_fills_every_slot_with_french():
    candidates = (
        [{"url": f"https://g{i}.com/x", "score": 8} for i in range(20)]
        + [{"url": f"https://c{i}.fr/y", "score": 9} for i in range(20)]
    )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    selected = _select_candidates(candidates, limit=10, scope="fr")
    assert sum(1 for c in selected if ".fr" in c["url"]) == 10
    assert len(selected) == 10


def test_global_scope_applies_no_reservation():
    candidates = [{"url": f"https://g{i}.com/x", "score": 8} for i in range(20)]
    assert len(_select_candidates(candidates, limit=10, scope="global")) == 10
    assert _select_candidates([], limit=10, scope="fr") == []
    assert _select_candidates(candidates, limit=0, scope="fr") == []


# ── Credit metering ───────────────────────────────────────

def test_only_agent_runs_consume_credits():
    """Search and fetch are unmetered on the plan; one agent run bills its
    num_of_steps (measured 3-35), not 1. Counting every call as 1 credit made the
    dashboard report 'exhausted' for work that costs nothing — which matters
    because French source scoping adds search queries."""
    assert _billable_steps(["tinyfish", "search", "query", "x"], {"results": []}) == 0
    assert _billable_steps(["tinyfish", "fetch", "content", "get", "u"], {}) == 0
    assert _billable_steps(["tinyfish", "agent", "run", "--url", "u"], {"num_of_steps": 13}) == 13
    # Missing or unusable step counts still bill at least one credit.
    assert _billable_steps(["tinyfish", "agent", "run"], {}) == 1
    assert _billable_steps(["tinyfish", "agent", "run"], {"num_of_steps": "x"}) == 1
    assert _billable_steps(["tinyfish"], {}) == 0


# ── Instagram actor input ─────────────────────────────────

def test_instagram_terms_are_sanitised_for_the_actor():
    """apify/instagram-hashtag-scraper validates every term and rejects the WHOLE
    run with HTTP 400 on punctuation. Since terms are batched into one call, a
    single bad term returned zero Instagram posts for the entire search."""
    from app.services.apify_client import sanitize_ig_term

    assert sanitize_ig_term("immunothérapie sous-cutanée") == "immunothérapie sous cutanée"
    assert sanitize_ig_term("#Tecentriq") == "Tecentriq"
    assert sanitize_ig_term("anti-PD1") == "anti PD1"
    # Accents are legal and must survive — they are most French oncology terms.
    assert sanitize_ig_term("leucémie") == "leucémie"
    assert sanitize_ig_term("") == ""


def test_no_sanitised_term_can_trip_the_actor_validation():
    import re

    from app.services.apify_client import sanitize_ig_term

    # The actor's own pattern, read from its live 400 response.
    pattern = re.compile(r"""^\s*#?[^!?.,:;\-+=*&%$#@/\\~^|<>()\[\]{}"'`]+$""")
    hostile = [
        "What do doctors think about subcutaneous therapies in lung cancer?",
        "cancer de l'ovaire",
        "R&D: immuno-oncologie (France)",
        "50% survie — 2 ans",
    ]
    for term in hostile:
        cleaned = sanitize_ig_term(term)
        assert pattern.match(cleaned), f"still invalid: {cleaned!r}"


def test_multi_word_terms_enable_keyword_search():
    """`keywordSearch` exists on the actor and was never set, so a typed phrase
    was matched as a literal hashtag and found nothing."""
    from app.services.apify_client import _ig_run_input

    phrase = _ig_run_input(["immunothérapie sous-cutanée"], 30)
    assert phrase["keywordSearch"] is True
    assert phrase["hashtags"] == ["immunothérapie sous cutanée"]

    tag = _ig_run_input(["cancerdupoumon"], 30)
    assert "keywordSearch" not in tag, "single-token terms stay hashtag searches"

    # Nothing survivable -> None, so the caller skips the run instead of 400ing.
    assert _ig_run_input(["???", "  "], 30) is None


# ── Disease focus (competitor scoping) ────────────────────

def test_focus_terms_lead_with_french_clinical_vocabulary():
    """A French oncologist writes CBNPC, never NSCLC — an English-only term list
    cannot reach French sources at all."""
    from app.services.fr_sources import focus_clause, focus_terms

    fr = focus_terms("lung_cancer", "fr")
    assert '"cancer du poumon"' in fr and "CBNPC" in fr
    # A little English survives so congress material stays reachable.
    assert any("lung cancer" in t for t in fr)

    clause = focus_clause("lung_cancer", "fr")
    assert clause.startswith("(") and " OR " in clause


def test_no_focus_means_no_restriction():
    from app.services.fr_sources import focus_clause, focus_terms

    assert focus_terms(None, "fr") == ()
    assert focus_terms("cardiology", "fr") == (), "unknown areas must not silently filter"
    assert focus_clause(None, "fr") == ""


def test_focused_target_queries_are_all_on_topic():
    """The client asked for competitor tracking to cover lung cancer only. A
    large competitor publishes across many areas, and those results would fill
    the candidate cap before any lung-cancer content was reached."""
    queries = build_search_queries(
        "MSD France", {"twitter": "msdfrance"}, scope="fr", disease_area="lung_cancer"
    )
    own_account = ("site:twitter.com/", "site:x.com/", "site:linkedin.com/in/")
    topical = ("cancer", "cbnpc", "cpc", "poumon", "thoracique", "pulmonaire", "lung")
    for q in queries:
        if any(marker in q for marker in own_account):
            continue          # the target's own feed is in scope by definition
        assert any(t in q.lower() for t in topical), f"off-topic query: {q}"


def test_focused_queries_drop_the_broad_pharma_sweep():
    focused = build_search_queries("MSD France", {}, scope="fr", disease_area="lung_cancer")
    assert not any("pharmaceutical OR oncology" in q for q in focused)
    assert not any("Roche FDA OR EMA" in q for q in focused)
    # …and are still pinned to the French source registry.
    assert any("gustaveroussy.fr" in q for q in focused)


def test_focus_does_not_change_unfocused_targets():
    """KOLs keep their full-breadth coverage."""
    plain = build_search_queries("BESSE BENJAMIN", {}, scope="fr")
    assert any("pharmaceutical OR oncology" in q for q in plain)


def test_target_own_accounts_survive_the_focus_narrowing():
    queries = build_search_queries(
        "MSD France", {"twitter": "msdfrance"}, scope="fr", disease_area="lung_cancer"
    )
    assert any("site:x.com/msdfrance" in q for q in queries)


# ── Tracked accounts registry ─────────────────────────────

def test_registry_accounts_drive_the_search_when_supplied():
    """The client asked to define accounts; the queries must follow the registry,
    not the hardcoded constant."""
    from app.services.tinyfish_social import _search_variants

    variants = _search_variants("twitter", "cancer", "fr", ["MyClinic", "MyInstitute"])
    joined = " ".join(variants)
    assert "site:x.com/MyClinic" in joined and "site:x.com/MyInstitute" in joined
    # …and the curated constant must NOT leak in when the caller supplied a list.
    assert "GustaveRoussy" not in joined


def test_no_accounts_configured_means_no_account_pinning():
    """An empty registry must not silently fall back to the constant — that would
    scrape accounts the team explicitly removed."""
    from app.services.tinyfish_social import _search_variants

    variants = _search_variants("twitter", "cancer", "fr", [])
    assert len(variants) == 1, "only the unpinned discovery lane remains"
    assert "site:x.com/" not in variants[0]


def test_caller_supplying_nothing_falls_back_to_the_curated_constant():
    """Paths without a DB session (tests, ad-hoc calls) must keep France pinning."""
    from app.services.tinyfish_social import _search_variants

    joined = " ".join(_search_variants("twitter", "cancer", "fr", None))
    assert "GustaveRoussy" in joined


def test_handles_are_normalised_and_batched():
    from app.services.tinyfish_social import _account_groups

    groups = _account_groups("twitter", ["@one", "two ", "three", "four", "five", "six"])
    assert len(groups) == 2, "batched five per query"
    assert "site:x.com/one" in groups[0] and "@" not in groups[0]


def test_account_pinning_is_x_only():
    """LinkedIn uses its country locale; Instagram's actor cannot fetch a profile."""
    from app.services.tinyfish_social import _account_groups

    assert _account_groups("linkedin", ["someone"]) == []
    assert _account_groups("instagram", ["someone"]) == []


# ── Instagram via free search ─────────────────────────────

def test_instagram_post_urls_are_recognised():
    from app.services.tinyfish_social import _is_post_url

    assert _is_post_url("instagram", "https://www.instagram.com/p/DZpyA39jA2W/") is True
    assert _is_post_url("instagram", "https://www.instagram.com/reel/DRCorr1khrh/") is True
    # A profile or explore page is not a post and must not be ingested as one.
    assert _is_post_url("instagram", "https://www.instagram.com/unicancer/") is False
    assert _is_post_url("instagram", "https://www.instagram.com/explore/tags/cancer/") is False


def test_instagram_author_comes_from_the_search_title():
    """An Instagram post URL is /p/<code> — it identifies the post, not the
    person — so the title is the only author signal a search result carries."""
    from app.services.tinyfish_social import _instagram_author

    assert _instagram_author({"title": 'Ligue contre le cancer on Instagram: "Un vaccin"'}) \
        == "Ligue contre le cancer"
    assert _instagram_author({"title": "Some unrelated page"}) is None
    assert _instagram_author({}) is None


def test_instagram_search_is_france_scoped_and_free():
    from app.services.tinyfish_social import _search_variants

    variants = _search_variants("instagram", "cancer du poumon", "fr")
    assert variants == ["cancer du poumon site:instagram.com"]


# ── French SOURCE for social: the account, not the domain ──

def test_french_voice_recognises_a_french_society_on_a_global_platform():
    """The defect this replaces: `is_french_source` reads the domain, and every
    social post lives on a global platform domain. Measured on the live table,
    that labelled @SPLF_SocPneumo — a French learned society in our own curated
    registry — "global", simply because it posts on x.com."""
    from app.services.fr_sources import french_voice
    assert french_voice("https://x.com/SPLF_SocPneumo/status/1",
                        "@SPLF_SocPneumo", "fr")
    assert french_voice("https://x.com/GustaveRoussy/status/1",
                        "@GustaveRoussy", "en"), "a French centre posting in English is still French"


def test_french_voice_rejects_francophone_sources_outside_france():
    """French is not France. Searching in French returns Quebec and Wallonia
    too, and the client tracks the French market specifically."""
    from app.services.fr_sources import french_voice
    assert not french_voice("https://www.facebook.com/coloncanada/posts/x",
                            "coloncanada", "fr")
    assert not french_voice("https://x.com/sante_quebec/status/1",
                            "@sante_quebec", "fr")


def test_french_voice_rejects_anglophone_accounts():
    from app.services.fr_sources import french_voice
    assert not french_voice("https://x.com/MDAnderson/status/1", "@MDAnderson", "en")
    assert not french_voice("https://www.instagram.com/p/X/",
                            "huntsmancancerinstitute", "en")


def test_french_voice_accepts_a_tracked_account_in_any_language():
    from app.services.fr_sources import french_voice
    assert french_voice("https://www.instagram.com/p/X/", "bms_france", None)
    assert french_voice("https://www.instagram.com/p/X/", "unicancer", "en",
                        tracked=("unicancer",))


def test_linkedin_author_is_read_from_every_locale():
    """The French scope pins searches to fr.linkedin.com, so matching only the
    bare domain meant the lane that matters most never recorded an author —
    which both weakens provenance and starves Emerging Voices."""
    from app.services.tinyfish_social import _extract_handle
    assert _extract_handle("https://fr.linkedin.com/posts/ifct_onco-xyz") == "ifct"
    assert _extract_handle(
        "https://fr.linkedin.com/posts/etienne-giroux-leprieu_a-b") == "etienne-giroux-leprieu"
    assert _extract_handle("https://www.linkedin.com/in/benjamin-besse-1") == "benjamin-besse-1"
    assert _extract_handle("https://x.com/Inserm/status/9") == "@Inserm"


# ── Account tracking: the registry has to actually drive the scrape ──

def test_linkedin_account_queries_avoid_the_prefix_trap():
    """`site:` matches on prefix, so pinning the path is not safe.

    Measured: `site:fr.linkedin.com/posts/ifct` returned
    `ifct-institut-de-formation-continue-des-therapeutes`, a different
    organisation. The handle therefore goes in as a TERM and the path pin stops
    at /posts."""
    from app.services.tinyfish_social import linkedin_account_queries
    queries = linkedin_account_queries(["ifct", "@unicancer", "  "])
    assert queries == ["ifct site:fr.linkedin.com/posts",
                       "unicancer site:fr.linkedin.com/posts"]
    assert not any("/posts/" in q for q in queries), "path pinning prefix-matches"


def test_one_query_per_handle_because_or_batching_starves_handles():
    """Measured: `(ifct OR unicancer OR gustaveroussy)` returned 8 unicancer
    posts and nothing for the other two. Batching is cheaper and wrong."""
    from app.services.tinyfish_social import linkedin_account_queries
    queries = linkedin_account_queries(["a", "b", "c"])
    assert len(queries) == 3
    assert all(" OR " not in q for q in queries)


def test_exact_author_filter_rejects_prefix_lookalikes():
    from app.services.tinyfish_social import by_exact_author
    posts = [
        {"post_url": "https://fr.linkedin.com/posts/ifct_jad2025-activity-1"},
        {"post_url": "https://fr.linkedin.com/posts/ifct-institut-de-formation_x-2"},
        {"post_url": "https://fr.linkedin.com/posts/alexis-cortot-a9b_y-3"},
    ]
    kept = by_exact_author(posts, ["ifct"])
    assert len(kept) == 1
    assert kept[0]["author"] == "ifct"


def test_exact_author_filter_decodes_percent_encoded_slugs():
    """French org slugs come back percent-encoded; an encoded slug never
    compares equal to the handle the team typed into the registry."""
    from app.services.tinyfish_social import by_exact_author
    posts = [{"post_url":
              "https://fr.linkedin.com/posts/soci%C3%A9t%C3%A9-fran%C3%A7aise_x-1"}]
    assert len(by_exact_author(posts, ["société-française"])) == 1
