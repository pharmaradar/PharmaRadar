"""Acceptance tests: the questions the client actually types.

Different in kind from the rest of the suite. Everything else asks "is this
function correct"; this asks "does Amaury get what he asked for". The questions
below are real — taken from his search history in the live platform — and each
one is a shape the answer has to satisfy.

The one that prompted the file: he typed

    "what is the top 5 subject that lung cancer patient want to discuss"

and the platform returned 120 posts, including the same Roche post four times
and a BMS item about sub-Saharan Africa. Nothing was wrong with any individual
component. The product still failed, because a question was answered with a
search.

No network here — these pin the request/response contract and the reasoning that
surrounds the model call. The model's prose is not asserted; what is asserted is
that the answer has the shape he asked for, that it cannot silently answer about
the wrong people, and that it never invents a source.
"""
import pytest

from app.services import social_answer as sa


# Real queries from the client's recent-search list, with the shape each implies.
CLIENT_QUESTIONS = [
    "what is the top 5 subject that lung cancer patient want to discuss",
    "does KOL think about pembrolizumab",
    "what doctor think about subcutaneous therapies in lung cancer",
]


# ── "top 5" must return five ──────────────────────────────

def test_a_top_n_question_is_recognised_as_wanting_a_list():
    assert sa.wants_ranked_list(CLIENT_QUESTIONS[0]) is True


def test_the_requested_count_is_honoured():
    """Answering with three when five were asked for is a wrong answer, not a
    shorter one."""
    assert sa.requested_count("what is the top 5 subject that patients discuss") == 5
    assert sa.requested_count("top 3 concerns") == 3
    assert sa.requested_count("les 10 principaux sujets") == 5   # no "top N" form


def test_the_prompt_asks_for_exactly_the_number_requested():
    prompt = sa.build_prompt("what is the top 5 subject that lung cancer patient want to discuss",
                             [{"text": "x", "platform": "instagram"}], "patient", "")
    assert "Exactly 5 items" in prompt


def test_an_open_question_is_not_forced_into_a_list():
    """"what do doctors think about X" wants prose. A leaderboard would be a
    worse answer, not a tidier one."""
    assert sa.wants_ranked_list("what doctor think about subcutaneous therapies") is False
    prompt = sa.build_prompt("what doctor think about subcutaneous therapies",
                             [{"text": "x"}], "doctor", "")
    assert "sentences answering the question" in prompt


@pytest.mark.parametrize("question", CLIENT_QUESTIONS)
def test_every_client_question_produces_a_usable_prompt(question):
    """Whatever he types, the prompt must carry the question, the section
    markers the parser expects, and the evidence."""
    prompt = sa.build_prompt(question, [{"text": "a post", "platform": "x"}],
                             sa.asks_about(question), "")
    assert question in prompt
    for marker in ("##ANSWER##", "##SO_WHAT##", "##CONFIDENCE##"):
        assert marker in prompt


# ── Answering about the right people ──────────────────────

def test_a_question_about_patients_is_recognised():
    assert sa.asks_about(CLIENT_QUESTIONS[0]) == "patient"


def test_a_question_about_doctors_is_recognised():
    assert sa.asks_about(CLIENT_QUESTIONS[2]) == "doctor"


def test_a_question_about_kols_is_recognised():
    assert sa.asks_about(CLIENT_QUESTIONS[1]) == "kol"


def test_a_question_naming_nobody_has_no_audience():
    assert sa.asks_about("pembrolizumab") is None


def test_asking_about_patients_with_no_patient_voices_is_flagged():
    """THE failure in the screenshot. The posts matching "what patients want to
    discuss" were Roche and BMS corporate accounts — zero patients. An answer
    that read as patient opinion would be confidently wrong in a way the reader
    cannot see."""
    fits, note = sa.evidence_matches_audience("patient",
                                              {"organisation": 23, "other": 36, "doctor": 1})
    assert fits is False
    # "None" is a different message from "only a few", and the reader acts on it
    # differently: none means the answer is about patients rather than by them.
    assert note.startswith("None of the matched posts")
    assert "not BY them" in note


def test_a_thin_minority_of_the_right_voice_is_still_flagged():
    """Two patients out of forty is not a patient view."""
    fits, note = sa.evidence_matches_audience("patient", {"patient": 2, "organisation": 38})
    assert fits is False
    assert "indicative" in note


def test_a_genuine_majority_of_the_right_voice_passes_without_a_caveat():
    fits, note = sa.evidence_matches_audience("patient", {"patient": 30, "organisation": 10})
    assert fits is True
    assert note == ""


def test_a_question_with_no_named_audience_is_never_caveated():
    """Nothing to disagree with, so a warning would just be noise."""
    fits, note = sa.evidence_matches_audience(None, {"organisation": 50})
    assert fits is True and note == ""


def test_the_prompt_tells_the_model_to_separate_organisations_from_the_audience():
    prompt = sa.build_prompt(CLIENT_QUESTIONS[0], [{"text": "x"}], "patient",
                             "None of the matched posts come from patients")
    assert "organisation" in prompt.lower()
    assert "EVIDENCE CAVEAT" in prompt


# ── Evidence hygiene ──────────────────────────────────────

def test_the_same_post_repeated_is_counted_once():
    """The screenshot shows one Roche post four times. Repetition is not
    evidence; left in, a single corporate message reads as the dominant theme
    and crowds real variety out of the prompt."""
    roche = ("Behind every #LungCancer diagnosis is a person, a family, "
             "a community navigating the challenges together.")
    posts = [{"text": roche}] * 4 + [{"text": "A different post entirely"}]
    assert len(sa.dedupe_evidence(posts)) == 2


def test_deduplication_ignores_whitespace_and_case():
    posts = [{"text": "Same message here"}, {"text": "  SAME   message here  "}]
    assert len(sa.dedupe_evidence(posts)) == 1


def test_deduplication_keeps_genuinely_different_posts():
    posts = [{"text": f"Distinct post number {i}"} for i in range(10)]
    assert len(sa.dedupe_evidence(posts)) == 10


def test_empty_posts_are_dropped():
    assert sa.dedupe_evidence([{"text": ""}, {"text": None}]) == []


def test_the_evidence_budget_leaves_the_model_room_to_answer():
    """The first live run truncated at 917 characters with two of three sections
    missing: a thinking model spends the same budget on reasoning. Pinned so the
    evidence count cannot creep back up and silently re-break the answer."""
    assert sa.MAX_EVIDENCE <= 40
    assert sa.EVIDENCE_CHARS <= 600


# ── Citations: never invent a source ──────────────────────

def _evidence(n=20):
    return [{"platform": "x", "author": f"a{i}", "voice": "other",
             "url": f"https://x/{i}", "text": f"post {i}"} for i in range(n)]


def test_single_citations_resolve_to_real_posts():
    used = sa.resolve_citations("supported by [3] and [7]", _evidence())
    assert [c["n"] for c in used] == [3, 7]
    assert used[0]["url"] == "https://x/2"


def test_grouped_citations_resolve():
    """REGRESSION. Models write [8, 13] as often as [8] [13]. Matching only the
    single form dropped every grouped citation, so a well-sourced answer showed
    zero sources — measured on a live run before the fix."""
    used = sa.resolve_citations("evidence [8, 13] and [2,5]", _evidence())
    assert [c["n"] for c in used] == [8, 13, 2, 5]


def test_an_out_of_range_citation_is_dropped_not_rendered():
    """A fabricated citation is worse than an uncited claim: it looks verified.
    [99] against 20 posts must vanish, not link to nothing."""
    used = sa.resolve_citations("real [4] invented [99]", _evidence())
    assert [c["n"] for c in used] == [4]


def test_a_repeated_citation_is_listed_once():
    used = sa.resolve_citations("[5] and again [5] and [5]", _evidence())
    assert [c["n"] for c in used] == [5]


def test_an_answer_with_no_citations_yields_no_sources():
    assert sa.resolve_citations("a confident claim with nothing behind it", _evidence()) == []


# ── Parsing the model's reply ─────────────────────────────

_REPLY = """##ANSWER##
- Treatment options — patients ask about immunotherapy [1, 2]
- Screening — early detection comes up repeatedly [3]

##SO_WHAT##
Build patient-facing material on treatment sequencing.

##CONFIDENCE##
The posts are mostly organisational; direct patient voice is thin.
"""


def test_a_ranked_reply_parses_into_points():
    out = sa.parse_answer(_REPLY, _evidence(), ranked=True)
    assert len(out["points"]) == 2
    assert out["points"][0].startswith("Treatment options")


def test_so_what_and_confidence_are_both_captured():
    """Both were empty on the first live run because the reply truncated before
    reaching them — the sections a reader needs most to judge the answer."""
    out = sa.parse_answer(_REPLY, _evidence(), ranked=True)
    assert "patient-facing material" in out["so_what"]
    assert "thin" in out["confidence"]


def test_citations_inside_the_answer_are_resolved():
    out = sa.parse_answer(_REPLY, _evidence(), ranked=True)
    assert [c["n"] for c in out["citations"]] == [1, 2, 3]


def test_numbered_list_output_is_accepted_too():
    """Models alternate between "- " and "1. " however the prompt is written."""
    reply = "##ANSWER##\n1. First subject\n2. Second subject\n\n##SO_WHAT##\nAct.\n"
    out = sa.parse_answer(reply, _evidence(), ranked=True)
    assert out["points"] == ["First subject", "Second subject"]


def test_a_truncated_reply_still_yields_what_arrived():
    """Partial output must degrade to a partial answer, not to nothing — the
    reason every long-output endpoint here uses markers rather than JSON."""
    out = sa.parse_answer("##ANSWER##\n- One subject\n- Two subj", _evidence(), ranked=True)
    assert len(out["points"]) >= 1
    assert out["so_what"] == ""


def test_an_open_question_reply_is_kept_as_prose():
    reply = "##ANSWER##\nDoctors are broadly positive about subcutaneous administration [1].\n"
    out = sa.parse_answer(reply, _evidence(), ranked=False)
    assert out["points"] == []
    assert "subcutaneous" in out["answer_text"]


# ── Naming the people ─────────────────────────────────────
#
# "what are the top 5 topics and give me the name of the people" — the client
# wants answers with people in them, not just themes. Every matched post already
# carries an author, so this costs nothing beyond using what is there.

def _authored(author, platform="twitter", voice="organisation", n=1):
    return [{"author": author, "platform": platform, "voice": voice,
             "likes": 5, "comments": 1, "text": f"post by {author}"} for _ in range(n)]


def test_the_loudest_speakers_are_identified():
    evidence = _authored("@GustaveRoussy", n=4) + _authored("@institut_curie", n=2)
    voices = sa.main_voices(evidence, tracked_names=set())
    assert voices[0]["author"] == "@GustaveRoussy"
    assert voices[0]["mentions"] == 4


def test_an_untracked_speaker_is_flagged_as_such():
    """The actionable half: a name near the top that nobody follows yet is a
    candidate KOL, which is the spec's stakeholder-identification ask."""
    from app.services.term_expansion import fold_accents

    evidence = _authored("@GustaveRoussy", n=3) + _authored("dr-unknown-person", n=2)
    tracked = {fold_accents("@GustaveRoussy").lower().lstrip("@")}
    voices = {v["author"]: v for v in sa.main_voices(evidence, tracked_names=tracked)}
    assert voices["dr-unknown-person"]["tracked"] is False


def test_each_speaker_carries_its_voice_bucket():
    """A name is only actionable once you know whether it is a clinician, an
    organisation, or unclassified."""
    evidence = _authored("@laliguecancer", voice="patient", n=2)
    assert sa.main_voices(evidence)[0]["voice"] == "patient"


def test_unattributed_posts_do_not_become_a_speaker():
    """"unknown" is not a person, and listing it as the top voice would be
    absurd on a page the client reads."""
    evidence = _authored("unknown", n=9) + _authored("@institut_curie", n=1)
    names = [v["author"] for v in sa.main_voices(evidence)]
    assert "unknown" not in names


def test_the_prompt_lists_the_speakers_and_asks_the_model_to_name_them():
    voices = [{"author": "@GustaveRoussy", "mentions": 4, "voice": "organisation",
               "tracked": True},
              {"author": "dr-new-voice-12345", "mentions": 2, "voice": "other",
               "tracked": False}]
    prompt = sa.build_prompt("top 5 topics and who is talking about them",
                             [{"text": "x"}], None, "", voices=voices)
    assert "@GustaveRoussy" in prompt and "dr-new-voice-12345" in prompt
    assert "not currently tracked" in prompt
    assert "Name the people" in prompt


def test_the_prompt_forbids_attributing_a_point_to_the_wrong_person():
    """Naming people raises the cost of a hallucination: a fabricated quote
    attributed to a real French clinician is the worst output this can produce."""
    prompt = sa.build_prompt("who is talking about immunotherapy", [{"text": "x"}],
                             None, "", voices=[{"author": "@X", "mentions": 1,
                                                "voice": "kol", "tracked": True}])
    assert "never attribute" in prompt.lower()


def test_no_speakers_leaves_the_prompt_valid():
    prompt = sa.build_prompt("what is being said", [{"text": "x"}], None, "", voices=[])
    assert "##ANSWER##" in prompt
