"""Reconcile immutable moomoo archives and write a flagged research price dataset.

No network calls, token access, synthetic price filling, or strategy evaluation.
"""
from pathlib import Path
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sqlite3
import zipfile

EXPECTED_ORIGINAL = "85aa32f5749ceadd3b5eb28245b693063136552a81756edb302acf49b1eeb309"
EXPECTED_PATCH = "08ff7106037db53ddc45dc86f93f5d7dcfd3df5e47471d679e02317f1db0f170"
PRICE_FIELDS = ["open", "high", "low", "close"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def member(name):
    n = name.replace("\\", "/")
    if n.startswith("/") or ":" in n.split("/")[0] or any(x in {"", ".", ".."} for x in n.split("/")):
        raise ValueError("Unsafe archive member")
    return n


def read_archive(path, expected, manifest_name):
    assert sha(path) == expected, "Input archive identity differs"
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None, "CRC failure"
        names = {}
        for n in z.namelist():
            key = member(n)
            assert key not in names, "Ambiguous normalized member"
            names[key] = n
        hashes = json.loads(z.read(names[manifest_name]))
        for n, value in hashes.items():
            assert hashlib.sha256(z.read(names[member(n)])).hexdigest() == value, n
        contents = {n: z.read(original) for n, original in names.items()}
    return contents, len(hashes)


def load_json(contents, name):
    return json.loads(contents[member(name)])


def csv_rows(contents, name):
    return list(csv.DictReader(io.StringIO(contents[name].decode("utf-8-sig"))))


def raw_day(bar):
    return dt.datetime.strptime(str(bar["date"]), "%Y%m%d").date().isoformat()


def validate_price(bar):
    o, hi, lo, c = [float(bar[k]) for k in PRICE_FIELDS]
    assert all(math.isfinite(v) for v in [o, hi, lo, c])
    assert 0 < lo <= min(o, c) <= max(o, c) <= hi


def primary_raw_map(contents, windows):
    found = {}
    for w in windows:
        if w.get("status") == "FAILED":
            continue
        for filename in w.get("pages", []):
            raw = load_json(contents, filename)
            assert raw.get("ret_code", raw.get("code", 0)) == 0
            for i, bar in enumerate(raw.get("data", {}).get("kline_list", [])):
                day = raw_day(bar)
                if w["start"] <= day <= w["end"]:
                    validate_price(bar)
                    if day in found:
                        assert all(found[day]["bar"].get(k) == bar.get(k)
                                   for k in PRICE_FIELDS + ["volume", "turnover"]), "Raw conflict"
                    found[day] = {"bar": bar, "member": member(filename),
                                  "pointer": "/data/kline_list/%d" % i}
    return found


def build(original_path, patch_path, out, policy_path):
    original, original_hashes = read_archive(original_path, EXPECTED_ORIGINAL, "SHA256SUMS.json")
    patch, patch_hashes = read_archive(patch_path, EXPECTED_PATCH, "SHA256SUMS.json")
    policy = json.loads(policy_path.read_text())
    summary = load_json(original, "coverage_summary.json")
    patch_plan = load_json(patch, "repair_plan.json")
    patch_result = {x["id"]: x for x in load_json(patch, "repair_results.json")}
    assert load_json(patch, "receipt.json")["source_zip_sha256"] == EXPECTED_ORIGINAL
    out.mkdir(parents=True, exist_ok=False)
    (out / "csv").mkdir()
    (out / "audit").mkdir()
    series = {}
    reconciliation = []
    for s in summary:
        name = s["file"]
        rows = csv_rows(original, name)
        windows = load_json(original, name[:-4] + "_windows.json")
        raw = primary_raw_map(original, windows)
        assert {r["local_date"] for r in rows} == set(raw)
        mapped = {}
        for r in rows:
            d = r["local_date"]
            assert all(float(r[k]) == float(raw[d]["bar"][k]) for k in PRICE_FIELDS)
            mapped[d] = {"row": r, "raw": raw[d]["bar"], "archive_sha256": EXPECTED_ORIGINAL,
                         "source_member": raw[d]["member"], "source_pointer": raw[d]["pointer"],
                         "source_stage": "ORIGINAL"}
        assert len(mapped) == len(rows)
        series[name] = {"metadata": s, "rows": mapped}
        reconciliation.append({"file": name, "original_rows": len(rows), "raw_prices_match": True})
    # Verify patch saved bars against the original provider response.
    for task in patch_plan["tasks"]:
        if "symbol" not in task:
            continue
        saved = load_json(patch, task["id"] + "_bars.json")
        raw = {}
        for f in patch_result[task["id"]].get("response_files", []):
            payload = load_json(patch, f)
            assert payload.get("ret_code", payload.get("code", 0)) == 0
            for i, b in enumerate(payload.get("data", {}).get("kline_list", [])):
                d = raw_day(b)
                if task["start"] <= d <= task["end"]:
                    raw[d] = {"bar": b, "member": f, "pointer": "/data/kline_list/%d" % i}
        assert saved == {d: x["bar"] for d, x in raw.items()}, "Patch/raw mismatch"
        for b in saved.values():
            validate_price(b)
    anchors = []
    for key in ["qqq_adjustment_anchor_early", "qqq_adjustment_anchor_recent"]:
        old = series["US.QQQ_DAY_a1.csv"]["rows"]
        for d, b in load_json(patch, key + "_bars.json").items():
            assert d in old
            assert all(float(old[d]["row"][k]) == float(b[k]) for k in PRICE_FIELDS)
            anchors.append(d)
    assert len(anchors) == 36
    additions = []
    for task in patch_plan["tasks"]:
        if task["kind"] != "FAILED_WINDOW_PATCH":
            continue
        new = load_json(patch, task["id"] + "_bars.json")
        assert set(new) == set(task["expected_reference_dates"])
        filename = task["symbol"] + "_DAY_a%d.csv" % task["autype"]
        target = series[filename]["rows"]
        f = patch_result[task["id"]]["response_files"][0]
        raw_list = load_json(patch, f)["data"]["kline_list"]
        indices = {raw_day(b): i for i, b in enumerate(raw_list)}
        for d, b in new.items():
            assert d not in target, "Patch overlaps accepted original data"
            row = {"symbol": task["symbol"], "local_date": d, "frequency": "DAY",
                   "autype": str(task["autype"]), **{k: str(b[k]) for k in PRICE_FIELDS},
                   "volume_raw": str(b.get("volume", "")), "turnover_raw": str(b.get("turnover", "")),
                   "time_key_raw": str(b.get("time_key", "")), "time_zone_raw": str(b.get("time_zone", "")),
                   "before_live_index_publication": "False"}
            target[d] = {"row": row, "raw": b, "archive_sha256": EXPECTED_PATCH,
                         "source_member": f, "source_pointer": "/data/kline_list/%d" % indices[d],
                         "source_stage": "PATCH"}
        additions.append({"task": task["id"], "rows_added": len(new), "source_member": f})
    missing = policy["unresolved"]["sox_daily_candidate_dates"]
    assert all(d not in series["US..SOX_DAY_a0.csv"]["rows"] for d in missing)
    for task in patch_plan["tasks"]:
        if task["kind"] == "CANDIDATE_GAP_RECHECK":
            assert load_json(patch, task["id"] + "_bars.json") == {}
    db = sqlite3.connect(out / "market_research.sqlite")
    db.executescript("""
        CREATE TABLE input_archives(name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, manifest_files INTEGER NOT NULL);
        CREATE TABLE series(series_id TEXT PRIMARY KEY, symbol TEXT, frequency TEXT, autype INTEGER);
        CREATE TABLE observations(
          series_id TEXT, local_date TEXT, available_after_local_date TEXT,
          open REAL, high REAL, low REAL, close REAL, volume_raw TEXT, turnover_raw TEXT,
          time_key_raw TEXT, time_zone_raw TEXT, research_eligible INTEGER,
          flags_json TEXT, source_archive_sha256 TEXT, source_member TEXT,
          source_pointer TEXT, source_stage TEXT, vendor_fields_json TEXT,
          PRIMARY KEY(series_id,local_date));
        CREATE TABLE unresolved_dates(symbol TEXT, frequency TEXT, local_date TEXT,
          reason TEXT, evidence TEXT, PRIMARY KEY(symbol,frequency,local_date));
        CREATE VIEW research_daily AS
          SELECT o.*,s.symbol,s.autype FROM observations o JOIN series s USING(series_id)
          WHERE s.frequency='DAY' AND o.research_eligible=1;
        CREATE VIEW research_weekly AS
          SELECT o.*,s.symbol,s.autype FROM observations o JOIN series s USING(series_id)
          WHERE s.frequency='WEEK' AND o.research_eligible=1;
    """)
    db.executemany("INSERT INTO input_archives VALUES(?,?,?)",
                  [(original_path.name, EXPECTED_ORIGINAL, original_hashes),
                   (patch_path.name, EXPECTED_PATCH, patch_hashes)])
    registry, exclusions, provenance, all_records = [], [], [], []
    cutoff = dt.date.fromisoformat(policy["cutoff"])
    for filename, data in sorted(series.items()):
        meta, entries = data["metadata"], data["rows"]
        sid = "%s|%s|a%d" % (meta["symbol"], meta["frequency"], meta["autype"])
        db.execute("INSERT INTO series VALUES(?,?,?,?)",
                   (sid, meta["symbol"], meta["frequency"], meta["autype"]))
        kept = []
        merged = []
        for d, entry in sorted(entries.items()):
            r = dict(entry["row"])
            date = dt.date.fromisoformat(d)
            flags = []
            eligible = True
            available = date
            if meta["frequency"] == "WEEK":
                assert date.weekday() == 0, "Unverified weekly label semantics"
                available = date + dt.timedelta(days=4)
                if available > cutoff:
                    flags.append("INCOMPLETE_WEEK_AT_CUTOFF")
                    eligible = False
                if meta["symbol"] == "SH.000001" and d == "2014-12-15":
                    flags.append("WEEK_CONTAINS_RECONFIRMED_SATURDAY_ANOMALY")
                    eligible = False
                week_gaps = [x for x in missing
                             if date <= dt.date.fromisoformat(x) <= available]
                if meta["symbol"] == "US..SOX" and week_gaps:
                    flags.append("UNDERLYING_DAILY_COVERAGE_GAP_REMAINS")
            if meta["frequency"] == "DAY" and date.weekday() >= 5:
                flags.append("NONTRADING_WEEKEND_SOURCE_ANOMALY")
                eligible = False
            prepublication = str(r.get("before_live_index_publication", "")).lower() == "true"
            if prepublication:
                flags.append("PRE_LIVE_INDEX_PUBLICATION")
                eligible = False
            if meta["symbol"] == "US..SOX":
                flags.append("INDEX_ACTIVITY_FIELDS_NOT_VERIFIED")
            if meta["symbol"] == "US.QQQ" and meta["autype"] == 1:
                flags.append("FORWARD_ADJUSTED_PRICE_NOT_CERTIFIED_TOTAL_RETURN")
            r.update({"available_after_local_date": available.isoformat(),
                      "research_eligible": int(eligible), "quality_flags": "|".join(flags),
                      "source_archive_sha256": entry["archive_sha256"],
                      "source_member": entry["source_member"], "source_pointer": entry["source_pointer"],
                      "source_stage": entry["source_stage"]})
            merged.append(r)
            all_records.append({"series_id": sid, **r})
            provenance.append({"series_id": sid, "local_date": d,
                               **{k: r[k] for k in ["source_archive_sha256", "source_member",
                                                     "source_pointer", "source_stage"]}})
            if eligible:
                kept.append(r)
            else:
                exclusions.append({"series_id": sid, "local_date": d,
                                   "flags": flags, "source_member": entry["source_member"]})
            db.execute("INSERT INTO observations VALUES(" + ",".join(["?"] * 18) + ")",
                (sid, d, available.isoformat(), *[float(r[k]) for k in PRICE_FIELDS],
                 r["volume_raw"], r["turnover_raw"], r["time_key_raw"], r["time_zone_raw"],
                 int(eligible), json.dumps(flags), entry["archive_sha256"], entry["source_member"],
                 entry["source_pointer"], entry["source_stage"], json.dumps(entry["raw"])))
        fields = list(merged[0]) if merged else []
        for prefix, records in [("merged_", merged), ("research_", kept)]:
            with (out / "csv" / (prefix + filename)).open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(records)
        registry.append({"series_id": sid, "symbol": meta["symbol"], "frequency": meta["frequency"],
                         "autype": meta["autype"], "merged_rows": len(merged),
                         "research_rows": len(kept), "excluded_rows": len(merged) - len(kept),
                         "first_merged_date": min(entries), "last_merged_date": max(entries),
                         "first_research_date": kept[0]["local_date"] if kept else None,
                         "last_research_date": kept[-1]["local_date"] if kept else None})
    for d in missing:
        db.execute("INSERT INTO unresolved_dates VALUES(?,?,?,?,?)",
                   ("US..SOX", "DAY", d, "NO_DATA_ON_PROVIDER_RECHECK",
                    "Missing relative to observed QQQ dates; exact recheck returned empty; no price inserted."))
    db.commit()
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()
    def write_json(name, value):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    write_json("merge_policy.json", policy)
    write_json("series_registry.json", registry)
    write_json("audit/patch_additions.json", additions)
    write_json("audit/anchor_dates.json", {"dates": sorted(anchors), "price_fields_exact": True})
    write_json("audit/original_reconciliation.json", reconciliation)
    write_json("audit/excluded_observations.json", exclusions)
    write_json("audit/unresolved_dates.json", missing)
    with (out / "audit" / "row_provenance.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(provenance[0]))
        writer.writeheader()
        writer.writerows(provenance)
    receipt = {"dataset_version": "0.1", "status": "MERGED_WITH_EXPLICIT_UNRESOLVED_LIMITS",
               "original_sha256": EXPECTED_ORIGINAL, "patch_sha256": EXPECTED_PATCH,
               "manifest_files_verified": {"original": original_hashes, "patch": patch_hashes},
               "primary_windows_merged": len(additions),
               "new_rows": sum(x["rows_added"] for x in additions),
               "anchors_checked": len(anchors), "unresolved_candidate_dates": len(missing),
               "original_unchanged": sha(original_path) == EXPECTED_ORIGINAL,
               "patch_unchanged": sha(patch_path) == EXPECTED_PATCH,
               "observations_total": len(all_records), "excluded_observations": len(exclusions),
               "daily_or_weekly_missing_prices_inserted": 0,
               "provider_calendar_used_as_sole_filter": False,
               "no_point_in_time_data_claim": True, "no_alpha_claim": True,
               "code_sha256": sha(Path(__file__))}
    write_json("merge_receipt.json", receipt)
    print(json.dumps({"receipt": receipt, "registry": registry}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("merge_policy.json"))
    args = parser.parse_args()
    build(args.original, args.patch, args.out, args.policy)
