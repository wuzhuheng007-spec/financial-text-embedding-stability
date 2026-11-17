"""Freeze source-located English narrative before opening any return labels.

Every eligible narrative node is considered throughout the exhibit. Paragraphs
are split at sentence/word boundaries only when needed for the fixed tokenizer.
The selector does not read prices, scores, reference facts, or outcomes.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup
from transformers import AutoTokenizer

from .source import CONFIG, PRIVATE, PUBLIC, digest, now, write_json

MODEL = Path(__file__).resolve().parents[2] / "artifacts/news_sentiment_factor_v2_20261117/inputs/finbert_2022"
SECTIONS = {
    "RISK_OR_CONDITIONS": re.compile(r"\b(risk|uncertain|subject to|may not|could adversely|challeng|headwind|forward.looking|caution|volatil|tariff|regulator|constraint)\w*\b", re.I),
    "GUIDANCE": re.compile(r"\b(outlook|guidance|guides?|expects?|anticipates?|forecast\w*|project\w*|next quarter\b|upcoming quarter\b|future fiscal|plan to|target\w*)\b", re.I),
    "FINANCIAL_REVIEW": re.compile(r"\b(revenues?|net income|net loss|earnings|gross margins?|operating income|operating margin|eps|diluted shares?|cash flows?|capital expenditure|capex|profits?|fiscal quarter|financial results)\b", re.I),
    "OPERATING_DISCUSSION": re.compile(r"\b(demand|customer|product|segment|data center|datacenter|cloud|shipment|supply|manufactur|production|inventory|market|business|growth|sales|capacity|memory|hbm|semiconductor|services)\w*\b", re.I),
}
EXCLUDE_PREFIX = ("contacts:", "investor relations", "media contact", "about this release", "copyright")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
CONTENTS_LIKE = re.compile(r"(?is)(?:highlights|financial summary).{0,150}(?:operational summary).{0,150}(?:outlook)")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def locator(node) -> str:
    parts = []
    while getattr(node, "name", None) and node.name != "[document]":
        siblings = [s for s in node.previous_siblings if getattr(s, "name", None) == node.name]
        parts.append(f"{node.name}[{len(siblings) + 1}]")
        node = node.parent
    return "/" + "/".join(reversed(parts))


def source_nodes(raw: bytes):
    soup = BeautifulSoup(raw, "html.parser")
    seen = set()
    for node in soup.find_all(["p", "div", "li"]):
        if node.find_parent("table") or node.find("table") or node.find(["p", "div", "li"]):
            continue
        value = " ".join(node.get_text(" ", strip=True).split())
        if not value or value in seen:
            continue
        seen.add(value)
        yield locator(node), value


def section_of(text: str) -> str | None:
    for name in ("RISK_OR_CONDITIONS", "GUIDANCE", "FINANCIAL_REVIEW", "OPERATING_DISCUSSION"):
        if SECTIONS[name].search(text):
            return name
    return None


def split_with_offsets(text: str, tokenizer, max_tokens: int):
    # This is a model-input offset into normalized DOM-node text, not a raw byte offset.
    if len(tokenizer.encode(text, add_special_tokens=True)) <= max_tokens:
        yield 0, len(text), text
        return
    sentences = []
    start = 0
    for match in SENTENCE.finditer(text):
        sentences.append((start, match.start(), text[start:match.start()].strip()))
        start = match.end()
    sentences.append((start, len(text), text[start:].strip()))
    for begin, end, sentence in sentences:
        if not sentence:
            continue
        if len(tokenizer.encode(sentence, add_special_tokens=True)) <= max_tokens:
            yield begin, end, sentence
            continue
        # A long sentence is partitioned at whitespace; no words are discarded.
        words = list(re.finditer(r"\S+", sentence))
        i = 0
        while i < len(words):
            lo, hi = i + 1, len(words)
            best = i
            while lo <= hi:
                mid = (lo + hi) // 2
                piece = sentence[words[i].start():words[mid - 1].end()]
                if len(tokenizer.encode(piece, add_special_tokens=True)) <= max_tokens:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            if best == i:
                raise ValueError("SINGLE_WORD_OVER_TOKEN_BUDGET")
            a = begin + words[i].start()
            b = begin + words[best - 1].end()
            yield a, b, text[a:b]
            i = best


def freeze() -> dict:
    target = PRIVATE / "text_input_private.json"
    if target.exists():
        raise FileExistsError("TEXT_INPUT_ALREADY_FROZEN")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    review_path = PRIVATE / "source_review_private.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    max_tokens = config["text_selection"]["max_finbert_tokens_including_special"]
    rows, ledger = [], []
    for source in sorted(review["rows"], key=lambda x: (x["acceptanceDateTime"], x["accessionNumber"])):
        if source["review_status"] != "ACCEPT_EARNINGS_EX99_1_SOURCE_HEADING":
            continue
        path = Path(source["exhibit_private_path"])
        if digest(path) != source["exhibit_sha256"]:
            raise ValueError("EXHIBIT_HASH_CHANGED")
        counts = Counter()
        seen_text = set()
        for dom_path, paragraph in source_nodes(path.read_bytes()):
            counts["all_unique_narrative_nodes"] += 1
            if len(paragraph) < 50 or len(re.findall(r"[A-Za-z]", paragraph)) < 35:
                counts["excluded_short_or_non_english"] += 1
                continue
            if paragraph.casefold().startswith(EXCLUDE_PREFIX):
                counts["excluded_fixed_contact_prefix"] += 1
                continue
            fingerprint = sha(paragraph.casefold())
            if fingerprint in seen_text:
                counts["excluded_duplicate_text_within_event"] += 1
                continue
            seen_text.add(fingerprint)
            selected_sections_for_node = set()
            for begin, end, piece in split_with_offsets(paragraph, tokenizer, max_tokens):
                if len(tokenizer.encode(piece, add_special_tokens=True)) > max_tokens:
                    raise ValueError("SPLIT_TOKEN_BUDGET_FAILURE")
                alpha_words = len(re.findall(r"[A-Za-z]+", piece))
                numbers = len(re.findall(r"(?<![A-Za-z])[$€£]?\(?\d[\d,.]*\)?%?", piece))
                if CONTENTS_LIKE.search(piece) or (numbers > 8 and numbers > alpha_words / 3):
                    counts["excluded_table_or_contents_like_chunk"] += 1
                    continue
                section = section_of(piece)
                if not section:
                    counts["excluded_unclassified_chunk"] += 1
                    continue
                identifier = sha(source["accessionNumber"] + "|" + dom_path + "|" + str(begin) + "|" + piece)[:24]
                rows.append({"block_id": identifier, "symbol": source["symbol"],
                             "event_id": source["accessionNumber"], "section": section,
                             "source_sha256": source["exhibit_sha256"],
                             "source_path": str(path), "dom_path": dom_path,
                             "normalized_node_offset": [begin, end],
                             "acceptance_utc": source["header_acceptance_utc"],
                             "text": piece, "text_sha256": sha(piece)})
                counts[f"selected_blocks_{section}"] += 1
                selected_sections_for_node.add(section)
            for section in selected_sections_for_node:
                counts[f"selected_nodes_{section}"] += 1
        ledger.append({"event_id": source["accessionNumber"], "symbol": source["symbol"],
                       "source_sha256": source["exhibit_sha256"], "counts": dict(counts),
                       "selected_sections": sorted({x["section"] for x in rows if x["event_id"] == source["accessionNumber"]})})
    if not rows or len({x["block_id"] for x in rows}) != len(rows):
        raise ValueError("EMPTY_OR_DUPLICATE_TEXT_INPUT")
    write_json(target, {"schema": "sentiment_v3_text_input_private_1", "frozen_at_utc": now(),
                        "config_sha256": digest(CONFIG), "source_review_sha256": digest(review_path),
                        "selector_sha256": digest(Path(__file__)), "model_tokenizer_path": str(MODEL),
                        "rows": rows, "ledger": ledger})
    aggregate = Counter()
    for item in ledger:
        aggregate.update(item["counts"])
    receipt = {"schema": "sentiment_v3_text_freeze_redacted_1", "frozen_at_utc": now(),
               "config_sha256": digest(CONFIG), "source_review_sha256": digest(review_path),
               "selector_sha256": digest(Path(__file__)), "input_sha256": digest(target),
               "events": len(ledger), "blocks": len(rows), "sections": dict(Counter(x["section"] for x in rows)),
               "aggregate_selection_counts": dict(aggregate),
               "event_coverage": [{k: item[k] for k in ("event_id", "symbol", "selected_sections", "counts")} for item in ledger],
               "outcome_labels_opened": False, "same_team_review": "PENDING_SOURCE_SPOT_CHECK"}
    write_json(PUBLIC / "text_freeze_redacted.json", receipt)
    return {"events": len(ledger), "blocks": len(rows), "sections": receipt["sections"]}


if __name__ == "__main__":
    print(json.dumps(freeze()))
