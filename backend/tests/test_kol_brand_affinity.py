"""Per-KOL brand affinity — which products a person discusses, ours vs theirs.

Topics say what someone talks about; brands say whose assets, which is the
question that decides whether and how to engage them. Computed with the same
`tally()` that powers global share of voice, so "our share of the conversation"
has one implementation rather than two that can drift.

It is read next to the declared-payments panel on the same profile, and the pair
is the point: on the live data, Girard Nicolas gives 23% of his product talk to
Roche while AstraZeneca outspends Roche on him €119k to €92k. Either number
alone is a half-answer.

`tally` itself is covered elsewhere; these tests pin the KOL-specific decisions —
what text is searched, and what the figures must not claim.
"""
import pytest

from app.services.brands import detect, tally


def item(text: str, sentiment: str = "neutral", source: str = "s", engagement: int = 0) -> dict:
    return {"text": text, "sentiment": sentiment, "source": source, "engagement": engagement}


# ── What text is searched ─────────────────────────────────

def test_brands_are_found_in_the_statement_body():
    out = tally([item("Tecentriq showed a survival benefit in this cohort")])
    assert [b["brand"] for b in out["brands"]] == ["Tecentriq"]


def test_brands_are_found_in_the_topic_line_too():
    """The profile concatenates topic + statement + context before matching.
    A drug named only in the topic — which is common, since the topic is often
    literally the drug — would otherwise be missed entirely."""
    out = tally([item("Keytruda in first-line NSCLC  ")])
    assert out["total_mentions"] == 1


def test_generic_inn_names_count_as_the_brand():
    """Clinicians write "atezolizumab" at least as often as "Tecentriq", and a
    KOL who only ever uses the INN would otherwise score zero on our portfolio."""
    out = tally([item("atezolizumab combined with chemotherapy")])
    assert [b["brand"] for b in out["brands"]] == ["Tecentriq"]


def test_matching_is_word_anchored():
    """Same rule the registry ingestion follows — "Opdivo" inside "Opdivoqtig"
    is a different product."""
    assert detect("Opdivoqtig was administered") == []


# ── Ours vs theirs ────────────────────────────────────────

def test_our_share_counts_only_roche_products():
    out = tally([
        item("Tecentriq data"), item("Alecensa data"),
        item("Keytruda data"), item("Imfinzi data"),
    ])
    assert out["roche_mentions"] == 2
    assert out["competitor_mentions"] == 2
    assert out["roche_share"] == 50


def test_a_kol_who_never_names_our_products_scores_zero_not_null():
    """A real, actionable answer: this person's product conversation is entirely
    about the competition."""
    out = tally([item("Imfinzi and Tagrisso in EGFR+ disease")])
    assert out["roche_mentions"] == 0
    assert out["roche_share"] == 0
    assert out["competitor_mentions"] == 2


def test_one_statement_naming_two_drugs_counts_for_both():
    """Share of CONVERSATION, not a partition of it — which is why the UI shows
    shares against the mention total and never sums them."""
    out = tally([item("Tecentriq versus Keytruda in first line")])
    assert out["total_mentions"] == 2
    assert out["roche_share"] == 50


# ── What the figures must not claim ───────────────────────

def test_unrated_mentions_are_not_reported_as_neutral_sentiment():
    """A drug discussed ten times without an opinion attached is UNRATED, not
    0% positive. Collapsing the two invents a signal that was never expressed —
    the same rule share-of-voice already follows."""
    out = tally([item("Tecentriq mentioned", sentiment="neutral") for _ in range(10)])
    assert out["brands"][0]["net_sentiment"] is None
    assert out["brands"][0]["rated_mentions"] == 0


def test_net_sentiment_uses_only_the_mentions_that_carried_an_opinion():
    out = tally([
        item("Tecentriq works well", sentiment="positive"),
        item("Tecentriq disappointing", sentiment="negative"),
        item("Tecentriq mentioned", sentiment="neutral"),
        item("Tecentriq works well", sentiment="positive"),
    ])
    row = out["brands"][0]
    assert row["rated_mentions"] == 3
    assert row["net_sentiment"] == round(100 * (2 - 1) / 3)


def test_a_person_with_no_product_talk_yields_an_empty_tally_not_an_error():
    """Most statements name no drug at all. That is a normal state and must
    render as "nothing named yet", not as a zero-share verdict on the KOL."""
    out = tally([item("discussed screening pathways and referral delays")])
    assert out["total_mentions"] == 0
    assert out["brands"] == []
    assert out["roche_share"] == 0


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_empty_text_is_ignored_rather_than_crashing(bad):
    assert tally([{"text": bad, "sentiment": "neutral"}])["total_mentions"] == 0


def test_brands_are_ranked_by_mentions():
    """The reader scans the top of the list, so the ordering is load-bearing."""
    out = tally([item("Keytruda")] * 5 + [item("Tecentriq")] * 2 + [item("Opdivo")])
    assert [b["brand"] for b in out["brands"]][:3] == ["Keytruda", "Tecentriq", "Opdivo"]
