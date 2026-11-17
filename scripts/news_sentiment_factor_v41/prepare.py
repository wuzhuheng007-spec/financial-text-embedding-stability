"""Deterministic Chinese segmentation, local entity evidence and blind review selection."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from scripts.quant_personal_feed.event_rules import ENTITY_RULES, RULE_VERSION, classify
from .freeze import CFG, MODEL, PRIVATE, PUBLIC, digest, sha, write


def semantic_kind(text: str) -> str:
    if re.search(r"否认|辟谣|澄清|不实|并未|没有", text):
        return "DENIAL_CANDIDATE"
    if re.search(r"预计|预测|可能|或将|预计将|展望|预期", text):
        return "PREDICTION_CANDIDATE"
    if re.search(r"去年|上周|此前|回顾|历史上|过去一年", text):
        return "RETROSPECTIVE_CANDIDATE"
    return "REPORT_CANDIDATE"


def punctuation_spans(text: str) -> list[tuple[int, int]]:
    boundaries = {0, len(text)}
    for hit in re.finditer(r"[。！？!?；;\n]+", text):
        boundaries.add(hit.end())
    for hit in re.finditer(r"(?<!\d)(?:\d{1,2}[.、]|[一二三四五六][、.])(?=\S)", text):
        if hit.start() == 0 or text[hit.start() - 1].isspace():
            boundaries.add(hit.start())
    result = []
    points = sorted(boundaries)
    for a, b in zip(points, points[1:]):
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            result.append((a, b))
    return result


def token_safe_spans(text: str, a: int, b: int, tokenizer, max_tokens: int) -> list[tuple[int, int, int]]:
    pieces = []
    cursor = a
    while cursor < b:
        remaining = text[cursor:b]
        count = len(tokenizer.encode(remaining, add_special_tokens=True, truncation=False))
        if count <= max_tokens:
            pieces.append((cursor, b, count))
            break
        lo, hi, best = cursor + 1, b, None
        while lo <= hi:
            mid = (lo + hi) // 2
            n = len(tokenizer.encode(text[cursor:mid], add_special_tokens=True, truncation=False))
            if n <= max_tokens:
                best = (mid, n)
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            raise ValueError("SINGLE_CHARACTER_EXCEEDS_TOKEN_LIMIT")
        pieces.append((cursor, best[0], best[1]))
        cursor = best[0]
    return pieces


def segment_article(article: dict, tokenizer, max_tokens: int) -> list[dict]:
    full = article["title"] + "\n" + article["body"]
    event = classify({"title": article["title"], "content": article["body"],
                      "published_at_utc": article.get("published_at_utc"),
                      "received_at": article["received_at_utc"]})
    pieces = []
    if article["title"].strip():
        a, b = 0, len(article["title"])
        pieces.extend(("TITLE", x, y, n) for x, y, n in token_safe_spans(full, a, b, tokenizer, max_tokens))
    body_start = len(article["title"]) + 1
    for a, b in punctuation_spans(article["body"]):
        pieces.extend(("BODY_SEGMENT", x, y, n) for x, y, n in
                      token_safe_spans(full, a + body_start, b + body_start, tokenizer, max_tokens))
    rows = []
    body_index, title_index = 0, 0
    for kind, start, end, token_count in pieces:
        text = full[start:end]
        labels = classify({"content": text})
        companies = [e for e in labels["entities"] if ENTITY_RULES[e["id"]][1].startswith("COMPANY")]
        if kind == "TITLE":
            title_index += 1
            segment_id = f"T{title_index:04d}"
        else:
            body_index += 1
            segment_id = f"B{body_index:04d}"
        rows.append({
            "provider": article["provider"], "article_id": article["article_id"],
            "version": article["version"], "segment_id": segment_id, "kind": kind,
            "start": start, "end": end, "text": text, "input_sha256": digest(text.encode("utf-8")),
            "token_count": token_count, "source_text_hash": article["source_text_hash"],
            "received_at_utc": article["received_at_utc"], "published_at_utc": article.get("published_at_utc"),
            "event_group_id": event["event_group_id"], "grouping_basis": event["grouping_basis"],
            "entity_rule_version": RULE_VERSION,
            "entity_candidates": [{"id": e["id"], "phrase": e["phrase"]} for e in companies],
            "company_association": companies[0]["id"] if len(companies) == 1 else None,
            "association_status": "ONE_EXPLICIT_COMPANY" if len(companies) == 1 else
                                  "MULTI_COMPANY_UNRESOLVED" if companies else "NO_EXPLICIT_COMPANY",
            "semantic_kind_candidate": semantic_kind(text),
        })
    for row in rows:
        assert full[row["start"]:row["end"]] == row["text"]
        assert row["token_count"] <= max_tokens
    return rows


def review_selection(segments: list[dict], cap: int) -> list[dict]:
    # Only source text and fixed regex strata are consulted. Model outputs are unavailable here.
    preferred = ["REPORT_CANDIDATE", "PREDICTION_CANDIDATE", "DENIAL_CANDIDATE",
                 "RETROSPECTIVE_CANDIDATE"]
    ordered = sorted(segments, key=lambda x: (x["received_at_utc"], x["article_id"], x["version"], x["segment_id"]))
    selected, used = [], set()
    per_source = max(1, cap // 2)
    for source in ("moomoo_webapi", "jin10"):
        source_rows = [x for x in ordered if x["provider"] == source]
        for cardinality in ("ONE_EXPLICIT_COMPANY", "MULTI_COMPANY_UNRESOLVED", "NO_EXPLICIT_COMPANY"):
            for nature in preferred:
                if len([x for x in selected if x["provider"] == source]) >= per_source:
                    break
                candidate = next((x for x in source_rows if x["association_status"] == cardinality
                                  and x["semantic_kind_candidate"] == nature
                                  and (x["article_id"], x["version"]) not in used), None)
                if candidate:
                    selected.append(candidate)
                    used.add((candidate["article_id"], candidate["version"]))
        for candidate in source_rows:
            if len([x for x in selected if x["provider"] == source]) >= per_source:
                break
            if (candidate["article_id"], candidate["version"]) not in used:
                selected.append(candidate)
                used.add((candidate["article_id"], candidate["version"]))
    return selected[:cap]


def main() -> dict:
    if (PRIVATE / "segments_private.json").exists():
        raise FileExistsError("V41_SEGMENTS_ALREADY_FROZEN")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    frozen = json.loads((PRIVATE / "input_manifest_private.json").read_text(encoding="utf-8"))
    if sha(CFG) != frozen["config_sha256"]:
        raise ValueError("CONFIG_DRIFT_AFTER_INPUT_FREEZE")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False)
    rows = []
    for article in frozen["rows"]:
        rows.extend(segment_article(article, tokenizer, cfg["scoring"]["max_tokens_including_special"]))
    if len(rows) > cfg["scoring"]["max_real_inference_units"]:
        # Preserve the full segmentation and explicitly disclose the scoring budget gap.
        budget_status = "SEGMENTS_EXCEED_INFERENCE_CAP"
    else:
        budget_status = "WITHIN_INFERENCE_CAP"
    selected = review_selection(rows, cfg["scoring"]["semantic_review_max"])
    write(PRIVATE / "segments_private.json", rows)
    write(PRIVATE / "review_units_private.json", selected)
    counts = Counter(x["provider"] for x in rows)
    public = {"schema": "news_sentiment_v41_segment_manifest_redacted_1",
              "source_manifest_sha256": sha(PRIVATE / "input_manifest_private.json"),
              "segment_rule_version": cfg["scoring"]["segment_rule_version"],
              "entity_rule_version": RULE_VERSION, "segment_count": len(rows),
              "segments_by_provider": dict(counts),
              "segments_by_kind": dict(Counter(x["kind"] for x in rows)),
              "association_status": dict(Counter(x["association_status"] for x in rows)),
              "long_split_pieces": sum(x["segment_id"].startswith("B") and x["token_count"] == cfg["scoring"]["max_tokens_including_special"] for x in rows),
              "scoring_budget_status": budget_status,
              "review_units": len(selected), "review_by_provider": dict(Counter(x["provider"] for x in selected)),
              "review_by_stratum": dict(Counter(x["association_status"] + "/" + x["semantic_kind_candidate"] for x in selected)),
              "segments_sha256": sha(PRIVATE / "segments_private.json"),
              "review_units_sha256": sha(PRIVATE / "review_units_private.json"),
              "no_model_scores_read": True}
    write(PUBLIC / "segment_manifest_redacted.json", public)
    return public


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
