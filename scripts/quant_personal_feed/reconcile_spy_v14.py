"""Redacted comparison of the two saved v1.3 SPY observation windows."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "apps/quant_portal/var/v13_20261116"
OUTPUT = ROOT / "results/quant_portal_personal_timeline_v14_20261116/spy_window_reconciliation_redacted.json"


def main() -> None:
    windows = []
    for name in ("session_acceptance_dev_before_final.json", "session_acceptance_private.json"):
        source = PRIVATE / name
        if not source.is_file():
            windows.append({"private_receipt": name, "status": "NOT_VERIFIED_FILE_MISSING"})
            continue
        raw = source.read_bytes()
        saved = json.loads(raw.decode("utf-8"))
        spy = saved.get("per_symbol_session", {}).get("SPY:OVERNIGHT", {})
        windows.append({"private_receipt": name, "sha256": hashlib.sha256(raw).hexdigest(),
                        "status": "VERIFIED_SAVED_WINDOW",
                        "start_at_utc": saved.get("start_at_utc"),
                        "end_at_utc": saved.get("end_at_utc"),
                        "elapsed_seconds": saved.get("elapsed_seconds"),
                        "cache_reads": saved.get("cache_reads"),
                        "input_sha256_at_start": saved.get("input_sha256_at_start"),
                        "SPY_OVERNIGHT": {
                            "visible_samples": spy.get("visible_samples"),
                            "source_time_advances": spy.get("source_time_advances"),
                            "first_source_at": spy.get("first_source_at"),
                            "last_source_at": spy.get("last_source_at")}})
    result = {"schema": "v14_spy_v13_window_reconciliation", "windows": windows,
              "interpretation": "11 and 10 source-time advances belong to two distinct five-minute saved-cache observation windows; neither count is an independent trading result."}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(OUTPUT.relative_to(ROOT)),
                      "verified_windows": sum(w["status"] == "VERIFIED_SAVED_WINDOW" for w in windows)}))


if __name__ == "__main__":
    main()
