"""Date-proxy SEC text comparison on the already reused development period.

This runner parses a saved full-window response but extracts OHLCV values only
through May 2026. It never labels, fits or scores June/July price outcomes.
Raw source text and row-level predictions remain private. A/B/C comparisons
use identical qualified rows.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import numpy as np

from scripts.market_observer_historical_v1.direction_baseline import (
    L2_GRID, TRAIN_END, VALIDATION_END, Ohlcv, classification_metrics,
    fit_and_select, make_direction_examples, predict_up,
)
from scripts.market_observer_historical_v1.price_baseline import sha256_file, source_config


SCHEMA = "market_observer_official_text_comparison_v1"
SYMBOLS = ("US.NVDA", "US.MU")
TOKEN = re.compile(r"[a-z][a-z0-9]+")
SEC_ARCHIVE_HOSTS = {"www.sec.gov", "sec.gov"}
NEW_YORK = ZoneInfo("America/New_York")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema") != "market_observer_official_text_dev_config_v1":
        raise ValueError("unknown frozen comparison configuration")
    if config.get("symbols") != list(SYMBOLS) or config["model"]["l2_grid"] != list(L2_GRID):
        raise ValueError("configuration changed frozen symbols or L2 grid")
    if config["split"]["validation_end"] != VALIDATION_END.isoformat():
        raise ValueError("configuration changed validation boundary")
    return config


def load_calendar(path: Path) -> tuple[list[date], set[date]]:
    """Read the saved US calendar; WHOLE/23400 alone counts as a full session."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("ret_code") != 0:
        raise ValueError("saved US trading calendar failed")
    rows = data["data"]["trading_days"]
    sessions: list[date] = []
    full: set[date] = set()
    for row in rows:
        d = date.fromisoformat(row["time"])
        if sessions and d <= sessions[-1]:
            raise ValueError("US trading calendar is duplicated or out of order")
        sessions.append(d)
        if row.get("trade_date_type") == "WHOLE" and int(row.get("trade_second", 0)) >= 23400:
            full.add(d)
    if not sessions or sessions[0] != date(2025, 4, 1) or sessions[-1] != date(2026, 7, 31):
        raise ValueError("saved US calendar window differs from frozen source")
    return sessions, full


def load_prevalidation_bars(price_dir: Path, symbol: str) -> tuple[list[Ohlcv], dict[str, Any]]:
    """Extract only pre-June prices from the unchanged full-window source file."""
    _, config_hash = source_config(price_dir)
    path = price_dir / f"{symbol.replace('.', '_')}_page1.raw.json"
    response = json.loads(path.read_text(encoding="utf-8"))
    if response.get("ret_code") != 0 or response.get("pagination", {}).get("has_more") is not False:
        raise ValueError("saved price response failed or has incomplete pagination")
    source_rows = response.get("data", {}).get("kline_list")
    if not isinstance(source_rows, list):
        raise ValueError("missing daily prices")
    bars: list[Ohlcv] = []
    for row in source_rows:
        raw_day = str(row["date"])
        day = date.fromisoformat(f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:8]}")
        if day > VALIDATION_END:
            continue  # No test-window OHLCV field is interpreted.
        opening = float(row["open"])
        closing = float(row["close"])
        volume = float(row["volume"])
        if not all(math.isfinite(v) and v > 0 for v in (opening, closing, volume)):
            raise ValueError("invalid prevalidation OHLCV")
        if bars and day <= bars[-1].day:
            raise ValueError("prevalidation prices are duplicated or out of order")
        bars.append(Ohlcv(day, opening, closing, volume))
    if not bars or bars[0].day != date(2025, 4, 1) or bars[-1].day != date(2026, 5, 29):
        raise ValueError("saved price response lacks frozen prevalidation endpoints")
    return bars, {"source_sha256": sha256_file(path), "source_request_sha256": config_hash,
                  "prevalidation_bar_count": len(bars)}


def first_eligible_decision_session(announcement: date, sessions: list[date],
                                    full_sessions: set[date]) -> date | None:
    """Date D -> first complete regular session F > D -> next session F+1."""
    for i, session in enumerate(sessions):
        if session > announcement and session in full_sessions:
            return sessions[i + 1] if i + 1 < len(sessions) else None
    return None


def _month_keys(sessions: list[date]) -> set[str]:
    return {d.strftime("%Y-%m") for d in sessions}


def _symbol(value: str | None) -> str | None:
    if not value:
        return None
    return value if value.startswith("US.") else f"US.{value}"


def monthly_sec_coverage(rows: list[dict[str, Any]], symbol: str,
                         window_sessions: list[date]) -> tuple[bool, list[str]]:
    """Unknown or partial source coverage always excludes, never means no news."""
    index: dict[tuple[str, str], str] = {}
    for row in rows:
        if row.get("source_kind") == "SEC_FILINGS":
            key = (_symbol(row.get("symbol")), row.get("month"))
            if key in index and index[key] != row.get("status"):
                raise ValueError(f"conflicting monthly source status: {key}")
            index[key] = row.get("status", "UNKNOWN")
    bad = [f"{month}:{index.get((symbol, month), 'MISSING')}"
           for month in sorted(_month_keys(window_sessions))
           if index.get((symbol, month)) != "COMPLETE"]
    return not bad, bad


def _sec_archive_document(doc: dict[str, Any]) -> bool:
    if doc.get("source_kind") not in {"SEC_FILINGS", "ISSUER_EARNINGS"}:
        return False
    parsed = urlparse(doc.get("source_url") or "")
    return parsed.scheme == "https" and parsed.hostname in SEC_ARCHIVE_HOSTS \
        and parsed.path.lower().startswith("/archives/edgar/data/")


def _text_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sec_proxy_announcement_date(doc: dict[str, Any]) -> date:
    """Use the later SEC filing or acceptance date, never the earlier one."""
    filing = date.fromisoformat(doc["sec_filing_date"])
    accepted = datetime.fromisoformat(doc["sec_acceptance_at"].replace("Z", "+00:00"))
    if accepted.tzinfo is None:
        raise ValueError("SEC acceptance timestamp has no timezone")
    conservative = max(filing, accepted.astimezone(NEW_YORK).date())
    announced = date.fromisoformat(doc["announcement_date"])
    if announced != conservative:
        raise ValueError("SEC announcement proxy differs from later filing/acceptance date")
    if doc.get("announcement_date_basis") != "MAX_SEC_FILING_DATE_ACCEPTANCE_ET_DATE":
        raise ValueError("SEC announcement-date basis is not documented")
    return conservative


def load_sec_events(manifest: dict[str, Any], *, config: dict[str, Any],
                    latest_decision: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load only archived accession texts eligible to be considered by validation.

    A fixed-version candidate with a failed body or hash remains an invalid
    event that can exclude rows. Other source families are not C inputs.
    """
    events: dict[str, dict[str, Any]] = {}
    problems: list[dict[str, Any]] = []
    for doc in manifest.get("documents", []):
        symbol = _symbol(doc.get("symbol"))
        if symbol not in SYMBOLS:
            continue
        if not _sec_archive_document(doc):
            if doc.get("source_kind") == "SEC_FILINGS" or \
                    (doc.get("source_kind") == "ISSUER_EARNINGS" and doc.get("sec_accession")):
                problems.append({"document_id": doc.get("document_id"),
                                 "reason": "SEC_ACCESSION_DOCUMENT_NOT_ARCHIVE_URL"})
            continue
        try:
            announced = sec_proxy_announcement_date(doc)
        except (KeyError, TypeError, ValueError):
            problems.append({"document_id": doc.get("document_id"), "reason": "INVALID_SEC_ANNOUNCEMENT_DATE_PROXY"})
            continue
        if announced > latest_decision:
            continue
        event_id = doc.get("event_id")
        if not event_id:
            problems.append({"document_id": doc.get("document_id"), "reason": "NO_EVENT_ID"})
            continue
        key = f"{symbol}:{event_id}"
        event = events.setdefault(key, {"event_id": event_id, "symbol": symbol,
                                        "announcement_date": announced, "docs": [], "problems": []})
        if event["announcement_date"] != announced:
            event["problems"].append("MIXED_ANNOUNCEMENT_DATES")
        event["docs"].append(doc)
    output: list[dict[str, Any]] = []
    for event in events.values():
        chunks: list[str] = []
        doc_lengths: list[dict[str, Any]] = []
        ordered = sorted(event["docs"],
                         key=lambda d: (0 if d.get("source_kind") == "ISSUER_EARNINGS" else 1,
                                        str(d.get("document_id"))))
        per_doc_budget = max(1, int(config["event_text_max_characters"]) // len(ordered))
        for doc in ordered:
            did = doc.get("document_id")
            if doc.get("version_status") != "SEC_ARCHIVE_HASHED":
                event["problems"].append(f"{did}:VERSION_UNKNOWN")
                continue
            if doc.get("full_body_status") != "FULL_BODY":
                event["problems"].append(f"{did}:BODY_INCOMPLETE")
                continue
            body_path = Path(doc.get("body_path") or "")
            if not body_path.is_file() or _text_sha(body_path) != doc.get("body_sha256"):
                event["problems"].append(f"{did}:BODY_HASH_OR_PATH")
                continue
            raw = body_path.read_text(encoding="utf-8")
            cleaned = clean_text(raw)
            if not cleaned:
                event["problems"].append(f"{did}:EMPTY_BODY")
                continue
            retained = cleaned[:per_doc_budget]
            doc_lengths.append({"document_id": did, "original_characters": len(cleaned),
                                "retained_characters": len(retained),
                                "truncated": len(cleaned) > len(retained)})
            chunks.append(retained)
        joined = " ".join(chunks)
        text = joined[:int(config["event_text_max_characters"])]
        output.append({"event_id": event["event_id"], "symbol": event["symbol"],
                       "announcement_date": event["announcement_date"], "text": text,
                       "document_count": len(event["docs"]),
                       "document_character_audit": doc_lengths,
                       "original_group_characters": sum(d["original_characters"] for d in doc_lengths),
                       "retained_group_characters": len(text),
                       "truncated_document_count": sum(d["truncated"] for d in doc_lengths),
                       "problems": sorted(set(event["problems"])),
                       "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    output.sort(key=lambda e: (e["announcement_date"], e["symbol"], e["event_id"]))
    return output, problems


def clean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return " ".join(normalized.split())


def tokenize(value: str) -> list[str]:
    return TOKEN.findall(value)


def fit_tfidf(train_texts: list[str], *, max_features: int, min_df: int
              ) -> tuple[list[str], np.ndarray]:
    df: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for text in train_texts:
        terms = tokenize(text)
        df.update(set(terms))
        total.update(terms)
    vocab = [term for term, _ in sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))
             if df[term] >= min_df][:max_features]
    idf = np.array([math.log((1 + len(train_texts)) / (1 + df[term])) + 1
                    for term in vocab], dtype=float)
    return vocab, idf


def transform_tfidf(texts: list[str], vocab: list[str], idf: np.ndarray) -> np.ndarray:
    index = {word: i for i, word in enumerate(vocab)}
    result = np.zeros((len(texts), len(vocab)), dtype=float)
    for row, text in enumerate(texts):
        count = Counter(tokenize(text))
        for term, frequency in count.items():
            col = index.get(term)
            if col is not None:
                result[row, col] = (1 + math.log(frequency)) * idf[col]
        norm = float(np.linalg.norm(result[row]))
        if norm:
            result[row] /= norm
    return result


def _row_window(example: Any, sessions: list[date]) -> tuple[date, list[date]]:
    by_day = {d: i for i, d in enumerate(sessions)}
    i = by_day[example.label_day]
    if i == 0:
        raise ValueError("label cannot be first market session")
    decision = sessions[i - 1]
    if decision != example.skipped_prior_day:
        raise ValueError("price-feature prior day disagrees with trading calendar")
    if i < 20:
        raise ValueError("insufficient twenty-session announcement lookback")
    return decision, sessions[i - 20:i]


def assemble_rows(examples: list[Any], sessions: list[date], full_sessions: set[date],
                  coverage: list[dict[str, Any]], events: list[dict[str, Any]],
                  config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    event_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        event["eligible_decision_session"] = first_eligible_decision_session(
            event["announcement_date"], sessions, full_sessions)
        event_by_symbol[event["symbol"]].append(event)
    for example in examples:
        decision, window = _row_window(example, sessions)
        covered, gaps = monthly_sec_coverage(coverage, example.symbol, window)
        if not covered:
            excluded.append({"symbol": example.symbol, "label_day": example.label_day.isoformat(),
                             "reason": "SEC_COVERAGE_UNKNOWN", "details": gaps})
            continue
        used = [event for event in event_by_symbol[example.symbol]
                if event["eligible_decision_session"] is not None
                and window[0] <= event["eligible_decision_session"] <= decision]
        failed = [event for event in used if event["problems"] or not event["text"]]
        if failed:
            excluded.append({"symbol": example.symbol, "label_day": example.label_day.isoformat(),
                             "reason": "SEC_DOCUMENT_INCOMPLETE_OR_VERSION_UNKNOWN",
                             "event_ids": [event["event_id"] for event in failed]})
            continue
        agg_budget = int(config["aggregate_text_max_characters"])
        per_event_budget = max(1, agg_budget // max(1, len(used)))
        joined = " ".join(event["text"][:per_event_budget] for event in used)
        text = joined[:agg_budget]
        eligible.append({"symbol": example.symbol, "label_day": example.label_day.isoformat(),
                         "decision_session": decision.isoformat(), "last_feature_day": example.last_feature_day.isoformat(),
                         "label_up": example.label_up, "price_features": list(example.features),
                         "announcement_text": text, "announcement_count": len(used),
                         "sec_source_coverage_complete": True,
                         "event_ids": [event["event_id"] for event in used],
                         "original_aggregate_characters": sum(len(event["text"]) for event in used),
                         "retained_aggregate_characters": len(text),
                         "truncated_event_group_count": sum(len(event["text"]) > per_event_budget for event in used),
                         "input_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    return eligible, excluded


def _split_indices(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    train = np.asarray([i for i, row in enumerate(rows)
                        if date.fromisoformat(row["label_day"]) <= TRAIN_END], dtype=int)
    val = np.asarray([i for i, row in enumerate(rows)
                      if TRAIN_END < date.fromisoformat(row["label_day"]) <= VALIDATION_END], dtype=int)
    return train, val


def exclude_cross_split_event_reuse(rows: list[dict[str, Any]]
                                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """No validation text may repeat an event group seen in train text."""
    train_event_ids = {event for row in rows
                       if date.fromisoformat(row["label_day"]) <= TRAIN_END
                       for event in row["event_ids"]}
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in rows:
        overlap = sorted(train_event_ids.intersection(row["event_ids"]))
        if overlap and date.fromisoformat(row["label_day"]) > TRAIN_END:
            removed.append({"symbol": row["symbol"], "label_day": row["label_day"],
                            "reason": "EVENT_GROUP_CROSSES_TRAIN_VALIDATION",
                            "event_ids": overlap})
        else:
            kept.append(row)
    return kept, removed


def fit_common_comparison(rows: list[dict[str, Any]], config: dict[str, Any]
                          ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    train, val = _split_indices(rows)
    if len(train) < 30 or len(val) < 1:
        raise ValueError("insufficient common eligible train or validation rows")
    y = np.asarray([row["label_up"] for row in rows], dtype=int)
    if set(y[train]) != {0, 1} or set(y[val]) != {0, 1}:
        raise ValueError("common train or validation rows lack both directions")
    if sum(rows[int(i)]["announcement_count"] > 0 for i in val) \
            < config["minimum_validation_event_present_rows"]:
        raise ValueError("no event-present validation row; C text increment cannot be assessed")
    texts = [row["announcement_text"] for row in rows]
    tf = config["tfidf"]
    vocab, idf = fit_tfidf([texts[i] for i in train],
                           max_features=tf["max_features"], min_df=tf["min_document_frequency"])
    if not vocab:
        raise ValueError("no train-fitted TF-IDF vocabulary on qualified source text")
    text_x = transform_tfidf(texts, vocab, idf)
    price_x = np.asarray([row["price_features"] for row in rows], dtype=float)
    extra_x = np.asarray([[row["announcement_count"], int(row["sec_source_coverage_complete"])]
                          for row in rows], dtype=float)
    c_x = np.column_stack((price_x, text_x, extra_x))
    majority = int(np.count_nonzero(y[train]) > len(train) / 2)
    a_pred = np.full(len(y), majority, dtype=int)
    b_l2, b_fitted, b_grid = fit_and_select(price_x[train], y[train], price_x[val], y[val])
    c_l2, c_fitted, c_grid = fit_and_select(c_x[train], y[train], c_x[val], y[val])
    b_pred, _ = predict_up(price_x[val], *b_fitted)
    c_pred, _ = predict_up(c_x[val], *c_fitted)
    summary = {"status": "REAL_REUSED_VALIDATION_DEVELOPMENT",
               "common_train_n": len(train), "common_validation_n": len(val),
               "train_class_distribution": {"down": int(np.count_nonzero(y[train] == 0)),
                                            "up": int(np.count_nonzero(y[train] == 1))},
               "validation_class_distribution": {"down": int(np.count_nonzero(y[val] == 0)),
                                                 "up": int(np.count_nonzero(y[val] == 1))},
               "validation_with_announcement": int(sum(rows[int(i)]["announcement_count"] > 0 for i in val)),
               "validation_without_announcement_verified_sec": int(sum(rows[int(i)]["announcement_count"] == 0 for i in val)),
               "tfidf_vocabulary_size": len(vocab),
               "tfidf_vocabulary_sha256": canonical_hash(vocab),
               "tfidf_idf_sha256": hashlib.sha256(idf.tobytes()).hexdigest(),
               "A": {"train_majority_up": majority, "validation": classification_metrics(y[val], a_pred[val])},
               "B": {"selected_l2": b_l2,
                     "all_validation_candidates": {str(k): v for k, v in b_grid.items()},
                     "validation": classification_metrics(y[val], b_pred)},
               "C": {"selected_l2": c_l2,
                     "all_validation_candidates": {str(k): v for k, v in c_grid.items()},
                     "validation": classification_metrics(y[val], c_pred)}}
    private_predictions = [{"symbol": rows[int(i)]["symbol"],
                            "label_day": rows[int(i)]["label_day"],
                            "actual_up": int(y[int(i)]), "A_predicted_up": int(a_pred[int(i)]),
                            "B_predicted_up": int(b_pred[j]), "C_predicted_up": int(c_pred[j]),
                            "input_text_sha256": rows[int(i)]["input_text_sha256"]}
                           for j, i in enumerate(val)]
    return summary, private_predictions


def run(*, price_dir: Path, calendar_path: Path, manifest_path: Path,
        config_path: Path, private_dir: Path, public_path: Path,
        fit: bool) -> dict[str, Any]:
    config = load_config(config_path)
    sessions, full_sessions = load_calendar(calendar_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    coverage = manifest.get("monthly_coverage", [])
    events, manifest_problems = load_sec_events(
        manifest, config=config, latest_decision=VALIDATION_END)
    private_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"schema": SCHEMA,
                              "role": "RETROSPECTIVE_SEC_DATE_PROXY_REUSED_VALIDATION_ONLY",
                              "strict_historical_availability": "UNPROVEN_UNCHANGED",
                              "source_scope": config["designated_text_source"],
                              "config_sha256": sha256_file(config_path),
                              "calendar_sha256": sha256_file(calendar_path),
                              "corpus_manifest_sha256": sha256_file(manifest_path),
                              "event_groups_considered": len(events),
                              "event_groups_with_truncated_documents": int(sum(e["truncated_document_count"] > 0 for e in events)),
                              "truncated_documents": int(sum(e["truncated_document_count"] for e in events)),
                              "manifest_problems": manifest_problems,
                              "symbols": {}, "test": "NO_JUNE_JULY_OHLCV_EXTRACTED_LABELED_FIT_OR_SCORED_CONTAMINATED"}
    for symbol in SYMBOLS:
        bars, source = load_prevalidation_bars(price_dir, symbol)
        examples = make_direction_examples(symbol, bars)
        rows, excluded = assemble_rows(examples, sessions, full_sessions, coverage,
                                       events, config)
        rows, cross_split_excluded = exclude_cross_split_event_reuse(rows)
        excluded.extend(cross_split_excluded)
        private_rows = private_dir / f"{symbol.replace('.', '_')}_qualified_rows.private.jsonl"
        private_rows.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        private_ex = private_dir / f"{symbol.replace('.', '_')}_excluded_rows.private.jsonl"
        private_ex.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in excluded), encoding="utf-8")
        train, val = _split_indices(rows)
        excluded_dates: dict[str, dict[str, list[str]]] = {"train": {}, "validation": {}}
        for item in excluded:
            split = "train" if date.fromisoformat(item["label_day"]) <= TRAIN_END else "validation"
            excluded_dates[split].setdefault(item["reason"], []).append(item["label_day"])
        for by_reason in excluded_dates.values():
            for dates in by_reason.values():
                dates.sort()
        sym: dict[str, Any] = {"price_source_sha256": source["source_sha256"],
                               "price_example_count_through_validation": len(examples),
                               "price_example_count_by_split": {
                                   "train": sum(e.label_day <= TRAIN_END for e in examples),
                                   "validation": sum(TRAIN_END < e.label_day <= VALIDATION_END for e in examples)},
                               "qualified_train_n": len(train), "qualified_validation_n": len(val),
                               "excluded_count": len(excluded),
                               "excluded_by_reason": dict(Counter(row["reason"] for row in excluded)),
                               "excluded_label_dates_by_reason": excluded_dates,
                               "qualified_with_announcement": int(sum(row["announcement_count"] > 0 for row in rows)),
                               "qualified_without_announcement_verified_sec": int(sum(row["announcement_count"] == 0 for row in rows)),
                               "qualified_rows_with_aggregate_truncation": int(sum(row["truncated_event_group_count"] > 0 for row in rows)),
                               "qualified_rows_sha256": sha256_file(private_rows),
                               "excluded_rows_sha256": sha256_file(private_ex),
                               "comparison_status": "NOT_RUN_PENDING_PRICE_AUDIT" if not fit else "NOT_RUN_GATE"}
        if fit:
            if manifest_problems:
                sym["comparison_status"] = "NOT_RUN_GATE"
                sym["gate_reason"] = "SEC accession manifest has invalid source or date metadata"
            else:
                try:
                    comparison, predictions = fit_common_comparison(rows, config)
                except ValueError as exc:
                    sym["comparison_status"] = "NOT_RUN_GATE"
                    sym["gate_reason"] = str(exc)
                else:
                    private_predictions = private_dir / f"{symbol.replace('.', '_')}_validation_predictions.private.jsonl"
                    private_predictions.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                                                           for row in predictions), encoding="utf-8")
                    sym["comparison_status"] = "REAL_REUSED_VALIDATION_DEVELOPMENT"
                    sym["comparison"] = comparison
                    sym["private_predictions_sha256"] = sha256_file(private_predictions)
        result["symbols"][symbol] = sym
    private_receipt = private_dir / "text_comparison.private.json"
    private_receipt.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--price-dir", type=Path, required=True)
    parser.add_argument("--calendar-path", type=Path, required=True)
    parser.add_argument("--manifest-path", type=Path, required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--public-path", type=Path, required=True)
    parser.add_argument("--fit", action="store_true", help="fit A/B/C only after price audit gate")
    args = parser.parse_args()
    result = run(price_dir=args.price_dir, calendar_path=args.calendar_path,
                 manifest_path=args.manifest_path, config_path=args.config_path,
                 private_dir=args.private_dir, public_path=args.public_path, fit=args.fit)
    print(json.dumps({"schema": result["schema"], "symbols": {
        k: {"status": v["comparison_status"], "train": v["qualified_train_n"],
            "validation": v["qualified_validation_n"], "excluded": v["excluded_count"]}
        for k, v in result["symbols"].items()}}, sort_keys=True))


if __name__ == "__main__":
    main()
