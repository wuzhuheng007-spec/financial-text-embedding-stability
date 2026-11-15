"""Read back the real six-issuer intake and its local source captures."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/news_earnings_macro_20261115"
HEX = re.compile(r"^[0-9a-f]{64}$")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name: str) -> dict:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def check() -> dict:
    receipt = read("assembly_receipt.json")
    config = read("intake_config.json")
    contract = json.loads((ROOT / "docs/news_research/earnings/metric_contract.json").read_text(encoding="utf-8"))
    cards = read("company_cards.json")
    macro = read("macro_cards.json")
    manifest = read("source_manifest.json")
    news = read("news_events.json")
    market = read("market_observations.json")
    growth = read("growth_calculations.json")
    assert receipt["as_of_utc"] == config["frozen_as_of_utc"] == cards["as_of_utc"]
    for rel, expected in receipt["partial_sha256"].items():
        path = (ROOT / rel).resolve()
        assert path.is_relative_to(ROOT) and path.is_file() and digest(path) == expected
    for name, expected in receipt["outputs_sha256"].items():
        assert digest(RESULTS / name) == expected
    sources = manifest["sources"]
    assert len(sources) == 41
    for source in sources:
        assert HEX.fullmatch(source["sha256"])
        assert source["captured_local_bytes_verified"]
        path = (ROOT / source["local_ignored_artifact"]).resolve()
        assert path.is_relative_to(ROOT) and path.is_file() and digest(path) == source["sha256"]
        assert source["point_in_time_snapshot_at_cutoff"] is False
    registered = {(source["issuer_id"], source["url"], source["sha256"]) for source in sources}
    assert len(cards["cards"]) == 6
    metrics = [row for card in cards["cards"] for row in card["metrics"]]
    assert len(metrics) == 72
    for card in cards["cards"]:
        assert {row["metric"] for row in card["metrics"]} == set(contract["required_metrics"])
        assert len(card["metrics"]) == 12
    with (RESULTS / "earnings_metrics.jsonl").open(encoding="utf-8") as handle:
        long_rows = [json.loads(line) for line in handle]
    assert metrics == long_rows
    with (RESULTS / "coverage.csv").open(encoding="utf-8", newline="") as handle:
        coverage = list(csv.DictReader(handle))
    assert len(coverage) == len(metrics)
    for row in metrics:
        if row["raw_value"] is not None:
            assert (row["issuer_id"], row["source_url"], row["source_sha256"]) in registered
        assert row["first_available_at"] is None
    roles = Counter(row["value_role"] for row in metrics if row["raw_value"] is not None)
    assert roles == {"ACTUAL": 57, "DERIVED": 3}
    assert sum(row["raw_value"] is None for row in metrics) == 12
    assert receipt["reported_core_values"] == 57 and receipt["derived_core_values"] == 3
    assert len(macro["cards"]) == 3
    for event in macro["cards"]:
        assert ("MACRO", event["source_url"], event["source_sha256"]) in registered
        assert ("MACRO", event["prior_source_url"], event["prior_source_sha256"]) in registered
        assert event["first_available_at_utc"] is None
        assert event["market_outcome_status"] == "NOT_RUN"
    assert len(news["events"]) == 6
    assert len({row["issuer_id"] for row in news["events"]}) == 6
    cutoff = datetime.fromisoformat(config["frozen_as_of_utc"].replace("Z", "+00:00"))
    for event in news["events"]:
        assert datetime.fromisoformat(event["event_date"]).date() <= cutoff.date()
        assert (event["issuer_id"], event["source_url"], event["source_sha256"]) in registered
        assert event["market_outcome_status"] == "NOT_RUN"
    assert len(growth["calculations"]) == 36
    assert growth["computed"] == 32 and growth["not_computed"] == 4
    for row in growth["calculations"]:
        if row["status"] == "COMPUTED":
            assert row["formula"] and len(row["input_row_ids"]) == 2
            assert (row["issuer_id"], row["current_source_url"], row["current_source_sha256"]) in registered
            assert (row["issuer_id"], row["prior_source_url"], row["prior_source_sha256"]) in registered
    assert market["status"] == "NOT_RUN" and not market["observations"]
    dashboard = (RESULTS / "dashboard.html").read_text(encoding="utf-8")
    assert all(token in dashboard for token in ('id="earnings"', 'id="macro"', 'id="news"',
                                                'AI_ANALYST_DRAFT', 'financial evaluation NOT_RUN'))
    for path in RESULTS.iterdir():
        if path.suffix not in {".json", ".jsonl", ".csv", ".html"}:
            continue
        body = path.read_text(encoding="utf-8")
        assert str(ROOT).lower() not in body.lower(), path.name
        assert not re.search(r"hf_[A-Za-z0-9]{30,}", body), path.name
    return {
        "schema": "earnings_macro_real_readback_audit_v1", "status": "PASS",
        "as_of_utc": config["frozen_as_of_utc"],
        "issuer_cards": len(cards["cards"]), "required_metric_checks": len(metrics),
        "reported_values": roles["ACTUAL"], "derived_values": roles["DERIVED"],
        "explicit_missing": 12, "verified_source_captures": len(sources),
        "macro_cards": len(macro["cards"]), "selected_company_news": len(news["events"]),
        "growth_computed": growth["computed"], "growth_not_computed": growth["not_computed"],
        "model_calls": 0, "market_observations": 0, "financial_experiments": "NOT_RUN",
        "limitation": "Checks source bytes, references, units/roles and product consistency; does not prove historical first availability or visual layout",
    }


def main() -> None:
    output = RESULTS / "integrity_audit.json"
    if output.exists():
        raise FileExistsError(output)
    result = check()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
