"""Conservative Item 1 extraction from an SEC complete submission."""
from __future__ import annotations

from html.parser import HTMLParser
import re


class ExtractionError(RuntimeError):
    pass


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "table", "section", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.suppressed += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.suppressed:
            self.suppressed -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def primary_text(raw: bytes, expected_form: str = "10-K") -> tuple[str, dict]:
    if expected_form not in {"10-K", "10-Q"}:
        raise ExtractionError("Only original 10-K and 10-Q forms are supported")
    decoded = raw.decode("utf-8", errors="replace")
    if decoded.count("\ufffd") > max(20, len(decoded) // 1000):
        raise ExtractionError("Too many UTF-8 replacement characters in SEC submission")
    documents = list(re.finditer(r"<DOCUMENT>\s*(.*?)\s*</DOCUMENT>", decoded, re.I | re.S))
    primary = []
    for doc in documents:
        content = doc.group(1)
        kind = re.search(r"<TYPE>\s*([^\r\n<]+)", content, re.I)
        if kind and kind.group(1).strip().upper() == expected_form:
            primary.append((doc, content))
    if len(primary) != 1:
        raise ExtractionError(f"Expected one primary TYPE {expected_form} document; found {len(primary)}")
    doc, content = primary[0]
    match = re.search(r"<TEXT>\s*(.*?)\s*</TEXT>", content, re.I | re.S)
    if not match:
        raise ExtractionError(f"Primary {expected_form} has no closed TEXT block")
    html = match.group(1)
    parser = _Text()
    parser.feed(html)
    parser.close()
    rendered = "".join(parser.parts)
    rendered = rendered.replace("\xa0", " ").replace("\r", "")
    rendered = re.sub(r"[\t ]+", " ", rendered)
    rendered = re.sub(r" *\n *", "\n", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip() + "\n"
    if len(rendered) < 2000:
        raise ExtractionError(f"Primary {expected_form} rendered text is implausibly short")
    return rendered, {"primary_document_char_start_in_decoded_submission": doc.start(),
                      "primary_document_char_end_in_decoded_submission": doc.end(),
                      "utf8_replacement_characters": decoded.count("\ufffd"),
                      "rendered_characters": len(rendered)}


_ITEM1 = re.compile(r"^[ \t]*item[ \t]+1[ \t]*[.·:\-]?[ \t]*business[ \t]*$", re.I | re.M)
_NEXT = re.compile(r"^[ \t]*item[ \t]+(?:1[ \t]*a|1[ \t]*b|2)[ \t]*[.·:\-]?[ \t]*(?:risk[ \t]+factors|unresolved[ \t]+staff[ \t]+comments|properties)?[ \t]*$", re.I | re.M)


def extract_item1(text: str) -> tuple[str, dict]:
    starts = list(_ITEM1.finditer(text))
    ends = list(_NEXT.finditer(text))
    candidates: list[tuple[int, int, str]] = []
    for match in starts:
        next_heading = next((h for h in ends if h.start() > match.end()), None)
        if next_heading is None:
            continue
        start, end = match.start(), next_heading.start()
        content = text[start:end]
        if len(content) >= 1000 and len(content.split()) >= 150:
            candidates.append((start, end, content))
    if not candidates:
        raise ExtractionError("No substantive Item 1 Business with a following section boundary")
    candidates.sort(key=lambda c: len(c[2]), reverse=True)
    if len(candidates) > 1 and len(candidates[1][2]) >= 1000:
        raise ExtractionError("Multiple substantive Item 1 candidates; manual resolution required")
    start, end, content = candidates[0]
    if len(content) > len(text) * 0.7:
        raise ExtractionError("Item 1 span is implausibly large relative to the primary filing")
    if _ITEM1.search(content[30:]):
        raise ExtractionError("Repeated Item 1 heading inside selected section")
    return content, {"start_char": start, "end_char_exclusive": end, "characters": len(content),
                     "word_count": len(content.split()), "start_heading_count": len(starts),
                     "next_heading_count": len(ends), "substantive_candidate_count": len(candidates)}


def comparison(text: str, item: str, offsets: dict) -> dict:
    start, end = offsets["start_char"], offsets["end_char_exclusive"]
    if text[start:end] != item:
        raise ExtractionError("Item 1 is not an exact substring of rendered primary text")
    return {
        "source_start_before": text[max(0, start - 240):start],
        "source_start_at": text[start:start + 240],
        "extracted_start": item[:240],
        "source_end_before": text[max(start, end - 240):end],
        "extracted_end": item[-240:],
        "source_end_after": text[end:end + 240],
        "exact_substring": True,
    }


