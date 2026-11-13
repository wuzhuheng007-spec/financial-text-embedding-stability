"""Run migrated synthetic before/after contract probes against active code."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from m5_bridge import evaluate as evaluator


INPUT_NAMES = (
    "config.json", "events.csv", "signals.csv", "targets.csv",
    "synthetic_calendar_sessions.csv", "synthetic_calendar_coverage.json",
    "fit_base.json", "synthetic_training_input_manifest.json",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads((args.inputs / "config.json").read_text(encoding="utf-8"))
    if config.get("schema_version") != 2 or config.get("synthetic") is not True:
        raise ValueError("After probes require migrated synthetic schema v2 inputs")
    args.work_dir.mkdir(parents=True, exist_ok=False)
    outcomes = []
    for case in ("control", "delayed_anchor", "missing_train_label_end", "unbound_market_stock"):
        folder = args.work_dir / case
        folder.mkdir()
        for name in INPUT_NAMES:
            shutil.copy2(args.inputs / name, folder / name)
        if case == "delayed_anchor":
            sessions = [row["session"] for row in read_csv(folder / "synthetic_calendar_sessions.csv")]
            events = read_csv(folder / "events.csv")
            targets = read_csv(folder / "targets.csv")
            for row in events:
                row["entry_session"] = sessions[sessions.index(row["entry_session"]) + 1]
                row["exit_session"] = sessions[sessions.index(row["exit_session"]) + 1]
            for row in targets:
                row["entry_session"] = sessions[sessions.index(row["entry_session"]) + 1]
                row["exit_session"] = sessions[sessions.index(row["exit_session"]) + 1]
                if row["target_type"] == "market_relative":
                    row["benchmark_entry_session"] = row["entry_session"]
                    row["benchmark_exit_session"] = row["exit_session"]
            write_csv(folder / "events.csv", events)
            write_csv(folder / "targets.csv", targets)
        elif case == "missing_train_label_end":
            fit_path = folder / "fit_base.json"
            fit = json.loads(fit_path.read_text(encoding="utf-8"))
            training_path = folder / "synthetic_training_input_manifest.json"
            training = json.loads(training_path.read_text(encoding="utf-8"))
            training.pop("training_rows")
            training_path.write_text(json.dumps(training), encoding="utf-8")
            fit["training_input_manifest_sha256"] = hashlib.sha256(training_path.read_bytes()).hexdigest()
            fit_path.write_text(json.dumps(fit), encoding="utf-8")
        elif case == "unbound_market_stock":
            targets = read_csv(folder / "targets.csv")
            for row in targets:
                if row["target_type"] == "market_relative" and row["status"] == "ok":
                    row["stock_target"] = "9"
                    row["target"] = str(9 - float(row["beta"]) * float(row["benchmark_target"]))
            write_csv(folder / "targets.csv", targets)
        try:
            summary = evaluator.evaluate(folder / "events.csv", folder / "signals.csv",
                                         folder / "targets.csv", folder / "config.json", folder / "run")
            outcomes.append({"case": case, "accepted": True, "scope": summary["scope"]})
        except evaluator.EvaluationError as exc:
            outcomes.append({"case": case, "accepted": False, "reason": str(exc)})
    first, second = "2020-01-01T23:30:00-08:00", "2020-01-02T00:00:00+00:00"
    outcomes.append({"case": "cross_timezone_training_order", "first": first, "second": second,
                     "implementation_not_after": evaluator._not_after(first, second, "probe"),
                     "actual_not_after": datetime.fromisoformat(first) <= datetime.fromisoformat(second)})
    expected = {"control": True, "delayed_anchor": False, "missing_train_label_end": False,
                "unbound_market_stock": False}
    passed = all(row["accepted"] == expected[row["case"]] for row in outcomes if "accepted" in row)
    passed = passed and outcomes[-1]["implementation_not_after"] == outcomes[-1]["actual_not_after"] is False
    report = {"status": "PASS" if passed else "FAIL", "synthetic_only": True,
              "input_schema_version": 2,
              "evaluator_sha256": hashlib.sha256(Path(evaluator.__file__).read_bytes()).hexdigest(),
              "cases": outcomes}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
