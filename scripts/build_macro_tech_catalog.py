"""Validate source-bound macro/technology candidate cards and render public indexes.

This tool only reads a manually checked candidate catalog. It does not fetch
news, infer missing expectations, calculate returns, or run a model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse


CATEGORIES = (
    ("MACRO_RELEASE", "宏观数据", "Macro releases"),
    ("MONETARY_POLICY", "货币政策", "Monetary policy"),
    ("TECH_POLICY", "科技与产业政策", "Technology and industrial policy"),
    ("GEO_ENERGY", "地缘与能源", "Geopolitics and energy"),
    ("FINANCIAL_STABILITY", "金融稳定与杠杆", "Financial stability and leverage"),
    ("TECH_CHAIN", "科技产业链", "Pivotal technology chain"),
)
CATEGORY_NAMES = {code: (zh, en) for code, zh, en in CATEGORIES}
SUPPORT_TYPES = {
    "AGENCY_RELEASE", "AGENCY_ASSESSMENT", "OFFICIAL_STATEMENT",
    "ISSUER_STATEMENT", "ISSUER_FILING",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate(catalog: dict) -> dict:
    if catalog.get("schema") != "macro_tech_candidate_events_v1":
        raise ValueError("Unexpected catalog schema")
    if catalog.get("timezone") != "Asia/Shanghai":
        raise ValueError("Missing execution timezone")
    start = date.fromisoformat(catalog["window_start"])
    end = date.fromisoformat(catalog["window_end"])
    checked_at = datetime.fromisoformat(catalog["retrieved_at_local"])
    if checked_at.utcoffset() is None:
        raise ValueError("Source-check timestamp needs a timezone offset")
    if start > end or catalog.get("role") != "DEVELOPMENT_RELEVANCE_CANDIDATES":
        raise ValueError("Invalid window or candidate role")
    events = catalog.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 12:
        raise ValueError("Expected 1 to 12 bounded candidate events")
    ids: set[str] = set()
    clusters: set[str] = set()
    counts: Counter[str] = Counter()
    fact_rows = 0
    for card in events:
        event_id = card.get("id")
        cluster = card.get("event_cluster")
        category = card.get("category")
        if not isinstance(event_id, str) or not event_id or event_id in ids:
            raise ValueError("Missing or duplicate event id")
        if not isinstance(cluster, str) or not cluster or cluster in clusters:
            raise ValueError(f"Missing or duplicate cluster: {event_id}")
        if category not in CATEGORY_NAMES:
            raise ValueError(f"Unknown category: {event_id}")
        event_date = date.fromisoformat(card["date"])
        if not start <= event_date <= end:
            raise ValueError(f"Event outside fixed window: {event_id}")
        for key in ("title_zh", "title_en", "tech_link_zh", "tech_link_en"):
            if not isinstance(card.get(key), str) or not card[key].strip():
                raise ValueError(f"Missing {key}: {event_id}")
        for key in ("facts_zh", "facts_en"):
            if (not isinstance(card.get(key), list) or len(card[key]) != 2
                    or any(not isinstance(x, str) or not x.strip() for x in card[key])):
                raise ValueError(f"Expected two sourced {key}: {event_id}")
        for key in ("unknowns_zh", "unknowns_en"):
            if (not isinstance(card.get(key), list) or not card[key]
                    or any(not isinstance(x, str) or not x.strip() for x in card[key])):
                raise ValueError(f"Expected explicit pending checks in {key}: {event_id}")
        urls = card.get("source_urls")
        if (not isinstance(urls, list) or not urls or len(urls) != len(set(urls))
                or not all(https_url(url) for url in urls)):
            raise ValueError(f"Invalid source URLs: {event_id}")
        date_evidence = card.get("date_evidence")
        if (not isinstance(date_evidence, dict)
                or date_evidence.get("url") not in urls
                or not isinstance(date_evidence.get("locator"), str)
                or not date_evidence["locator"].strip()):
            raise ValueError(f"Unbound event date: {event_id}")
        if (not isinstance(card.get("time_precision"), str)
                or not card["time_precision"].strip()
                or not isinstance(card.get("source_timing_note_zh"), str)
                or not card["source_timing_note_zh"].strip()
                or not isinstance(card.get("source_timing_note_en"), str)
                or not card["source_timing_note_en"].strip()):
            raise ValueError(f"Missing publication-time boundary: {event_id}")
        evidence = card.get("fact_evidence")
        if not isinstance(evidence, list) or len(evidence) != 2:
            raise ValueError(f"Each fact needs a locator: {event_id}")
        for position, row in enumerate(evidence):
            if (row.get("fact_index") != position or row.get("url") not in urls
                    or not isinstance(row.get("locator"), str)
                    or not row["locator"].strip()
                    or not isinstance(row.get("source_title"), str)
                    or not row["source_title"].strip()
                    or row.get("support_type") not in SUPPORT_TYPES):
                raise ValueError(f"Unbound fact {position}: {event_id}")
        if card.get("expectation_status") != "UNKNOWN":
            raise ValueError(f"Unverified pre-release expectation: {event_id}")
        if card.get("tech_link_status") != "HYPOTHESIS":
            raise ValueError(f"Tech transmission must remain a hypothesis: {event_id}")
        if card.get("first_public_at") is not None:
            raise ValueError(f"Unverified exact first-public instant: {event_id}")
        if card.get("status") != "SOURCE_BACKED_CANDIDATE":
            raise ValueError(f"Event mislabelled as admitted: {event_id}")
        if card.get("market_return_status") != "NOT_RUN":
            raise ValueError(f"Market outcome cannot enter discovery cards: {event_id}")
        if card.get("surprise_status") != "NOT_COMPUTED_NO_PRE_RELEASE_CONSENSUS":
            raise ValueError(f"Unverified numerical surprise: {event_id}")
        if category == "MACRO_RELEASE" and not isinstance(card.get("numeric_context"), dict):
            raise ValueError(f"Macro actual/prior/revision context missing: {event_id}")
        ids.add(event_id)
        clusters.add(cluster)
        counts[category] += 1
        fact_rows += 2
    if any(value > 2 for value in counts.values()):
        raise ValueError("More than two cards in one category")
    return {
        "candidate_events": len(events),
        "source_bound_fact_statements": fact_rows,
        "unique_event_clusters": len(clusters),
        "category_counts": {code: counts[code] for code, _, _ in CATEGORIES},
        "missing_pre_release_expectations": sum(
            card["expectation_status"] == "UNKNOWN" for card in events
        ),
        "exact_first_public_instants_known": 0,
        "market_labels": 0,
        "model_calls": 0,
    }


def directory(catalog: dict, language: str, input_hash: str) -> str:
    zh = language == "zh"
    title = ("宏观与科技传导：真实候选事件目录" if zh
             else "Macro and technology transmission: candidate event directory")
    intro = (
        "以下是按官方原件收集的开发候选，供偏好点选。两条事实各有来源定位；科技传导是待检验假设。"
        "公布前共识、实际首次公开的精确时刻、市场收益及历史证券身份未获本轮准入。"
        if zh else
        "These source-bound development candidates are for relevance screening. "
        "Each fact has a source locator; technology transmission is a testable hypothesis. "
        "Pre-release consensus, exact first-public instants, returns and historical security identity "
        "are not admitted here."
    )
    lines = [f"# {title}", "", "2026-11-15 · Asia/Shanghai", "", intro, "",
             f"Catalog SHA-256: `{input_hash}`", ""]
    for code, label_zh, label_en in CATEGORIES:
        cards = [card for card in catalog["events"] if card["category"] == code]
        if not cards:
            continue
        lines.extend([f"## {label_zh if zh else label_en}", ""])
        for card in cards:
            link = card["tech_link_zh" if zh else "tech_link_en"].strip()
            if zh and link.startswith("假设："):
                link = link[len("假设："):].strip()
            if not zh and link.lower().startswith("hypothesis:"):
                link = link[len("hypothesis:"):].strip()
            lines.extend([
                f"### {card['id']} · {card['date']} · {card['title_zh' if zh else 'title_en']}",
                "",
            ])
            for fact in card["facts_zh" if zh else "facts_en"]:
                lines.append(f"- {fact}")
            lines.extend([
                "",
                ("**科技关联假设：** " if zh else "**Technology hypothesis:** ")
                + link,
                "",
                ("**待核验：** " if zh else "**Pending checks:** ")
                + ("；" if zh else "; ").join(card["unknowns_zh" if zh else "unknowns_en"]),
                "",
                ("**时间与证据边界：** " if zh else "**Timing and evidence limit:** ")
                + card.get("source_timing_note_zh" if zh else "source_timing_note_en", "UNKNOWN"),
                "",
                ("**来源：** " if zh else "**Sources:** ")
                + ", ".join(f"[{i + 1}]({url})" for i, url in enumerate(card["source_urls"])),
                "",
            ])
    lines.extend([
        "## " + ("状态" if zh else "Status"), "",
        ("候选事件与来源事实已核；用户偏好 0，人工事实 gold 0，宏观模型推理与金融实验 NOT_RUN。"
         if zh else "Candidate events and source claims checked; preference responses 0, "
         "human fact gold 0, macro model inference and financial experiments NOT_RUN."),
        "",
    ])
    return "\n".join(lines)


def write_index(catalog: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "event_id", "event_cluster", "category", "event_date", "fact_index",
            "source_url", "source_title", "source_locator", "support_type",
            "release_time_text", "time_precision",
            "exact_first_public_status", "expectation_status", "retrieved_at_local",
            "source_version_note",
        ))
        writer.writeheader()
        for card in catalog["events"]:
            for row in card["fact_evidence"]:
                writer.writerow({
                    "event_id": card["id"],
                    "event_cluster": card["event_cluster"],
                    "category": card["category"],
                    "event_date": card["date"],
                    "fact_index": row["fact_index"],
                    "source_url": row["url"],
                    "source_title": row["source_title"],
                    "source_locator": row["locator"],
                    "support_type": row["support_type"],
                    "release_time_text": card.get("release_time_text") or "UNKNOWN",
                    "time_precision": card.get("time_precision", "DATE_ONLY"),
                    "exact_first_public_status": "UNKNOWN",
                    "expectation_status": card["expectation_status"],
                    "retrieved_at_local": catalog["retrieved_at_local"],
                    "source_version_note": card.get("source_version_note", ""),
                })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--out-zh", type=Path, required=True)
    parser.add_argument("--out-en", type=Path, required=True)
    parser.add_argument("--out-index", type=Path, required=True)
    parser.add_argument("--out-receipt", type=Path, required=True)
    args = parser.parse_args()
    targets = (args.out_zh, args.out_en, args.out_index, args.out_receipt)
    if any(path.exists() for path in targets):
        raise FileExistsError("Use new output paths; preserve previous candidate results")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    summary = validate(catalog)
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
    input_hash = sha256(args.catalog)
    args.out_zh.write_text(directory(catalog, "zh", input_hash), encoding="utf-8")
    args.out_en.write_text(directory(catalog, "en", input_hash), encoding="utf-8")
    write_index(catalog, args.out_index)
    receipt = {
        "schema": "macro_tech_catalog_build_receipt_v1",
        "catalog_sha256": input_hash,
        "candidate_selection_role": catalog["role"],
        "retrieved_at_local": catalog["retrieved_at_local"],
        **summary,
        "outputs_sha256": {str(path): sha256(path) for path in targets[:3]},
        "limitations": "Finite source-bound development cards, not an admitted event panel or financial result",
    }
    args.out_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
