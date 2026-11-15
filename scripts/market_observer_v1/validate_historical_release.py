"""Check the public historical handoff against its fixed, redacted receipts."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "results/market_observer_historical_20261115"
REPORTS = [ROOT / "reports/2026-11-15_market_observer_historical_v1.en.md",
           ROOT / "reports/2026-11-15_market_observer_historical_v1.zh.md"]


def _json(name: str) -> dict:
    return json.loads((RESULT / name).read_text(encoding="utf-8"))


def main() -> None:
    price = _json("price_probe_redacted.json")
    etf = _json("etf_expansion_redacted.json")
    news = _json("news_official_redacted.json")
    alignment = _json("event_alignment_redacted.json")
    baseline = _json("price_direction_v3_redacted.json")
    dot = _json("dot_progress_redacted.json")
    contamination = _json("test_contamination_redacted.json")
    status = json.loads((ROOT / "docs/news_research/STATUS.json").read_text(encoding="utf-8"))
    with (RESULT / "plan_execution.csv").open(encoding="utf-8", newline="") as handle:
        plan = list(csv.DictReader(handle))
    assert [row["stage"] for row in plan] == [f"P{i}" for i in range(8)]
    assert price["calendar"]["day_count"] == 335
    assert len(price["series"]) == 6 and all(row["bar_count"] == 335 for row in price["series"])
    assert len(etf["series"]) == 3 and all(row["row_count"] == 335 for row in etf["series"])
    assert news["jin10"]["search_news_MU_in_window"] == 7
    assert alignment["input_events"] == 50 and alignment["strict_pit_eligible"] == 0
    assert set(alignment["status_counts"]) == {"PUBLICATION_PROXY_ONLY"}
    assert baseline["C_news_incremental"] == "NOT_RUN_NO_QUALIFIED_COMPLETE_POINT_IN_TIME_NEWS"
    assert hashlib.sha256((ROOT / "configs/market_observer_historical_v1.json").read_bytes()).hexdigest() == baseline["config_sha256"]
    assert contamination["independent_test_claim_allowed"] is False
    assert dot["mode"] == "STATIC_REDACTED_HANDOFF_NOT_CONNECTED"
    assert status["market_observer_historical_v1"]["strict_point_in_time_news_eligible"] == 0
    for symbol, item in baseline["symbols"].items():
        assert item["source"]["bar_count"] == 335, symbol
        assert [item["splits"][part]["n"] for part in ("train", "validation", "test")] == [229, 41, 43]
        assert item["splits"]["test"]["label_last"] == "2026-07-31"
    checked_links = 0
    for report in REPORTS:
        body = report.read_text(encoding="utf-8")
        for required in ("335", "50", "229", "41", "43", "NOT_RUN"):
            assert required in body, (report.name, required)
        for target in re.findall(r"\]\(([^)]+)\)", body):
            if target.startswith(("https://", "http://", "#")):
                continue
            path = (report.parent / target.split("#", 1)[0]).resolve()
            assert path.is_file(), (report.name, target)
            checked_links += 1
    forbidden = ("access_token", "refresh_token", "bearer ", "account_id", "accid", "hf_")
    reviewed = [*REPORTS, ROOT / "docs/news_research/STATUS.json",
                ROOT / "docs/news_research/README.md", *RESULT.glob("*.json")]
    for path in reviewed:
        body = path.read_text(encoding="utf-8").lower()
        assert not any(secret in body for secret in forbidden), path.name
        assert "c:\\users\\rog" not in body, path.name
    receipt = {
        "schema": "market_observer_historical_release_validation_v1",
        "status": "PASS",
        "plan_stages": len(plan), "report_links_checked": checked_links,
        "pilot_symbols_checked": len(baseline["symbols"]),
        "alignment_candidates": alignment["input_events"],
        "strict_news_eligible": alignment["strict_pit_eligible"],
        "privacy_scanned_files": len(reviewed),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (RESULT / "release_validation_redacted.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
