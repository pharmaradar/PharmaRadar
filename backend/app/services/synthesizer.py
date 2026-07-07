"""Shared synthesis parsing for the social / discovery / dashboard 'takeaway' panels.

LLM output uses section markers (not JSON) so partial/truncated responses still
parse — same convention as the dashboard briefs. List items are one-per-line so a
cut response just loses the last item instead of failing the whole parse.

Markers used across surfaces:

    ##TAKEAWAY##       what is happening
    ##SO_WHAT##        so what for pharma / Roche
    ##CONCLUSION##     the bottom line / what to focus on
    ##PICKS##          [12] one-line why it matters   (id-resolved highlights)
    ##FOCUS##          - bullet of what to focus on   (plain text, no id)
"""
from __future__ import annotations

import re

_PICK_RE = re.compile(r'\s*\[(\d+)\]\s*(.*)')
_BULLET_PREFIX_RE = re.compile(r'^\s*(?:[-*•]|\d+[.)]|\[\d+\])\s*')
_ENDS_CLEAN_RE = re.compile(r'[.!?]["\')\]]?\s*$')
_SENT_END_RE = re.compile(r'[.!?]["\')\]]?')


def trim_incomplete(text: str) -> str:
    """Drop a trailing half-sentence so a truncated LLM response never shows a
    dangling fragment (e.g. '...Daiichi Sankyo/AstraZeneca's Datopot')."""
    text = (text or "").strip()
    if not text or _ENDS_CLEAN_RE.search(text):
        return text
    ends = list(_SENT_END_RE.finditer(text))
    return text[:ends[-1].end()].strip() if ends else text


def extract_section(raw: str, name: str) -> str:
    m = re.search(rf'##{name}##\s*(.*?)(?=##[A-Z_]+##|$)', raw, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def parse_picks(picks_text: str) -> list[dict]:
    """Lines like '[12] why it matters' -> [{'id': 12, 'why': '...'}]."""
    out: list[dict] = []
    for line in picks_text.splitlines():
        m = _PICK_RE.match(line)
        if m:
            out.append({"id": int(m.group(1)), "why": m.group(2).strip()})
    return out


def parse_bullets(text: str) -> list[str]:
    """Plain bullet lines (strips -, *, •, '1.', '[1]' prefixes)."""
    out: list[str] = []
    for line in text.splitlines():
        s = _BULLET_PREFIX_RE.sub("", line).strip()
        if s:
            out.append(s)
    return out


def parse_synthesis(raw: str) -> dict:
    """For social / discovery panels: takeaway + so_what + conclusion + id picks."""
    return {
        "takeaway": trim_incomplete(extract_section(raw, "TAKEAWAY")),
        "so_what": trim_incomplete(extract_section(raw, "SO_WHAT")),
        "conclusion": trim_incomplete(extract_section(raw, "CONCLUSION")),
        "picks": parse_picks(extract_section(raw, "PICKS")),
    }
