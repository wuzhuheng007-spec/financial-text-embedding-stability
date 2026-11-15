"""Build one training-only v2.2 input per saved earnings event, without fitting."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.market_observer_financial_text_v21.build import select_training_documents
from scripts.market_observer_financial_text_v21.parser import visible_text

from .adapter import assemble


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/market_observer_financial_text_v22_20261116.json"
V21_CONFIG = ROOT / "configs/market_observer_financial_text_v21.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
V21_BLOCKS = ROOT / "artifacts/market_observer_financial_text_v21_20261115/blocks.private.jsonl"
PRIVATE = ROOT / "artifacts/market_observer_financial_text_v22_20261116"
PUBLIC = ROOT / "results/market_observer_financial_text_v22_20261116"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite v2.2 result: {path}")
    path.write_bytes(data)


def _selected_documents(config: dict, manifest: dict) -> list[dict]:
    v21_config = json.loads(V21_CONFIG.read_text(encoding="utf-8"))
    source = config["source"]
    scope = v21_config["source_scope"]
    if (scope["symbols"] != source["symbols"]
            or scope["announcement_date_start"] != source["announcement_date_start"]
            or scope["announcement_date_end"] != source["announcement_date_end"]
            or source["new_downloads"] != 0):
        raise RuntimeError("v2.2 scope differs from frozen v2.1 training corpus")
    docs = select_training_documents(manifest, v21_config)
    if len(docs) != source["expected_earnings_exhibits"] + source["expected_matched_primary_filings"]:
        raise RuntimeError("unexpected saved document count")
    return docs


def _validate_selected_source(event: dict, by_block: dict[str, dict], by_doc: dict[str, dict],
                              soup_cache: dict[str, BeautifulSoup]) -> int:
    """Check actual model-visible substrings against the saved original DOM."""
    text = event["selected_text"]
    context_spans = {s["context_id"]: s for s in event["model_visible_spans"] if s["context_id"]}
    group_spans = {s["group_id"]: s for s in event["model_visible_spans"] if s["group_id"]}
    for group in event["group_inventory"]:
        if group["status"] != "SELECTED":
            continue
        anchor = group_spans.get(group["group_id"])
        if anchor is None or anchor["source_block_id"] != group["anchor_block_id"]:
            raise RuntimeError("selected group lacks a matching model-visible anchor")
        if f"refs={','.join(group['context_ids']) or 'none'} " not in text[anchor["start"]:anchor["end"]]:
            raise RuntimeError("group references differ from serialized model input")
        for cid in group["context_ids"]:
            context = context_spans.get(cid)
            if context is None or context["document_id"] != group["document_id"] or context["start"] >= anchor["start"]:
                raise RuntimeError("referenced context is absent, cross-document or after anchor")
    checked = 0
    for span in event["model_visible_spans"]:
        block = by_block.get(span["source_block_id"])
        doc = by_doc.get(span["document_id"])
        if block is None or doc is None or block["document_id"] != doc["document_id"]:
            raise RuntimeError("selected span has no same-document source block")
        if (span["source_sha256"] != block["source_sha256"]
                or span["source_sha256"] != doc["raw_body_sha256"]
                or span["dom_path"] != block["dom_path"]
                or span["source_block_id"] != block["block_id"]):
            raise RuntimeError("selected source provenance differs from saved block/document")
        if (text[span["raw_start"]:span["raw_end"]] != block["raw_text"]
                or not (span["start"] <= span["raw_start"] < span["raw_end"] <= span["end"])):
            raise RuntimeError("selected source fragment is not intact in model input")
        soup = soup_cache.get(doc["document_id"])
        if soup is None:
            raw = Path(doc["raw_body_path"]).read_bytes()
            if sha(raw) != doc["raw_body_sha256"]:
                raise RuntimeError("saved source body changed")
            soup = BeautifulSoup(raw, "html.parser")
            soup_cache[doc["document_id"]] = soup
        node = soup.select_one(span["dom_path"])
        if node is None or block["raw_text"] not in visible_text(node):
            raise RuntimeError("selected source text absent at original DOM locator")
        checked += 1
    return checked


def run() -> dict:
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    if config["schema"] != "market_observer_financial_text_v22_plan_v1":
        raise RuntimeError("unexpected v2.2 config schema")
    budget = config["input_adapter"]["max_characters_per_event"]
    if budget != 20_000:
        raise RuntimeError("v2.2 fixed budget changed")
    manifest_bytes = MANIFEST.read_bytes()
    docs = _selected_documents(config, json.loads(manifest_bytes))
    by_doc = {d["document_id"]: d for d in docs}
    if len(by_doc) != len(docs):
        raise RuntimeError("duplicate source document")
    block_bytes = V21_BLOCKS.read_bytes()
    blocks = [json.loads(line) for line in block_bytes.decode("utf-8").splitlines() if line.strip()]
    if not blocks or len({b["block_id"] for b in blocks}) != len(blocks):
        raise RuntimeError("missing or duplicate saved v2.1 source blocks")
    by_block = {}
    by_event: dict[str, list[dict]] = defaultdict(list)
    for block in blocks:
        doc = by_doc.get(block["document_id"])
        if (doc is None or block["event_group_id"] != doc["event_group_id"]
                or block["symbol"] != doc["symbol"]
                or block["announcement_date"] != doc["announcement_date"]
                or block["source_sha256"] != doc["raw_body_sha256"]
                or sha(block["raw_text"].encode("utf-8")) != block["raw_text_sha256"]):
            raise RuntimeError("saved v2.1 block provenance mismatch")
        by_block[block["block_id"]] = block
        by_event[block["event_group_id"]].append(block)
    if len(by_event) != config["source"]["expected_events"]:
        raise RuntimeError("training event count changed")
    event_order = sorted(by_event, key=lambda eid: (
        by_event[eid][0]["announcement_date"], by_event[eid][0]["symbol"], eid))
    events = []
    soup_cache: dict[str, BeautifulSoup] = {}
    selected_source_fragments = 0
    for event_id in event_order:
        event = assemble(by_event[event_id], budget)
        selected_source_fragments += _validate_selected_source(event, by_block, by_doc, soup_cache)
        if event["selected_text_characters"] != len(event["selected_text"]):
            raise RuntimeError("actual serialized character count mismatch")
        events.append(event)
    event_bytes = b"".join(canonical(event) for event in events)
    omissions = Counter(o["reason"] for event in events for o in event["omitted_groups"])
    summary = {
        "schema": "financial_text_v22_build_redacted_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_scope": "SAVED_NVDA_MU_2025-04-01_TO_2026-03-31_EARNINGS_ONLY",
        "config_sha256": sha(config_bytes), "v21_config_sha256": sha(V21_CONFIG.read_bytes()),
        "source_manifest_sha256": sha(manifest_bytes), "v21_blocks_sha256": sha(block_bytes),
        "adapter_code_sha256": sha(Path(__file__).with_name("adapter.py").read_bytes()),
        "builder_code_sha256": sha(Path(__file__).read_bytes()),
        "source_document_count": len(docs), "event_count": len(events), "saved_source_block_count": len(blocks),
        "selected_group_count": sum(len(e["selected_group_ids"]) for e in events),
        "omitted_group_count": sum(len(e["omitted_groups"]) for e in events),
        "omission_reasons": dict(omissions),
        "selected_source_fragments_dom_verified": selected_source_fragments,
        "gross_duplicate_context_characters_avoided": sum(e["gross_duplicate_context_characters_avoided"] for e in events),
        "event_input_receipts": [{"event_group_id": e["event_group_id"], "symbol": e["symbol"],
                                  "selected_text_characters": e["selected_text_characters"],
                                  "selected_text_sha256": e["selected_text_sha256"],
                                  "selected_groups": len(e["selected_group_ids"]),
                                  "omitted_groups": len(e["omitted_groups"]),
                                  "shared_contexts": len(e["selected_context_ids"])} for e in events],
        "private_event_inputs_sha256": sha(event_bytes),
        "model_fit_calls": 0, "model_inference_calls": 0,
        "source_contents_published": False,
        "source_locator_kind": "DOM_PATH_NOT_BYTE_OFFSET",
        "source_review_note": "Parser relations and selected text are engineering candidates, not confirmed financial facts.",
    }
    # No mutation until all input, relation, budget, and source-DOM gates pass.
    write_new(PRIVATE / "event_inputs.private.jsonl", event_bytes)
    write_new(PUBLIC / "build_redacted.json", canonical(summary))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    summary = run()
    if args.print_summary:
        print(json.dumps({k: v for k, v in summary.items() if k != "event_input_receipts"},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
