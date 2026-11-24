"""Conservative local language gate for source news shown in the portal."""
import re
from functools import lru_cache
from html import unescape
from unicodedata import name

from langdetect import DetectorFactory, LangDetectException, detect_langs

DetectorFactory.seed = 0
_TAG = re.compile(r"<[^>]*>")
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7ff\uffa0-\uffdc]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@lru_cache(maxsize=4096)
def _english(text: str) -> bool:
    try:
        best = detect_langs(text)[0]
    except LangDetectException:
        return False
    return best.lang == "en" and best.prob >= 0.90


def is_english_news(title: str, teaser: str, body: str) -> bool:
    fields = [_TAG.sub(" ", unescape(value or "")).strip() for value in (title, teaser, body)]
    if not fields[0] or any(_HANGUL.search(value) for value in fields):
        return False
    if any(ch.isalpha() and "LATIN" not in name(ch, "") for value in fields for ch in value):
        return False
    # Check each visible field independently. A long English article cannot
    # compensate for a non-English headline, teaser or body.
    visible = [value[:1800] for value in fields if value]
    return all(len(_WORD.findall(value)) >= 2 and _english(value) for value in visible)
