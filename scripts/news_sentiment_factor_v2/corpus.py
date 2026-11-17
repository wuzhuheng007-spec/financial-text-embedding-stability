"""Freeze complete narrative blocks from saved training-period issuer exhibits.

The selector sees source documents and fixed structural rules only. It never
loads price labels, the v2.5 table candidate list, or sentiment scores.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/news_sentiment_factor_v2_20261117.json"
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v2_20261117"
PUBLIC = ROOT / "results/news_sentiment_factor_v2_20261117"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dom_path(node) -> str:
    parts = []
    while getattr(node, "name", None) and node.name != "[document]":
        index = 1
        sibling = node.previous_sibling
        while sibling is not None:
            if getattr(sibling, "name", None) == node.name:
                index += 1
            sibling = sibling.previous_sibling
        parts.append(f"{node.name}[{index}]")
        node = node.parent
    return "/" + "/".join(reversed(parts))


def extract(doc: dict, rules: dict) -> tuple[list[dict], Counter]:
    from bs4 import BeautifulSoup
    path = Path(doc["raw_body_path"])
    if not path.is_file() or file_sha(path) != doc["raw_body_sha256"]:
        raise ValueError("RAW_SOURCE_HASH_MISMATCH:" + doc["document_id"])
    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    blocks = []
    skipped: Counter = Counter()
    seen: set[str] = set()
    for div in soup.find_all("div"):
        if div.find_parent("table") or div.find("table") or div.find("div"):
            continue
        value = " ".join(div.get_text(" ", strip=True).split())
        if not value:
            continue
        reason = None
        if len(value) < rules["min_characters"] or len(re.findall(r"[A-Za-z]", value)) < rules["min_ascii_letters"]:
            reason = "TOO_SHORT_OR_NOT_ENGLISH_NARRATIVE"
        elif len(value) > rules["max_characters"]:
            reason = "LONG_BLOCK_NOT_TRUNCATED"
        elif any(value.casefold().startswith(x) for x in rules["exclude_prefixes_casefold"]):
            reason = "FIXED_BOILERPLATE_PREFIX"
        elif len(re.findall(r"(?<![A-Za-z])[$€£]?\(?\d[\d,.]*\)?%?", value)) > rules["max_numeric_tokens"]:
            reason = "TABLE_LIKE_NUMERIC_DENSITY"
        else:
            fingerprint = sha(value.casefold().encode("utf-8"))
            if fingerprint in seen:
                reason = "DUPLICATE_TEXT_IN_EVENT"
            elif len(blocks) >= rules["max_blocks_per_event"]:
                reason = "FIXED_BLOCK_BUDGET"
            else:
                seen.add(fingerprint)
                locator = dom_path(div)
                block_id = sha((doc["document_id"] + "|" + locator + "|" + value).encode("utf-8"))[:20]
                blocks.append({
                    "block_id": block_id,
                    "document_id": doc["document_id"],
                    "event_group_id": doc["event_group_id"],
                    "symbol": doc["symbol"],
                    "announcement_date": doc["announcement_date"],
                    "source_url": doc["source_url"],
                    "source_kind": doc["source_kind"],
                    "document_type": doc["form"],
                    "raw_body_sha256": doc["raw_body_sha256"],
                    "body_sha256": doc["body_sha256"],
                    "official_published_at": doc.get("official_published_at"),
                    "sec_acceptance_at": doc.get("sec_acceptance_at"),
                    "historical_first_available_at": doc.get("historical_first_available_at"),
                    "retrieved_at_utc": doc.get("retrieved_at_utc"),
                    "time_evidence": "DATE_PROXY_ONLY" if not doc.get("historical_first_available_at") else "CHECK_FIRST_AVAILABLE_SEPARATELY",
                    "dom_path": locator,
                    "text": value,
                    "text_sha256": sha(value.encode("utf-8")),
                    "characters": len(value),
                })
        if reason:
            skipped[reason] += 1
    return blocks, skipped


def freeze() -> dict:
    if (PRIVATE / "input_manifest_private.json").exists():
        raise FileExistsError("FROZEN_INPUT_ALREADY_EXISTS")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if file_sha(MANIFEST) != config["source_selection"]["manifest_sha256"]:
        raise ValueError("CORPUS_MANIFEST_HASH_CHANGED")
    corpus = json.loads(MANIFEST.read_text(encoding="utf-8"))
    selector = config["source_selection"]
    docs = [d for d in corpus["documents"]
            if d.get("source_kind") == selector["source_kind"]
            and d.get("form") == selector["form"]
            and d.get("symbol") in config["issuer_symbols"]
            and config["study_window"][0] <= str(d.get("announcement_date")) <= config["this_run_end"]
            and d.get("version_status") == selector["version_status"]]
    docs.sort(key=lambda d: (d["announcement_date"], d["symbol"], d["document_id"]))
    if len(docs) != selector["max_events"] or len({d["event_group_id"] for d in docs}) != len(docs):
        raise ValueError("EVENT_SELECTION_COUNT_OR_DEDUP_CHANGED")
    selected = []
    ledger = []
    for doc in docs:
        blocks, skipped = extract(doc, selector)
        selected.extend(blocks)
        ledger.append({"event_group_id": doc["event_group_id"], "symbol": doc["symbol"],
                       "announcement_date": doc["announcement_date"], "document_id": doc["document_id"],
                       "raw_body_sha256": doc["raw_body_sha256"], "selected_blocks": len(blocks),
                       "skipped": dict(skipped), "version_status": doc["version_status"]})
    if not selected or len(selected) > config["finbert"]["max_actual_model_blocks"]:
        raise ValueError("SCORE_BLOCK_BUDGET_OR_EMPTY")
    timestamp = datetime.now(timezone.utc).isoformat()
    private = {"schema": "sentiment_v2_source_blocks_private_1", "frozen_at_utc": timestamp,
               "config_sha256": file_sha(CONFIG), "corpus_manifest_sha256": file_sha(MANIFEST),
               "selection_ledger": ledger, "blocks": selected}
    write_json(PRIVATE / "input_manifest_private.json", private)
    code = Path(__file__)
    receipt = {"schema": "sentiment_v2_freeze_redacted_1", "frozen_at_utc": timestamp,
               "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "config_sha256": file_sha(CONFIG), "corpus_manifest_sha256": file_sha(MANIFEST),
               "input_manifest_sha256": file_sha(PRIVATE / "input_manifest_private.json"),
               "selector_code_sha256": file_sha(code), "events": len(docs), "blocks": len(selected),
               "selection_ledger": ledger, "later_labels_read": False}
    write_json(PUBLIC / "freeze_redacted.json", receipt)
    return {"events": len(docs), "blocks": len(selected), "later_labels_read": False}


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    print(json.dumps(freeze()))
