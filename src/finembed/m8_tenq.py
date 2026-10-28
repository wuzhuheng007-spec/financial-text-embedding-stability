"""Generic, fail-closed 10-Q Item 2 candidate and period-hint logic.

These are program checks for saved original filings, never researcher decisions.
"""
from __future__ import annotations

import hashlib
import re

from .qpilot import (_H_ITEM2, _H_ITEM3, _H_PART_I, _H_PART_II,
                     _lines, _normalize)


_PERIOD = re.compile(
    r"\b(?:(?:three|six|nine|twelve)\s+months?|(?:trailing|rolling|last)\s+twelve\s+months?|"
    r"year|quarter)\s+(?:ended|ending)|\bas\s+of\b", re.I)
_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},?\s+\d{4}\b|\b20\d{2}-\d{2}-\d{2}\b", re.I)
_UNIT = re.compile(r"(?i)\b(?:dollars?\s+in\s+(?:millions?|billions?|thousands?)|"
                   r"(?:in\s+)?(?:millions?|billions?)\s+of\s+dollars?|"
                   r"in\s+(?:millions?|billions?|thousands?)|percent(?:age)?s?)\b")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _heading(lines: list[tuple[int, int, str]], index: int,
             number: int, pattern: re.Pattern) -> tuple[int, int, str] | None:
    """Allow `Item 2.Management` and wrapped titles without accepting Item 2 of Part II."""
    first = _normalize(lines[index][2])
    if not re.match(rf"^item\s+{number}\s*[.·:\-]?(?:\s|$|(?=management)|(?=quantitative))", first):
        return None
    components: list[str] = []
    for j in range(index, min(index + 5, len(lines))):
        part = _normalize(lines[j][2])
        if j > index and re.match(r"^(?:item\s+[0-9]|part\s+[ivx])", part):
            break
        if part:
            components.append(part)
        title = " ".join(components)
        if pattern.match(title):
            return lines[index][0], lines[j][1], title
        if len(title) > 240:
            break
    return None


def boundary_candidates_generic(text: str) -> dict:
    """Keep all TOC/body candidates; select only one long Item 2 -> Item 3 body."""
    lines = _lines(text)
    starts = [x for i in range(len(lines)) if (x := _heading(lines, i, 2, _H_ITEM2))]
    ends = [x for i in range(len(lines)) if (x := _heading(lines, i, 3, _H_ITEM3))]
    parts_i = [a for a, _b, value in lines if _H_PART_I.fullmatch(_normalize(value))]
    parts_ii = [a for a, _b, value in lines if _H_PART_II.fullmatch(_normalize(value))]
    candidates: list[dict] = []
    for start, heading_end, heading in starts:
        following = next((entry for entry in ends if entry[0] > heading_end), None)
        end = following[0] if following else None
        content = text[start:end] if end is not None else None
        words = len(content.split()) if content is not None else None
        long_body = (content is not None and len(content) >= 5000 and words >= 500
                     and len(content) <= 0.6 * len(text))
        classification = ("PROGRAM_SUBSTANTIVE_BODY_CANDIDATE" if long_body else
                          "TOC_OR_SHORT_CANDIDATE" if content is not None and len(content) < 5000 else
                          "NEEDS_REVIEW")
        candidates.append({
            "start_char": start, "heading_end_char_exclusive": heading_end,
            "end_char_exclusive": end, "heading": heading,
            "next_heading": following[2] if following else None,
            "characters": len(content) if content is not None else None,
            "words": words, "content_sha256": _sha(content) if content else None,
            "classification": classification,
            "preceding_part_i_char": max((p for p in parts_i if p < start), default=None),
            "following_part_ii_char": min((p for p in parts_ii if p > start), default=None),
            "start_context": text[max(0, start - 300):min(len(text), start + 500)],
            "end_context": text[max(0, end - 400):min(len(text), end + 350)] if end is not None else None,
        })
    long_indices = [i for i, candidate in enumerate(candidates)
                    if candidate["classification"] == "PROGRAM_SUBSTANTIVE_BODY_CANDIDATE"]
    selected_index = long_indices[0] if len(long_indices) == 1 else None
    selected = candidates[selected_index] if selected_index is not None else None
    return {
        "status": "PROGRAM_BOUNDARY_PASS" if selected is not None else "NEEDS_REVIEW",
        "selected_candidate_index": selected_index,
        "selected_span": ({"start_char": selected["start_char"],
                           "end_char_exclusive": selected["end_char_exclusive"],
                           "section_sha256": selected["content_sha256"]} if selected else None),
        "item2_heading_count": len(starts), "item3_heading_count": len(ends),
        "part_i_standalone_heading_count": len(parts_i),
        "part_ii_standalone_heading_count": len(parts_ii),
        "substantive_candidate_count": len(long_indices), "candidates": candidates,
        "selection_rule": "one >=5000-character/>=500-word Item 2-to-following-Item 3 span occupying <=60% of normalized primary text; all candidates retained",
        "limitations": ["A long program candidate still needs human semantic start/end and table review",
                        "Part I/II standalone lines may be absent in valid filing layouts"],
        "human_review": "PENDING_HUMAN_REVIEW",
    }


def period_hints(text: str, *, offset: int = 0, max_hits: int | None = None) -> list[dict]:
    """Preserve explicit period phrases with source offsets, not inferred coverage."""
    out: list[dict] = []
    for match in _PERIOD.finditer(text):
        start = match.start()
        segment = text[start:min(len(text), start + 190)]
        segment = segment.split("\n\n", 1)[0]
        if len(segment) > 190:
            segment = segment[:190]
        normalized = " ".join(segment.split())
        phrase = " ".join(match.group(0).split())
        out.append({"phrase": phrase, "source_start_char": offset + start,
                    "source_end_char_exclusive": offset + start + len(segment),
                    "source_excerpt": segment, "source_excerpt_sha256": _sha(segment),
                    "date_mentions_in_excerpt": [m.group(0) for m in _DATE.finditer(normalized)],
                    "status": "TEXT_MATCH_NEEDS_CONTEXT_REVIEW"})
        if max_hits is not None and len(out) >= max_hits:
            break
    return out


def unit_hints(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _UNIT.finditer(text)))


def fiscal_focus(html: str) -> tuple[str | None, str | None]:
    """Read iXBRL DEI fields, retaining missing values as null."""
    def value(name: str) -> str | None:
        match = re.search(rf'<ix:nonNumeric\b[^>]*\bname\s*=\s*["\']dei:{name}["\'][^>]*>(.*?)</ix:nonNumeric>',
                          html, re.I | re.S)
        if not match:
            return None
        result = re.sub(r"<[^>]+>", "", match.group(1)).strip().upper()
        return result if re.fullmatch(r"[A-Z0-9-]{1,16}", result) else None
    return value("DocumentFiscalYearFocus"), value("DocumentFiscalPeriodFocus")


def page_and_character_anomalies(section: str) -> dict:
    controls = [hex(ord(char)) for char in section if ord(char) < 32 and char not in "\r\n\t"]
    return {"replacement_character_count": section.count("\ufffd"),
            "unexpected_control_characters_first_20": controls[:20],
            "unexpected_control_count": len(controls),
            "form_10q_footer_mentions": len(re.findall(r"(?i)form\s+10-q\s*[|·:]\s*\d+", section)),
            "cross_reference_mentions": len(re.findall(r"(?i)\b(?:see|refer to)\s+(?:note|item|part|section)\b", section)),
            "review_status": "PROGRAM_COUNTS_ONLY_NEEDS_HUMAN_REVIEW"}
