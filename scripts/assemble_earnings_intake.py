"""Assemble six source-bound issuer partials into shareable research inputs.

The partial files and source captures remain under ignored local artifacts.
This script preserves their sparse values and explicit gaps. It does not infer
missing earnings, consensus, market returns or historic security identity.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ISSUERS = {
    "NVIDIA", "AMD", "INTEL", "COREWEAVE", "SAMSUNG_ELECTRONICS", "SK_HYNIX",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MACRO_RECEIPTS = (
    "cpi_202610", "cpi_202609", "jobs_202610", "jobs_202609",
    "fomc_20261028", "fomc_20260916",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Non-numeric value: {value!r}") from error
    if not result.is_finite():
        raise ValueError("Nonfinite numeric value")
    return result


def load_macro_sources() -> list[dict]:
    macro = json.loads((ROOT / "results/news_earnings_macro_20261115/macro_cards.json").read_text(encoding="utf-8"))
    expected = {url for card in macro["cards"]
                for url in (card["source_url"], card["prior_source_url"])}
    rows = []
    for stem in MACRO_RECEIPTS:
        receipt_path = ROOT / "artifacts/earnings_20261115/sources/macro" / f"{stem}.receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        raw = (ROOT / receipt["local_path"]).resolve()
        if not raw.is_relative_to(ROOT) or sha256(raw) != receipt["sha256"]:
            raise ValueError(f"Macro capture mismatch: {stem}")
        if receipt["http_status"] != 200 or receipt["requested_url"] not in expected:
            raise ValueError(f"Unexpected macro source: {stem}")
        rows.append({
            "issuer_id": "MACRO", "source_id": stem, "url": receipt["requested_url"],
            "sha256": receipt["sha256"], "captured_local_bytes_verified": True,
            "local_ignored_artifact": raw.relative_to(ROOT).as_posix(),
            "retrieved_at": receipt["retrieved_at_utc"],
            "kind": "OFFICIAL_ARCHIVED_RELEASE_CURRENT_CAPTURE",
            "point_in_time_snapshot_at_cutoff": False,
        })
    if {row["url"] for row in rows} != expected:
        raise ValueError("Macro source coverage mismatch")
    return rows


def load_partials(paths: list[Path], contract: dict, cutoff: datetime) -> tuple[list[dict], list[dict], dict, list[dict]]:
    required = set(contract["required_metrics"])
    permitted_missing = set(contract["missing_reasons"])
    issuers: list[dict] = []
    sources: list[dict] = []
    selected_news: list[dict] = []
    input_hashes: dict[str, str] = {}
    seen: set[str] = set()
    for path in paths:
        partial = json.loads(path.read_text(encoding="utf-8"))
        issuer = partial["issuer"]
        if issuer not in EXPECTED_ISSUERS or issuer in seen:
            raise ValueError(f"Unexpected or duplicate issuer: {issuer}")
        seen.add(issuer)
        if not path.is_relative_to(ROOT):
            raise ValueError(f"Partial outside repository: {issuer}")
        input_hashes[path.relative_to(ROOT).as_posix()] = sha256(path)
        partial_cutoff = datetime.fromisoformat(partial["as_of_utc"].replace("Z", "+00:00"))
        if partial_cutoff.tzinfo is None or abs((partial_cutoff - cutoff).total_seconds()) > 1:
            raise ValueError(f"Cutoff mismatch: {issuer}")
        if datetime.fromisoformat(partial["report_date"]).date() > cutoff.date():
            raise ValueError(f"Report is after cutoff: {issuer}")
        per_issuer_sources: dict[tuple[str, str | None], dict] = {}
        public_sources: list[dict] = []
        for source in partial["sources"]:
            url = source["url"]
            digest = source.get("sha256")
            if not url.startswith("https://"):
                raise ValueError(f"Non-HTTPS source: {issuer}")
            if digest is not None and not HEX64.fullmatch(digest):
                raise ValueError(f"Malformed source hash: {issuer}")
            local_file = source.get("file")
            captured = False
            repo_relative = None
            if local_file:
                raw = (path.parent / local_file).resolve()
                if not raw.is_relative_to(ROOT):
                    raise ValueError(f"Source outside repository: {issuer}")
                if not raw.is_file() or digest is None or sha256(raw) != digest:
                    raise ValueError(f"Missing or mismatched source capture: {issuer} {url}")
                captured = True
                repo_relative = raw.relative_to(ROOT).as_posix()
            row = {
                "issuer_id": issuer,
                "source_id": source.get("id"),
                "url": url,
                "sha256": digest,
                "captured_local_bytes_verified": captured,
                "local_ignored_artifact": repo_relative,
                "published_at": source.get("published_at"),
                "retrieved_at": source.get("retrieved_at"),
                "kind": source.get("kind"),
                "status": source.get("status"),
                "point_in_time_snapshot_at_cutoff": False,
            }
            key = (url, digest)
            if key in per_issuer_sources:
                raise ValueError(f"Duplicate source: {issuer} {url}")
            per_issuer_sources[key] = row
            public_sources.append(row)
            sources.append(row)
        metrics = partial["metrics"]
        names = [row["metric"] for row in metrics]
        if len(names) != len(set(names)) or set(names) != required:
            raise ValueError(f"Required metric coverage mismatch: {issuer}")
        for row in metrics:
            for key in contract["required_dimensions"]:
                if key not in row:
                    raise ValueError(f"Missing {key}: {issuer}/{row['metric']}")
            if row["issuer_id"] != issuer or row["value_role"] not in {"ACTUAL", "DERIVED"}:
                raise ValueError(f"Unexpected role or issuer: {issuer}/{row['metric']}")
            if row["value_role"] == "DERIVED" and (not row.get("formula") or not row.get("input_row_ids")):
                raise ValueError(f"Derived value without formula and inputs: {issuer}/{row['metric']}")
            if row["period_start"] != partial["report_period_start"] or row["period_end"] != partial["report_period_end"]:
                raise ValueError(f"Wrong period: {issuer}/{row['metric']}")
            key = (row["source_url"], row["source_sha256"])
            if key not in per_issuer_sources:
                raise ValueError(f"Unregistered source: {issuer}/{row['metric']}")
            if not row["source_locator"]:
                raise ValueError(f"Missing locator: {issuer}/{row['metric']}")
            if row["first_available_at"] is not None:
                raise ValueError(f"Unverified first-availability time: {issuer}")
            if row["raw_value"] is None:
                if row["normalized_value"] is not None or row["missing_reason"] not in permitted_missing:
                    raise ValueError(f"Missing value without permitted reason: {issuer}/{row['metric']}")
            else:
                if row["missing_reason"] is not None or row["source_sha256"] is None:
                    raise ValueError(f"Numeric value without source hash: {issuer}/{row['metric']}")
                if not per_issuer_sources[key]["captured_local_bytes_verified"]:
                    raise ValueError(f"Numeric value without verified local capture: {issuer}/{row['metric']}")
                raw = decimal(row["raw_value"])
                normalized = decimal(row["normalized_value"])
                scale = decimal(row["scale"])
                if normalized != raw * scale:
                    raise ValueError(f"Scale mismatch: {issuer}/{row['metric']}")
        for guidance in partial.get("guidance", []):
            key = (guidance["source_url"], guidance["source_sha256"])
            if key not in per_issuer_sources or not per_issuer_sources[key]["captured_local_bytes_verified"]:
                raise ValueError(f"Guidance has no captured source: {issuer}")
            for name in ("low", "high", "point_estimate"):
                if guidance.get(name) is not None:
                    decimal(guidance[name])
        for news in partial.get("news", []):
            key = (news["source_url"], news["source_sha256"])
            if key not in per_issuer_sources or not per_issuer_sources[key]["captured_local_bytes_verified"]:
                raise ValueError(f"News has no captured source: {issuer}")
            if not news.get("not_earnings_duplicate"):
                raise ValueError(f"Earnings duplicate in news list: {issuer}")
            if datetime.fromisoformat(news["event_date"]).date() > cutoff.date():
                raise ValueError(f"Post-cutoff news: {issuer}")
            if news.get("first_available_at") is not None:
                raise ValueError(f"Unverified first availability: {issuer}")
        if partial.get("news"):
            latest = sorted(partial["news"], key=lambda row: (row["event_date"], row["event_id"]))[-1]
            selected_news.append({"issuer_id": issuer, **latest,
                "selection_rule": "Latest captured, source-bound, separate issuer news by event date before cutoff; one per issuer",
                "market_outcome_status": "NOT_RUN"})
        public = {key: value for key, value in partial.items()
                  if key not in {"sources", "news"}}
        public["sources"] = public_sources
        public["news_event_ids"] = [row["event_id"] for row in partial.get("news", [])]
        if str(ROOT).lower() in json.dumps(public, ensure_ascii=False).lower():
            raise ValueError(f"Personal absolute path in shareable card: {issuer}")
        issuers.append(public)
    if seen != EXPECTED_ISSUERS:
        raise ValueError(f"Missing core issuers: {EXPECTED_ISSUERS - seen}")
    if len({row["source_url"] for row in selected_news}) != len(selected_news):
        raise ValueError("Duplicate cross-issuer news source; review cluster before publication")
    return issuers, sources, input_hashes, selected_news


def products(partials: list[dict], sources: list[dict], input_hashes: dict,
             selected_news: list[dict], config: dict, contract: dict) -> dict:
    metrics = [row for issuer in partials for row in issuer["metrics"]]
    counts = Counter(row["issuer_id"] for row in metrics if row["raw_value"] is not None)
    coverage = [
        {
            "issuer_id": row["issuer_id"],
            "metric": row["metric"],
            "status": ("SOURCE_BOUND_DERIVED" if row["value_role"] == "DERIVED" else "SOURCE_BOUND_REPORTED")
                      if row["raw_value"] is not None else row["missing_reason"],
            "period_end": row["period_end"],
            "source_url": row["source_url"],
            "source_sha256": row["source_sha256"] or "",
            "source_locator": row["source_locator"],
        }
        for row in metrics
    ]
    return {
        "schema": "earnings_intake_products_v1",
        "as_of_utc": config["frozen_as_of_utc"],
        "input_commit": config["input_commit"],
        "partial_sha256": input_hashes,
        "issuers": len(partials),
        "required_metric_checks": len(metrics),
        "required_metric_checks_per_issuer": len(contract["required_metrics"]),
        "source_bound_core_values": sum(counts.values()),
        "reported_core_values": sum(row["raw_value"] is not None and row["value_role"] == "ACTUAL" for row in metrics),
        "derived_core_values": sum(row["raw_value"] is not None and row["value_role"] == "DERIVED" for row in metrics),
        "source_bound_core_values_by_issuer": dict(sorted(counts.items())),
        "missing_core_checks": sum(row["raw_value"] is None for row in metrics),
        "captured_source_documents": sum(row["captured_local_bytes_verified"] for row in sources),
        "source_entries": len(sources),
        "consensus_values": 0,
        "new_model_calls": 0,
        "financial_labels": 0,
        "financial_experiments": "NOT_RUN",
        "selected_company_news": len(selected_news),
        "scope": "SOURCE_BOUND_DEVELOPMENT_CARDS_NOT_ADMITTED_FINANCIAL_SAMPLE",
        "company_cards": {"schema": "company_earnings_cards_v1", "as_of_utc": config["frozen_as_of_utc"], "cards": partials},
        "metrics": metrics,
        "coverage": coverage,
        "sources": sources,
        "news_events": {"schema": "source_bound_company_news_v1", "as_of_utc": config["frozen_as_of_utc"],
                        "selection_is_not_statistical_sampling": True, "events": selected_news},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partials", nargs=6, type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / "results/news_earnings_macro_20261115/intake_config.json").read_text(encoding="utf-8"))
    contract = json.loads((ROOT / "docs/news_research/earnings/metric_contract.json").read_text(encoding="utf-8"))
    cutoff = datetime.fromisoformat(config["frozen_as_of_utc"].replace("Z", "+00:00"))
    paths = [path.resolve() for path in args.partials]
    cards, sources, input_hashes, selected_news = load_partials(paths, contract, cutoff)
    macro_sources = load_macro_sources()
    output = products(cards, sources, input_hashes, selected_news, config, contract)
    out = args.out_dir
    targets = [out / name for name in (
        "company_cards.json", "earnings_metrics.jsonl", "coverage.csv", "source_manifest.json",
        "news_events.json", "market_observations.json", "assembly_receipt.json",
    )]
    if any(target.exists() for target in targets):
        raise FileExistsError("Preserve existing outputs; use a versioned directory")
    out.mkdir(parents=True, exist_ok=True)
    targets[0].write_text(json.dumps(output["company_cards"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    targets[1].write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output["metrics"]), encoding="utf-8", newline="\n")
    with targets[2].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(output["coverage"][0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(output["coverage"])
    targets[3].write_text(json.dumps({
        "schema": "earnings_source_manifest_v1",
        "as_of_utc": config["frozen_as_of_utc"],
        "sources": output["sources"] + macro_sources,
        "macro_captured_source_documents": len(macro_sources),
        "interpretation": "Current source captures; point-in-time release snapshots and redistribution rights are not established",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    targets[4].write_text(json.dumps(output["news_events"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    targets[5].write_text(json.dumps({"schema": "market_observations_v1", "status": "NOT_RUN",
        "as_of_utc": config["frozen_as_of_utc"], "observations": [],
        "reason": "No financial price protocol, historical identity chain or event labels approved"},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    receipt = {key: value for key, value in output.items()
               if key not in {"company_cards", "metrics", "coverage", "sources", "news_events"}}
    receipt["outputs_sha256"] = {target.name: sha256(target) for target in targets[:6]}
    targets[6].write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: receipt[key] for key in (
        "issuers", "required_metric_checks", "source_bound_core_values",
        "missing_core_checks", "captured_source_documents",
    )}))


if __name__ == "__main__":
    main()
