"""Run the descriptive census on a fixed daily price snapshot.

  python -m scripts.market_stress_v1.run_census SNAPSHOT.sqlite EXPECTED_SHA256 PRIVATE_OUT_DIR SUMMARY.json

Row-level output (episodes, every scheduled landmark, labels, exclusions) is written to PRIVATE_OUT_DIR and stays
there; SUMMARY.json holds counts, ranges and hashes only. The snapshot is opened read-only and refused when its
hash is not the expected one. No model is fitted."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import census as cs

SERIES = {"US.QQQ|DAY|a1": {"role": "primary_us", "note": "ETF, supplier forward-adjusted; total return NOT certified"},
          "US..SOX|DAY|a0": {"role": "primary_us", "note": "index, not directly tradable; registered holes cut the series"},
          "SH.000001|DAY|a0": {"role": "exploratory_local", "note": "index, not directly tradable"},
          "SH.000688|DAY|a0": {"role": "exploratory_local", "note": "index, live from 2020-07-23; earlier rows are pre-publication and excluded"}}
BLOCKED = {"SPY_or_SPX": "not admitted: needs its own quotes and calendar acceptance", "KOSPI": "not admitted: needs its own quotes and calendar acceptance"}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_csv(path: Path, rows: list[dict]) -> str:
    cols = sorted({k for r in rows for k in r}) if rows else ["empty"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n"); w.writeheader(); w.writerows(rows)
    return sha256_file(path)


def main(snapshot: Path, expected: str, out: Path, summary_path: Path, known_through: str = "2026-11-17") -> dict:
    got = sha256_file(snapshot)
    if got != expected:
        raise SystemExit(f"snapshot hash {got[:12]} is not the expected {expected[:12]}: register it as a new input version instead of overwriting")
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)
    summary = {"schema": "stress_census_summary_v1", "protocol_id": cs.PROTOCOL_ID, "evidence_class": "RETROSPECTIVE_DESCRIPTIVE (definitions written after the history was seen; not pre-registered)",
               "run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "input": {"snapshot_sha256": got, "opened": "read-only", "admitted_through": known_through},
               "parameters": {"warmup_quotes": cs.WARMUP, "entry": cs.ENTRY, "recovery": cs.RECOVERY, "recovery_run": cs.RECOVERY_RUN, "landmark_spacing": cs.SPACING, "horizon": cs.HORIZON,
                              "down": cs.DOWN, "up": cs.UP, "weakness_min_days": cs.WEAK_MIN_DAYS, "weakness_terminal": cs.WEAK_TERMINAL},
               "calendar": "the quotes present in the supplier's series; NOT checked against an exchange calendar; registered holes are the only breaks",
               "series": {}, "blocked_markets": BLOCKED, "recent_window": {"2026-11-18..2026-11-20": "MISSING (no authorised export on this machine; not fetched)", "2026-11-20": "DIAGNOSTIC_ONLY when it arrives; already seen"},
               "models_fitted": 0, "private_files": {}}
    all_e, all_m, all_l, all_x = [], [], [], []
    for sid, meta in SERIES.items():
        symbol = sid.split("|")[0]
        rows = con.execute("select local_date, open, close, research_eligible from observations where series_id=? order by local_date", (sid,)).fetchall()
        holes = {r[0] for r in con.execute("select local_date from unresolved_dates where symbol=? and frequency='DAY'", (symbol,))}
        quotes = [cs.Quote(d, o, c) for d, o, c, ok in rows if ok]
        dropped = [{"series_id": sid, "date": d, "reason": "NOT_RESEARCH_ELIGIBLE_IN_SNAPSHOT"} for d, _o, _c, ok in rows if not ok]
        c = cs.enumerate_states(sid, quotes, holes); labels = cs.label_landmarks(sid, quotes, holes, c.landmarks)
        sens = {f"h{h}_b{int(round(b * 100))}": cs.summarize(c, cs.label_landmarks(sid, quotes, holes, c.landmarks, horizon=h, down=1 - b, up=1 + b))["labels_on_included_landmarks"]["path"]
                for h in (20, 40, 60) for b in (0.05, 0.10, 0.15) if (h, b) != (40, 0.10)}
        s = cs.summarize(c, labels); s.update(role=meta["role"], note=meta["note"], rows_in_snapshot=len(rows), rows_not_research_eligible=len(dropped),
                                              first_quote=quotes[0].date if quotes else None, last_quote=quotes[-1].date if quotes else None,
                                              sensitivity_path_labels_pre_listed_not_selected=sens)
        summary["series"][sid] = s
        all_e += c.episodes; all_m += c.landmarks; all_l += labels; all_x += c.exclusions + dropped
    con.close()
    for name, rows in (("episodes.csv", all_e), ("landmarks_all_scheduled.csv", all_m), ("future_labels.csv", all_l), ("exclusions.csv", all_x)):
        summary["private_files"][name] = {"rows": len(rows), "sha256": write_csv(out / name, rows)}
    summary["totals"] = {"episodes": len(all_e), "landmarks_scheduled": len(all_m), "landmarks_included": sum(m["included"] for m in all_m), "labels": len(all_l), "exclusion_rows": len(all_x)}
    summary_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (out / "coverage.json").write_text(json.dumps({k: v["coverage"] for k, v in summary["series"].items()}, indent=1) + "\n", encoding="utf-8", newline="\n")
    return summary


if __name__ == "__main__":
    a = sys.argv[1:]
    s = main(Path(a[0]), a[1], Path(a[2]), Path(a[3]))
    print(json.dumps(s["totals"]))
