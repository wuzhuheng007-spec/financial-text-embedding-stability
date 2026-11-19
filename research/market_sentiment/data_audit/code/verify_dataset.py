"""Separate post-build self-check against original archived provider responses."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import sqlite3
import zipfile


def verify(original, patch, dataset):
    receipt = json.loads((dataset / "merge_receipt.json").read_text())
    assert hashlib.sha256(original.read_bytes()).hexdigest() == receipt["original_sha256"]
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == receipt["patch_sha256"]
    conn = sqlite3.connect(dataset / "market_research.sqlite")
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    prices_checked = 0
    with zipfile.ZipFile(original) as a, zipfile.ZipFile(patch) as b:
        archives = {receipt["original_sha256"]: a, receipt["patch_sha256"]: b}
        cache = {}
        for row in conn.execute("SELECT * FROM observations"):
            key = (row["source_archive_sha256"], row["source_member"])
            if key not in cache:
                cache[key] = json.loads(archives[key[0]].read(key[1]))
            index = int(row["source_pointer"].split("/")[-1])
            bar = cache[key]["data"]["kline_list"][index]
            day = str(bar["date"])
            assert row["local_date"] == day[:4] + "-" + day[4:6] + "-" + day[6:]
            for name in ["open", "high", "low", "close"]:
                assert row[name] == float(bar[name])
            assert json.loads(row["vendor_fields_json"]) == bar
            prices_checked += 1
    total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert total == receipt["observations_total"] == prices_checked
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE source_stage='PATCH'").fetchone()[0] == 1227
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE research_eligible=0").fetchone()[0] == 167
    assert conn.execute("SELECT COUNT(*) FROM research_daily WHERE symbol='SH.000001' AND local_date='2014-12-20'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM research_daily WHERE symbol='SH.000001' AND local_date='2015-06-24'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM research_weekly WHERE local_date='2026-11-16'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM research_weekly WHERE symbol='SH.000001' AND local_date='2014-12-15'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM research_daily WHERE symbol='SH.000688' AND local_date<'2020-07-23'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM unresolved_dates").fetchone()[0] == 7
    dates0 = {r[0] for r in conn.execute("SELECT local_date FROM research_daily WHERE symbol='US.QQQ' AND autype=0")}
    dates1 = {r[0] for r in conn.execute("SELECT local_date FROM research_daily WHERE symbol='US.QQQ' AND autype=1")}
    assert dates0 == dates1 and len(dates0) == 6967
    for (day,) in conn.execute("SELECT local_date FROM unresolved_dates"):
        assert conn.execute("SELECT COUNT(*) FROM observations WHERE series_id='US..SOX|DAY|a0' AND local_date=?", (day,)).fetchone()[0] == 0
    csv_files_checked = 0
    csv_rows_checked = 0
    registry = json.loads((dataset / "series_registry.json").read_text())
    for series in registry:
        stem = "%s_%s_a%d.csv" % (series["symbol"], series["frequency"], series["autype"])
        for prefix in ["merged_", "research_"]:
            query = "SELECT * FROM observations WHERE series_id=?"
            if prefix == "research_":
                query += " AND research_eligible=1"
            db_rows = {r["local_date"]: r for r in conn.execute(query, (series["series_id"],))}
            with (dataset / "csv" / (prefix + stem)).open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == len(db_rows)
            assert [r["local_date"] for r in rows] == sorted(db_rows)
            for r in rows:
                stored = db_rows[r["local_date"]]
                for key in ["open", "high", "low", "close"]:
                    assert float(r[key]) == stored[key]
                for key in ["available_after_local_date", "source_archive_sha256", "source_member",
                            "source_pointer", "source_stage", "volume_raw", "turnover_raw",
                            "time_key_raw", "time_zone_raw"]:
                    assert r[key] == stored[key]
                assert int(r["research_eligible"]) == stored["research_eligible"]
                assert r["quality_flags"] == "|".join(json.loads(stored["flags_json"]))
            csv_files_checked += 1
            csv_rows_checked += len(rows)
    conn.close()
    result = {"status": "PASS", "verification_type": "Author self-check, separate implementation",
              "raw_provider_records_rechecked": prices_checked, "new_patch_rows_rechecked": 1227,
              "excluded_records_checked": 167, "sqlite_integrity": "ok",
              "all_observation_prices_and_vendor_json_match_raw": True,
              "csv_files_checked": csv_files_checked, "csv_rows_checked": csv_rows_checked,
              "csv_prices_dates_provenance_and_flags_match_database": True,
              "original_and_patch_unchanged": True, "independent_validation": False,
              "investment_efficacy_tested": False}
    (dataset / "audit" / "post_merge_self_check.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    verify(args.original, args.patch, args.dataset)
