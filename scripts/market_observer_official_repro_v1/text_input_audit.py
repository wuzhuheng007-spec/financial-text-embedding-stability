"""Read-only reconstruction of frozen SEC text features for a private P2 audit.

This script writes diagnostic artifacts only. It does not download sources,
read June/July labels, train an estimator, or alter text processing.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re

from scripts.market_observer_official_v1.text_comparison import (
    canonical_hash,
    clean_text,
    fit_tfidf,
    load_sec_events,
    tokenize,
    transform_tfidf,
)


ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "artifacts/market_observer_official_text_audit_20261115"
PUBLIC = ROOT / "results/market_observer_official_repro_20261115"
SOURCE = ROOT / "artifacts/market_observer_official_20261115"
CONFIG = ROOT / "configs/market_observer_official_text_v1.json"
MANIFEST = SOURCE / "corpus_manifest.json"
MODEL_CODE = ROOT / "scripts/market_observer_official_v1/text_comparison.py"
TRAIN_END = date(2026, 3, 31)
VALIDATION_END = date(2026, 5, 31)
SYMBOLS = ("US.NVDA", "US.MU")


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


class VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


PATTERNS = {
    "revenue": re.compile(r"\b(?:revenue|net sales)\b"),
    "net_income": re.compile(r"\b(?:net income|net earnings|net loss)\b"),
    "guidance": re.compile(r"\b(?:outlook|guidance|expects|expected|forecast)\b"),
    "capital_expenditures": re.compile(r"\b(?:capital expenditures?|capital spending|capital investments?|capital expense)\b"),
}


def first_match(pattern: re.Pattern, value: str) -> dict | None:
    match = pattern.search(value)
    if match is None:
        return None
    start = max(0, match.start() - 75)
    end = min(len(value), match.end() + 115)
    return {"start_zero_based": match.start(), "excerpt": value[start:end]}


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["split"]["train_end"] != TRAIN_END.isoformat() or config["split"]["validation_end"] != VALIDATION_END.isoformat():
        raise RuntimeError("audit split disagrees with frozen config")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    prior_summary = json.loads((ROOT / "results/market_observer_official_20261115/text_comparison_redacted.json").read_text(encoding="utf-8"))
    docs = manifest["documents"]
    events, problems = load_sec_events(manifest, config=config, latest_decision=VALIDATION_END)
    if problems:
        raise RuntimeError(f"manifest problems: {problems}")
    by_event = {(e["symbol"], e["event_id"]): e for e in events}
    rows: list[dict] = []
    qualified_paths = {}
    for symbol in SYMBOLS:
        name = symbol.replace(".", "_")
        path = SOURCE / "text_comparison_v1" / f"{name}_qualified_rows.private.jsonl"
        qualified_paths[symbol] = path
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    if any(date.fromisoformat(row["label_day"]) > VALIDATION_END for row in rows):
        raise RuntimeError("frozen qualified rows unexpectedly include June or July")

    participating = {(row["symbol"], event_id) for row in rows for event_id in row["event_ids"]}
    if participating - set(by_event):
        raise RuntimeError("frozen row refers to an event absent from source corpus")
    by_doc_event: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for doc in docs:
        symbol = "US." + str(doc.get("symbol", ""))
        by_doc_event[(symbol, doc.get("event_id", ""))].append(doc)

    document_rows: list[dict] = []
    retained_by_doc: dict[str, str] = {}
    original_by_doc: dict[str, str] = {}
    event_rows: list[dict] = []
    for key in sorted(participating, key=lambda k: (by_event[k]["announcement_date"], k[0], k[1])):
        event = by_event[key]
        ordered = sorted(by_doc_event[key], key=lambda d: (
            0 if d.get("source_kind") == "ISSUER_EARNINGS" else 1,
            str(d.get("document_id"))))
        per_doc_budget = max(1, config["event_text_max_characters"] // len(ordered))
        retained_chunks: list[str] = []
        for order, doc in enumerate(ordered, 1):
            body_path = Path(doc["body_path"])
            if sha_file(body_path) != doc["body_sha256"]:
                raise RuntimeError("source body hash mismatch: " + doc["document_id"])
            raw_extracted = body_path.read_text(encoding="utf-8")
            original = clean_text(raw_extracted)
            kept = original[:per_doc_budget]
            original_by_doc[doc["document_id"]] = original
            retained_by_doc[doc["document_id"]] = kept
            retained_chunks.append(kept)
            document_rows.append({
                "review_role": "CODEX_REVIEW_MECHANICAL_RECONSTRUCTION",
                "symbol": key[0], "event_id": key[1], "document_id": doc["document_id"],
                "announcement_date": event["announcement_date"].isoformat(),
                "order_in_event": order, "documents_in_event": len(ordered),
                "source_kind": doc["source_kind"], "form": doc.get("form"),
                "content_type": doc.get("content_type"), "source_url": doc["source_url"],
                "body_sha256": doc["body_sha256"],
                "raw_extracted_characters": len(raw_extracted),
                "original_cleaned_characters": len(original),
                "retained_event_characters": len(kept),
                "per_document_budget": per_doc_budget,
                "last_retained_char_index_zero_based": len(kept) - 1 if kept else None,
                "clip_start_zero_based": per_doc_budget if len(original) > per_doc_budget else None,
                "clipped_characters": max(0, len(original) - len(kept)),
            })
        reconstructed = " ".join(retained_chunks)[:config["event_text_max_characters"]]
        if hashlib.sha256(reconstructed.encode("utf-8")).hexdigest() != event["text_sha256"]:
            raise RuntimeError("event text differs from frozen source: " + key[1])
        event_rows.append({
            "symbol": key[0], "event_id": key[1],
            "announcement_date": event["announcement_date"].isoformat(),
            "ordered_document_ids": [d["document_id"] for d in ordered],
            "document_count": len(ordered),
            "original_cleaned_characters": sum(len(original_by_doc[d["document_id"]]) for d in ordered),
            "retained_event_characters": len(reconstructed),
            "event_text_sha256": event["text_sha256"],
        })

    aggregate_rows: list[dict] = []
    stats_by_symbol: dict[str, dict] = {}
    vocab_private: dict[str, list[dict]] = {}
    for symbol in SYMBOLS:
        subset = sorted((r for r in rows if r["symbol"] == symbol), key=lambda r: r["label_day"])
        for row in subset:
            used = [by_event[(symbol, eid)] for eid in row["event_ids"]]
            per_event_budget = max(1, config["aggregate_text_max_characters"] // max(1, len(used)))
            reconstructed = " ".join(e["text"][:per_event_budget] for e in used)[:config["aggregate_text_max_characters"]]
            if reconstructed != row["announcement_text"]:
                raise RuntimeError("aggregated text mismatch: " + symbol + " " + row["label_day"])
            if hashlib.sha256(reconstructed.encode("utf-8")).hexdigest() != row["input_text_sha256"]:
                raise RuntimeError("frozen row text hash mismatch: " + symbol + " " + row["label_day"])
            aggregate_rows.append({
                "symbol": symbol, "label_day": row["label_day"],
                "split": "train" if date.fromisoformat(row["label_day"]) <= TRAIN_END else "validation",
                "decision_session": row["decision_session"],
                "last_feature_day": row["last_feature_day"],
                "event_ids": row["event_ids"], "event_count": len(used),
                "per_event_budget": per_event_budget,
                "original_aggregate_characters": sum(len(e["text"]) for e in used),
                "retained_aggregate_characters": len(reconstructed),
                "event_group_aggregate_clips": [e["event_id"] for e in used if len(e["text"]) > per_event_budget],
                "aggregate_text_sha256": row["input_text_sha256"],
                "sec_coverage_complete": row["sec_source_coverage_complete"],
            })
        train = [r for r in subset if date.fromisoformat(r["label_day"]) <= TRAIN_END]
        val = [r for r in subset if date.fromisoformat(r["label_day"]) > TRAIN_END]
        tf = config["tfidf"]
        vocab, idf = fit_tfidf([r["announcement_text"] for r in train],
                               max_features=tf["max_features"], min_df=tf["min_document_frequency"])
        previous_comparison = prior_summary["symbols"][symbol]["comparison"]
        if canonical_hash(vocab) != previous_comparison["tfidf_vocabulary_sha256"]:
            raise RuntimeError("train vocabulary differs from prior reported run: " + symbol)
        if hashlib.sha256(idf.tobytes()).hexdigest() != previous_comparison["tfidf_idf_sha256"]:
            raise RuntimeError("train IDF differs from prior reported run: " + symbol)
        total = Counter(term for r in train for term in tokenize(r["announcement_text"]))
        df = Counter(term for r in train for term in set(tokenize(r["announcement_text"])))
        vocab_private[symbol] = [{"rank_by_train_total_frequency": i + 1, "term": term,
                                  "train_total_frequency": total[term], "train_document_frequency": df[term],
                                  "idf": float(idf[i])} for i, term in enumerate(vocab)]
        transformed = transform_tfidf([r["announcement_text"] for r in subset], vocab, idf)
        unique_text = Counter(r["input_text_sha256"] for r in subset)
        split_stats: dict[str, dict] = {}
        for split_name, batch in (("train", train), ("validation", val)):
            indices = [i for i, r in enumerate(subset) if r in batch]
            token_lists = [tokenize(r["announcement_text"]) for r in batch]
            token_occurrences = sum(len(toks) for toks in token_lists)
            oov_occurrences = sum(sum(term not in vocab for term in toks) for toks in token_lists)
            split_stats[split_name] = {
                "rows": len(batch),
                "rows_with_event": sum(bool(r["event_ids"]) for r in batch),
                "rows_without_event_verified_sec": sum(not r["event_ids"] for r in batch),
                "zero_tfidf_vector_rows": sum(not transformed[i].any() for i in indices),
                "nonempty_text_zero_tfidf_vector_rows": sum(bool(subset[i]["announcement_text"]) and not transformed[i].any() for i in indices),
                "token_occurrences": token_occurrences,
                "oov_token_occurrences": oov_occurrences,
                "oov_token_occurrence_fraction": oov_occurrences / token_occurrences if token_occurrences else None,
                "unique_aggregate_text_hashes": len({r["input_text_sha256"] for r in batch}),
                "rows_sharing_aggregate_text_hash_with_another_day_same_symbol": sum(unique_text[r["input_text_sha256"]] > 1 for r in batch),
                "aggregate_clipped_rows": sum(r["truncated_event_group_count"] > 0 for r in batch),
                "coverage_flag_values": sorted({bool(r["sec_source_coverage_complete"]) for r in batch}),
            }
        stats_by_symbol[symbol] = {"vocabulary_size": len(vocab), "splits": split_stats,
                                   "vocabulary_idf_hashes_match_prior_run": True,
                                   "all_rows": len(subset), "unique_aggregate_text_hashes": len(unique_text),
                                   "all_rows_sharing_text_hash": sum(unique_text[r["input_text_sha256"]] > 1 for r in subset),
                                   "max_days_using_one_event_group": max(Counter(e for r in subset for e in r["event_ids"]).values(), default=0)}

    exhibit_rows: list[dict] = []
    for doc in sorted((d for d in docs if d["source_kind"] == "ISSUER_EARNINGS"),
                      key=lambda d: (d["announcement_date"], d["symbol"], d["document_id"])):
        did = doc["document_id"]
        active = ("US." + doc["symbol"], doc["event_id"]) in participating
        original = original_by_doc.get(did)
        if original is None:
            body_path = Path(doc["body_path"])
            if sha_file(body_path) != doc["body_sha256"]:
                raise RuntimeError("exhibit body hash mismatch: " + did)
            original = clean_text(body_path.read_text(encoding="utf-8"))
        retained = retained_by_doc.get(did, "")
        raw_path = Path(doc["raw_body_path"])
        if sha_file(raw_path) != doc["raw_body_sha256"]:
            raise RuntimeError("raw exhibit hash mismatch: " + did)
        parser = VisibleHTML()
        parser.feed(raw_path.read_text(encoding="utf-8", errors="replace"))
        raw_visible = clean_text(" ".join(parser.parts))
        topic_status: dict[str, dict] = {}
        active_rows = [r for r in aggregate_rows if r["symbol"] == "US." + doc["symbol"]
                       and doc["event_id"] in r["event_ids"]]
        event_text = by_event.get(("US." + doc["symbol"], doc["event_id"]), {}).get("text", "")
        for topic, pattern in PATTERNS.items():
            in_extracted = first_match(pattern, original)
            in_retained = first_match(pattern, retained)
            in_raw = first_match(pattern, raw_visible)
            if not active:
                status = "OUTSIDE_TRAIN_VALIDATION_NOT_MODEL_INPUT"
            elif in_retained:
                status = "RETAINED_LEXICAL_EVIDENCE"
            elif in_extracted:
                status = "TRUNCATED_LOST_LEXICAL_EVIDENCE"
            elif in_raw:
                status = "EXTRACTION_FAILED_LEXICAL_EVIDENCE"
            else:
                status = "ORIGINAL_NOT_DISCLOSED_LEXICAL_CHECK"
            event_match = pattern.search(event_text) if active else None
            model_rows_with_match_retained = sum(event_match is not None and
                                                 event_match.start() < r["per_event_budget"]
                                                 for r in active_rows)
            topic_status[topic] = {"status": status,
                                    "raw_visible_first": in_raw,
                                    "extracted_first": in_extracted,
                                    "retained_first": in_retained,
                                    "model_rows_with_event": len(active_rows),
                                    "model_rows_with_lexical_match_retained": model_rows_with_match_retained}
        exhibit_rows.append({
            "review_role": "CODEX_REVIEW_LEXICAL_SPOT_CHECK_NOT_INDEPENDENT_GOLD",
            "announcement_date": doc["announcement_date"], "symbol": doc["symbol"],
            "document_id": did, "event_id": doc["event_id"],
            "source_url": doc["source_url"], "body_sha256": doc["body_sha256"],
            "raw_body_sha256": doc["raw_body_sha256"],
            "train_validation_participating": active,
            "original_cleaned_characters": len(original),
            "retained_event_characters": len(retained) if active else None,
            "topics": topic_status,
        })

    write_jsonl(PRIVATE / "participating_documents.private.jsonl", document_rows)
    write_jsonl(PRIVATE / "participating_events.private.jsonl", event_rows)
    write_jsonl(PRIVATE / "aggregate_rows.private.jsonl", aggregate_rows)
    write_json(PRIVATE / "training_vocabulary_idf.private.json", vocab_private)
    write_json(PRIVATE / "earnings_exhibit_review.private.json", exhibit_rows)
    inputs = {"model_code_sha256": sha_file(MODEL_CODE), "config_sha256": sha_file(CONFIG),
              "corpus_manifest_sha256": sha_file(MANIFEST),
              "qualified_rows_sha256_by_symbol": {s: sha_file(p) for s, p in qualified_paths.items()}}
    reproduction_path = ROOT / "artifacts/market_observer_official_repro_20261115"
    inputs["qualified_rows_match_current_reproduction_by_symbol"] = {
        s: (reproduction_path / p.name).is_file() and sha_file(p) == sha_file(reproduction_path / p.name)
        for s, p in qualified_paths.items()}
    write_json(PRIVATE / "input_versions.private.json", inputs)
    topic_counts = {topic: dict(Counter(x["topics"][topic]["status"] for x in exhibit_rows)) for topic in PATTERNS}
    topic_model_row_retention = {
        topic: {"participating_exhibits_with_phrase_in_all_model_rows": sum(
                    x["train_validation_participating"]
                    and x["topics"][topic]["model_rows_with_event"] > 0
                    and x["topics"][topic]["model_rows_with_event"] == x["topics"][topic]["model_rows_with_lexical_match_retained"]
                    for x in exhibit_rows),
                "participating_exhibits_with_phrase_search_absence": sum(
                    x["train_validation_participating"]
                    and x["topics"][topic]["status"] == "ORIGINAL_NOT_DISCLOSED_LEXICAL_CHECK"
                    for x in exhibit_rows)}
        for topic in PATTERNS}
    public = {
        "schema": "market_observer_official_text_input_audit_v1",
        "role": "CODEX_REVIEW_PLUS_MECHANICAL_RECONSTRUCTION",
        "scope": "frozen train and reused validation SEC accession event texts for NVDA and MU; June/July labels not read or scored",
        "input_versions": inputs,
        "participating_event_groups": len(participating),
        "participating_documents": len(document_rows),
        "participating_documents_clipped": sum(r["clipped_characters"] > 0 for r in document_rows),
        "participating_event_groups_with_clipped_documents": sum(any(d["event_id"] == e["event_id"] and d["clipped_characters"] > 0 for d in document_rows) for e in event_rows),
        "qualified_train_validation_rows": len(aggregate_rows),
        "aggregated_text_mismatches": 0,
        "symbols": stats_by_symbol,
        "earnings_exhibits_inspected_fixed_date_order": len(exhibit_rows),
        "earnings_exhibits_participating_train_validation": sum(x["train_validation_participating"] for x in exhibit_rows),
        "participating_earnings_exhibits_clipped_at_document_budget": sum(
            d["source_kind"] == "ISSUER_EARNINGS" and d["clipped_characters"] > 0 for d in document_rows),
        "earnings_topic_lexical_status_counts": topic_counts,
        "earnings_topic_model_row_retention": topic_model_row_retention,
        "text_content_code_review": {
            "frequency_ranked_terms": "The top ranks in both 128-term vocabularies contain function words and SEC filing/format words; company names are also prominent. Examples include of, the, and, securities, exchange, act, nvidia and micron.",
            "numeric_token_limit": "The frozen tokenizer requires an ASCII letter as the first character, so standalone financial number tokens such as 44.1 are not represented directly in TF-IDF.",
            "selected_C_estimator_term_coefficients": "NOT_SAVED_IN_OLD_RUN_OR_REPRODUCTION; actual predictive text weights cannot be audited here without an additional fit.",
            "classification_limit": "This is a CODEX_REVIEW of the frequency-ranked terms, not an independently validated measure of causal or predictive importance.",
        },
        "review_limitations": [
            "Keyword presence locates candidate financial passages; it does not establish that a precise numeric fact was disclosed or extracted correctly.",
            "ORIGINAL_NOT_DISCLOSED_LEXICAL_CHECK means searched phrases were absent in saved raw visible HTML and extracted text; it is phrase-search absence, not definitive nondisclosure.",
            "Train-only vocabulary/IDF was reconstructed from frozen qualified rows; no estimator was fitted in this audit.",
            "A constant SEC coverage flag cannot explain within-sample label differences.",
        ],
    }
    write_json(PUBLIC / "text_input_audit_redacted.json", public)
    print(json.dumps({"private_dir": str(PRIVATE), "public_file": str(PUBLIC / "text_input_audit_redacted.json"),
                      "participating_events": len(participating), "participating_documents": len(document_rows),
                      "exhibits": len(exhibit_rows), "rows": len(aggregate_rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
