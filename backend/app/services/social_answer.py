"""Answering a typed question from the social posts that matched it.

The gap this closes, seen directly in client use: Amaury typed "what is the top
5 subject that lung cancer patient want to discuss" and the platform returned
120 posts for him to read. He asked a question and got a search engine. The
material to answer him was on the screen — it just was not read.

Two things make the answer trustworthy rather than merely fluent.

**It is grounded in the retrieved posts and cites them.** Every post handed to
the model is numbered, the model emits `[n]`, and `_resolve_citations` maps each
back to a real row. Out-of-range indices are dropped rather than invented, the
same contract synthesis_report and market_report already use.

**It reports whose voices answered.** The question above is about PATIENTS, and
the posts that matched it were largely Roche and BMS corporate accounts —
answering "what patients want to discuss" out of pharma marketing would be
confidently wrong in a way the reader cannot see. The voice split travels with
the answer so the reader knows whether the evidence fits the question, and the
answer says so in words when it does not.
"""
from __future__ import annotations

import json
import re

import structlog

logger = structlog.get_logger(__name__)

# Enough evidence to be worth a paragraph, few enough to leave the model room to
# answer. Posts are pre-ranked by the search, so this takes the best.
#
# Sized down from 60 after the first live run truncated at 917 characters,
# mid-sentence, with two of three sections missing: gemini-2.5-flash is a
# thinking model, so reasoning tokens come out of the SAME budget as the answer
# (the trap extractor._call_json documents). A shorter prompt and a bigger
# output budget are both needed — one alone does not fix it.
MAX_EVIDENCE = 35

# Per-post excerpt. Social posts are short; 700 characters mostly bought
# boilerplate signatures and hashtag walls.
EVIDENCE_CHARS = 420

# Questions that ask for a ranked list get one. Detected rather than assumed,
# because "what do doctors think about X" wants prose, not a leaderboard.
_RANK_PATTERNS = (
    r"\btop\s+\d+\b", r"\btop\b", r"\bmost\s+(common|discussed|frequent)\b",
    r"\bmain\b", r"\bbiggest\b", r"\bprincipa(l|ux)\b", r"\bles\s+plus\b",
    r"\brank(ed|ing)?\b", r"\bwhich\s+\d+\b",
)


def wants_ranked_list(question: str) -> bool:
    q = (question or "").lower()
    return any(re.search(p, q) for p in _RANK_PATTERNS)


def requested_count(question: str, default: int = 5) -> int:
    """"top 5 subjects" -> 5. Answering with three when five were asked for is
    a wrong answer, not a shorter one."""
    m = re.search(r"\btop\s+(\d{1,2})\b", (question or "").lower())
    if m:
        return max(1, min(int(m.group(1)), 10))
    return default


# Who the question is about, so the answer can check its evidence against it.
# Only asked of the question text, never inferred from the results — the point
# is to notice when the two disagree.
_AUDIENCE_HINTS = {
    "patient": ("patient", "patients", "malade", "malades", "caregiver", "aidant"),
    "doctor": ("doctor", "doctors", "clinician", "médecin", "medecin", "oncologue",
               "pneumologue", "physician", "hcp"),
    "kol": ("kol", "expert", "leader", "specialist", "spécialiste"),
}


def asks_about(question: str) -> str | None:
    """Which voice the question is about, if it names one."""
    q = (question or "").lower()
    for audience, words in _AUDIENCE_HINTS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", q) for w in words):
            return audience
    return None


def evidence_matches_audience(audience: str | None, voices: dict) -> tuple[bool, str]:
    """Does the retrieved evidence actually contain the voice being asked about?

    Returns (ok, note). The note is written for the reader, not for logs — it is
    the difference between an answer they can trust and one they cannot.
    """
    if not audience:
        return True, ""
    from app.services.voice_profile import PATIENT, DOCTOR, KOL, ORGANISATION

    bucket = {"patient": PATIENT, "doctor": DOCTOR, "kol": KOL}[audience]
    matching = voices.get(bucket, 0)
    total = sum(voices.values()) or 1
    share = matching / total

    if matching == 0:
        return False, (
            f"None of the matched posts could be identified as coming from "
            f"{audience}s — most are {ORGANISATION} accounts. The answer below "
            f"describes what is being said ABOUT {audience}s, not BY them.")
    if share < 0.25:
        return False, (
            f"Only {matching} of {total} matched posts ({share:.0%}) come from "
            f"{audience}s; the rest are mostly organisations. Read the answer as "
            f"indicative rather than representative.")
    return True, ""


def main_voices(evidence: list[dict], tracked_names: set[str] | None = None,
                limit: int = 8) -> list[dict]:
    """Who is actually driving this conversation, loudest first.

    The client asks for answers with people in them — "the top 5 topics and give
    me the name of the people". Every matched post already carries an author and
    a voice classification, so naming them costs nothing and turns a list of
    themes into a list of people to approach.

    Reuses market_report._compute_main_authors so "main voices" is computed one
    way across the platform, and keeps its `tracked` flag: an untracked author
    near the top is a candidate KOL nobody is following yet, which is the
    stakeholder-identification ask from the original spec.
    """
    from app.services.market_report import _compute_main_authors

    items = [{
        "author": item.get("author"),
        "engagement": item.get("likes", 0) + item.get("comments", 0),
        "platform": item.get("platform"),
        "is_tracked_kol": False,
        "target_type": None,
    } for item in evidence]
    voices = _compute_main_authors(items, tracked_names or set(), limit=limit)

    # Carry each author's voice bucket through, so the reader can see whether a
    # name is a clinician, an organisation or unclassified before acting on it.
    bucket_by_author = {}
    for item in evidence:
        name = (item.get("author") or "").strip().lower()
        if name and name not in bucket_by_author:
            bucket_by_author[name] = item.get("voice")
    for entry in voices:
        entry["voice"] = bucket_by_author.get(entry["author"].strip().lower())
    return voices


def build_prompt(question: str, evidence: list[dict], audience: str | None,
                 voice_note: str, voices: list[dict] | None = None) -> str:
    """The answering prompt. Section markers, not JSON — long JSON gets truncated
    mid-string and silently fails to parse, which is why every other long-output
    endpoint here uses markers."""
    ranked = wants_ranked_list(question)
    count = requested_count(question)

    numbered = "\n\n".join(
        f"[{i}] ({item.get('platform', '?')} · {item.get('voice', 'unknown')} voice · "
        f"{item.get('author') or 'unknown author'})\n{(item.get('text') or '')[:EVIDENCE_CHARS]}"
        for i, item in enumerate(evidence, 1))

    shape = (
        f"##ANSWER##\nExactly {count} items, each on its own line starting '- '. "
        f"Order them by how often and how strongly the posts support them, most "
        f"prominent first. After each item add ' — ' and one sentence of evidence, "
        f"citing the posts it came from as [n].\n"
        if ranked else
        "##ANSWER##\n3-5 sentences answering the question directly, citing posts "
        "as [n]. Lead with the answer, not with context.\n"
    )

    # Give the model the speakers explicitly. Without this it can only cite [n],
    # and the client asked for the PEOPLE, not just the evidence indices.
    speakers = ""
    if voices:
        listed = ", ".join(
            f"{v['author']} ({v.get('voice') or 'unclassified'}, {v['mentions']} posts"
            f"{', not currently tracked' if not v.get('tracked') else ''})"
            for v in voices[:8])
        speakers = (
            f"\nThe most active speakers in these posts are: {listed}.\n"
            "Name the people behind each point where the posts support it. Use "
            "the names exactly as given, and never attribute a point to someone "
            "whose posts do not support it.\n")

    audience_rule = (
        f"\nThe question asks about {audience}s. Say plainly when a point comes "
        f"from an organisation's own messaging rather than from {audience}s "
        f"themselves — the reader must be able to tell the two apart.\n"
        if audience else "")

    return (
        "You are a pharma intelligence analyst for a French medical-affairs team.\n"
        "Answer the question BELOW using only the numbered posts provided. Do not "
        "use outside knowledge, and do not pad the answer with what you already "
        "know about the disease.\n\n"
        f"QUESTION: {question}\n"
        f"{speakers}"
        f"{audience_rule}"
        f"{('EVIDENCE CAVEAT: ' + voice_note) if voice_note else ''}\n\n"
        "Output EXACTLY these sections, each starting with its marker:\n\n"
        f"{shape}\n"
        "##SO_WHAT##\n2-3 sentences on what a Roche medical-affairs team should "
        "do about this. Be specific; name the action.\n\n"
        "##CONFIDENCE##\nOne sentence: how well the posts actually support this "
        "answer, and what is missing. If the evidence is thin or comes from the "
        "wrong voices, say so plainly rather than hedging.\n\n"
        f"POSTS:\n{numbered}"
    )


_MARKER = r"##{name}##\s*(.*?)(?=##[A-Z_]+##|$)"


def _section(text: str, name: str) -> str:
    m = re.search(_MARKER.format(name=name), text or "", re.DOTALL | re.IGNORECASE)
    return (m.group(1).strip() if m else "")


def _bullets(block: str) -> list[str]:
    out = []
    for line in (block or "").splitlines():
        line = line.strip()
        if line.startswith(("- ", "• ", "* ")):
            out.append(line[2:].strip())
        elif re.match(r"^\d+[.)]\s+", line):
            out.append(re.sub(r"^\d+[.)]\s+", "", line).strip())
    return [b for b in out if b]


def resolve_citations(text: str, evidence: list[dict]) -> list[dict]:
    """Map the [n] markers the model emitted back to real posts.

    Indices outside the evidence list are DROPPED. A model that invents [99]
    must not produce a source link to nothing — a fabricated citation is worse
    than an uncited claim, because it looks verified.
    """
    used = []
    seen = set()
    # Models cite as [8], [8, 13] and [8,13] interchangeably. Matching only the
    # single-index form silently dropped every grouped citation — the answer
    # looked uncited when it was in fact well sourced.
    indices = []
    for group in re.findall(r"\[([\d,\s]+)\]", text or ""):
        for part in group.split(","):
            part = part.strip()
            if part.isdigit():
                indices.append(int(part))
    for idx in indices:
        if 1 <= idx <= len(evidence) and idx not in seen:
            seen.add(idx)
            item = evidence[idx - 1]
            used.append({
                "n": idx,
                "platform": item.get("platform"),
                "author": item.get("author"),
                "voice": item.get("voice"),
                "url": item.get("url"),
                "excerpt": (item.get("text") or "")[:200],
            })
    return used


def parse_answer(raw: str, evidence: list[dict], ranked: bool) -> dict:
    answer_block = _section(raw, "ANSWER")
    points = _bullets(answer_block) if ranked else []
    return {
        "points": points,
        "answer_text": answer_block if not points else "",
        "so_what": _section(raw, "SO_WHAT"),
        "confidence": _section(raw, "CONFIDENCE"),
        "citations": resolve_citations(raw, evidence),
    }


def dedupe_evidence(posts: list[dict]) -> list[dict]:
    """Drop reposts of identical text before they reach the model.

    The same Roche "Behind every #LungCancer diagnosis" post appeared four times
    in one result set. Repetition is not evidence — left in, it makes the model
    read a single corporate message as the dominant theme, and it burns prompt
    space that real variety needed.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for post in posts:
        key = " ".join((post.get("text") or "").lower().split())[:160]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(post)
    return out
