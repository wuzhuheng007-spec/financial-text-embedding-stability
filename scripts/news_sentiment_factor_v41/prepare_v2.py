"""Freeze markup-free model text with raw HTML source coordinates before inference."""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from html.parser import HTMLParser

from scripts.quant_personal_feed.event_rules import ENTITY_RULES, RULE_VERSION, classify
from .freeze import MODEL, PRIVATE, PUBLIC, ROOT, digest, sha, write
from .prepare import punctuation_spans, review_selection, semantic_kind, token_safe_spans

CFG = ROOT / "configs/news_sentiment_factor_v41_20261117_r2.json"


class MappedHTML(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "section", "article", "tr", "td", "th"}

    def __init__(self, raw: str):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.line_offsets = [0]
        for m in re.finditer("\n", raw):
            self.line_offsets.append(m.end())
        self.chars: list[str] = []
        self.spans: list[tuple[int, int]] = []
        self.hidden = 0

    def at(self) -> int:
        line, col = self.getpos()
        return self.line_offsets[line - 1] + col

    def sep(self) -> None:
        if self.chars and self.chars[-1] != "\n":
            pos = self.at()
            self.chars.append("\n")
            self.spans.append((pos, pos))

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in self.BLOCK and not self.hidden:
            self.sep()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in self.BLOCK and not self.hidden:
            self.sep()

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        start = self.at()
        for i, char in enumerate(data):
            self.chars.append(char)
            self.spans.append((start + i, start + i + 1))

    def handle_entityref(self, name: str) -> None:
        if self.hidden:
            return
        source = f"&{name};"
        start = self.at()
        for char in html.unescape(source):
            self.chars.append(char)
            self.spans.append((start, start + len(source)))

    def handle_charref(self, name: str) -> None:
        if self.hidden:
            return
        source = f"&#{name};"
        start = self.at()
        for char in html.unescape(source):
            self.chars.append(char)
            self.spans.append((start, start + len(source)))


def clean_with_map(raw: str) -> tuple[str, list[tuple[int, int]], str]:
    if not re.search(r"</?[A-Za-z][^>]*>", raw):
        return raw, [(i, i + 1) for i in range(len(raw))], "PLAIN_IDENTITY"
    parser = MappedHTML(raw)
    parser.feed(raw)
    parser.close()
    return "".join(parser.chars), parser.spans, "HTMLPARSER_TEXT_NODE_MAP"


def article_segments(article: dict, tokenizer, max_tokens: int) -> tuple[list[dict], dict]:
    title, title_map, title_method = clean_with_map(article["title"])
    body, body_map, body_method = clean_with_map(article["body"])
    event = classify({"title": title, "content": body,
                      "published_at_utc": article.get("published_at_utc"),
                      "received_at": article["received_at_utc"]})
    rows = []
    for kind, clean, mapping, method in (("TITLE", title, title_map, title_method),
                                         ("BODY_SEGMENT", body, body_map, body_method)):
        spans = [(0, len(clean))] if kind == "TITLE" and clean.strip() else punctuation_spans(clean)
        n = 0
        for a, b in spans:
            for x, y, token_count in token_safe_spans(clean, a, b, tokenizer, max_tokens):
                excerpt = clean[x:y]
                if not excerpt.strip():
                    continue
                n += 1
                labels = classify({"content": excerpt})
                companies = [e for e in labels["entities"] if ENTITY_RULES[e["id"]][1].startswith("COMPANY")]
                raw_start = mapping[x][0]
                raw_end = mapping[y - 1][1]
                rows.append({
                    "provider": article["provider"], "article_id": article["article_id"],
                    "version": article["version"], "segment_id": ("T" if kind == "TITLE" else "B") + f"{n:04d}",
                    "kind": kind, "source_field": "title" if kind == "TITLE" else "body",
                    "clean_start": x, "clean_end": y, "source_start": raw_start, "source_end": raw_end,
                    "source_mapping_method": method, "text": excerpt,
                    "input_sha256": digest(excerpt.encode("utf-8")), "token_count": token_count,
                    "source_text_hash": article["source_text_hash"],
                    "received_at_utc": article["received_at_utc"],
                    "published_at_utc": article.get("published_at_utc"),
                    "event_group_id": event["event_group_id"], "grouping_basis": event["grouping_basis"],
                    "entity_rule_version": RULE_VERSION,
                    "entity_candidates": [{"id": e["id"], "phrase": e["phrase"]} for e in companies],
                    "company_association": companies[0]["id"] if len(companies) == 1 else None,
                    "association_status": "ONE_EXPLICIT_COMPANY" if len(companies) == 1 else
                                          "MULTI_COMPANY_UNRESOLVED" if companies else "NO_EXPLICIT_COMPANY",
                    "semantic_kind_candidate": semantic_kind(excerpt),
                })
                assert 0 <= raw_start <= raw_end <= len(article["title"] if kind == "TITLE" else article["body"])
                assert token_count <= max_tokens
        concatenated = "".join(x["text"] for x in rows if x["kind"] == kind)
        assert re.sub(r"\s+", "", concatenated) == re.sub(r"\s+", "", clean), "NONWHITESPACE_LOST"
    return rows, {"provider": article["provider"], "article_id": article["article_id"],
                  "version": article["version"], "title_method": title_method, "body_method": body_method,
                  "title_original_chars": len(article["title"]), "title_visible_chars": len(title),
                  "body_original_chars": len(article["body"]), "body_visible_chars": len(body),
                  "visible_body_sha256": digest(body.encode("utf-8"))}


def main() -> dict:
    if (PRIVATE / "segments_v2_private.json").exists():
        raise FileExistsError("V41_V2_SEGMENTS_ALREADY_FROZEN")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    if sha(PRIVATE / "input_manifest_private.json") != cfg["frozen_input_manifest_sha256"]:
        raise ValueError("FROZEN_INPUT_DRIFT")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False)
    inputs = json.loads((PRIVATE / "input_manifest_private.json").read_text(encoding="utf-8"))["rows"]
    rows, maps = [], []
    for article in inputs:
        current, stats = article_segments(article, tokenizer, cfg["scoring"]["max_tokens_including_special"])
        rows.extend(current)
        maps.append(stats)
    selected = review_selection(rows, cfg["scoring"]["semantic_review_max"])
    write(PRIVATE / "segments_v2_private.json", rows)
    write(PRIVATE / "source_map_v2_private.json", maps)
    write(PRIVATE / "review_units_v2_private.json", selected)
    public = {"schema": "news_sentiment_v41_segment_manifest_r2_redacted_1",
              "config_sha256": sha(CFG), "input_manifest_sha256": cfg["frozen_input_manifest_sha256"],
              "segment_rule_version": cfg["scoring"]["segment_rule_version"],
              "segment_count": len(rows), "segments_by_provider": dict(Counter(x["provider"] for x in rows)),
              "segments_by_kind": dict(Counter(x["kind"] for x in rows)),
              "association_status": dict(Counter(x["association_status"] for x in rows)),
              "html_source_articles": sum(x["body_method"] == "HTMLPARSER_TEXT_NODE_MAP" for x in maps),
              "max_token_count": max((x["token_count"] for x in rows), default=0),
              "score_cap": cfg["scoring"]["max_real_inference_units"],
              "score_cap_status": "WITHIN_CAP" if len(rows) <= cfg["scoring"]["max_real_inference_units"] else "EXCEEDS_CAP",
              "review_units": len(selected), "review_by_provider": dict(Counter(x["provider"] for x in selected)),
              "segments_sha256": sha(PRIVATE / "segments_v2_private.json"),
              "source_map_sha256": sha(PRIVATE / "source_map_v2_private.json"),
              "review_units_sha256": sha(PRIVATE / "review_units_v2_private.json"),
              "no_model_scores_read": True}
    write(PUBLIC / "segment_manifest_r2_redacted.json", public)
    return public


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
