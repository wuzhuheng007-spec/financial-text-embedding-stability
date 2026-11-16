"""Source-preserving titles for owner-only supplier reports."""
from __future__ import annotations

import re

SENTENCE = re.compile(r"^(.+?[。！？!?]|.+?\.(?=\s|$))", re.S)


def source_title(title: str | None, content: str | None) -> tuple[str, str]:
    if title and title.strip():
        return title.strip(), "SUPPLIER_TITLE"
    body = " ".join((content or "").split())
    match = SENTENCE.search(body)
    if match:
        return match.group(1).strip(), "SOURCE_FIRST_SENTENCE"
    if body:
        return body[:120] + ("…" if len(body) > 120 else ""), "SOURCE_EXCERPT_INCOMPLETE"
    return "原文未提供标题或正文", "NO_SOURCE_TEXT"
