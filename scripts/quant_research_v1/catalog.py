"""Research catalog v2: dataset/factor metadata, engine contracts, eligibility, result grades, comparability and
historical imports. Additive to registry.py: legacy rows stay valid and read back as UNKNOWN.

Rules
  * Same (id, version) + same full canonical spec = idempotent; any difference (not only entry/params) is
    rejected with the list of differing fields. A changed definition needs a new version.
  * Capabilities, eligibility and permissions come from the backend registry, never from the browser.
  * An imported historical result is an EXTERNAL_IMPORT: it never becomes a platform run or a SUCCEEDED job.
  * Software success, mechanical check, data point-in-time eligibility, scientific screen and replication
    scope are stored separately (result_grades, append-only).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import sqlite3
from pathlib import Path

from . import registry

DATASET_V2_FIELDS = ("data_kind", "provider", "source_contract_version", "market", "timezone", "calendar_id", "calendar_version",
                     "adjustment_semantics", "dividend_treatment", "history_kind", "availability_evidence", "gap_policy",
                     "first_tradable_date", "source_manifest_sha256", "rights_scope")
FACTOR_V2_FIELDS = ("kind", "evaluation_mode", "input_data_kind", "signal_available_at_rule", "warmup_rule", "missing_rule",
                    "implementation_package_hash", "model_revision_if_any", "expected_direction", "candidate_search_family",
                    "definition", "engine")
COMPARE_REQUIRED_EQUAL = ("evaluation_mode", "execution_contract", "cost_contract", "cash_contract", "metric_contract",
                          "boundary_contract", "benchmark_id", "dataset_mask_hash")
EVALUATION_MODES = ("MARKET_TIME_SERIES", "EQUITY_CROSS_SECTION")
SCIENTIFIC_STATUSES = ("NOT_EVALUATED", "BLOCKED_INPUTS", "INSUFFICIENT_SAMPLE", "FIRST_SCREEN_NOT_PASSED", "FIRST_SCREEN_PASSED_REPLICATION_PENDING")

REPO = registry.REPO_ROOT
PHASE5_DIR = "research/market_sentiment/phase5_strategy_v1"
PHASE5_FREEZE_COMMIT = "1cfe1acb9e82c5c22f2ec1122c41cc2dd1209953"

ENGINES = {
    ("legacy_mom20_v1", "1"): {
        "title": "mom20 旧引擎（隔夜/日内拆分，收盘估值）", "evaluation_modes": ["MARKET_TIME_SERIES"], "dataset_kinds": ["OHLC_DAILY_SINGLE_CSV"],
        "execution_contract": "CLOSE_T_NEXT_OPEN_SPLIT_OVERNIGHT_INTRADAY|MARK_CLOSE_EXECUTION_DAY", "cost_contract": "POSITION_CHANGE_MULTIPLICATIVE",
        "cash_contract": "CASH_RETURN_0", "metric_contract": "CAGR_TRADING_BARS_252", "boundary_contract": "CONTINUOUS_LEDGER_SEGMENT_REPORT",
        "requires_python_modules": [], "runtime": "app image (built-in)"},
    ("phase5_o2o_v1", "1"): {
        "title": "phase5 开盘到开盘引擎（冻结程序）", "evaluation_modes": ["MARKET_TIME_SERIES"], "dataset_kinds": ["OHLC_DAILY_BUNDLE_SQLITE"],
        "execution_contract": "CLOSE_T_NEXT_OPEN|MARK_NEXT_OPEN", "cost_contract": "POST_FEE_TARGET_CASH_UNITS",
        "cash_contract": "CASH_RETURN_0", "metric_contract": "CAGR_ACTUAL_CALENDAR_ELAPSED", "boundary_contract": "RESET_EVALUATION_PERIOD_EPISODE_CLOSE",
        "requires_python_modules": ["numpy", "pandas", "scipy"], "runtime": "numeric job image (quant-research-numeric)",
        "frozen_program": PHASE5_DIR, "freeze_commit": PHASE5_FREEZE_COMMIT},
}


def canonical(o) -> str:
    return registry.canonical(o)


def sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def engine_contracts(engine_id: str, engine_version: str) -> dict:
    e = ENGINES[(engine_id, engine_version)]
    return {k: e[k] for k in ("execution_contract", "cost_contract", "cash_contract", "metric_contract", "boundary_contract")}


# ---------------------------------------------------------------- datasets
def register_dataset_meta(db, dataset_id: str, dataset_version: str, meta: dict) -> str:
    missing = [k for k in DATASET_V2_FIELDS if k not in meta]
    if missing:
        raise ValueError(f"DATASET_META_INCOMPLETE: {missing}")
    if not db.execute("SELECT 1 FROM datasets WHERE dataset_id=? AND dataset_version=?", (dataset_id, dataset_version)).fetchone():
        raise LookupError("dataset not registered")
    text = canonical(meta); h = sha_text(text)
    row = db.execute("SELECT meta_json, meta_sha256 FROM dataset_meta WHERE dataset_id=? AND dataset_version=?", (dataset_id, dataset_version)).fetchone()
    if row:
        if row[1] == h:
            return "UNCHANGED"
        old = json.loads(row[0])
        diff = sorted(k for k in set(old) | set(meta) if old.get(k) != meta.get(k))
        raise ValueError(f"DATASET_META_CONFLICT: {dataset_id}@{dataset_version} already registered with different {diff}; register a new version")
    db.execute("INSERT INTO dataset_meta VALUES(?,?,?,?,?)", (dataset_id, dataset_version, text, h, registry.utcnow())); db.commit()
    return "REGISTERED"


def register_bundle_snapshot(db, src_sqlite: Path, dataset_id: str, dataset_version: str, title: str, meta: dict,
                             expected_sha256: str | None = None) -> dict:
    """Freeze a multi-series research SQLite (bytes-identical copy) under datasets/<id>/<version>/ and register it."""
    src_sha = sha_file(src_sqlite)
    if expected_sha256 and src_sha != expected_sha256:
        raise ValueError("SNAPSHOT_SOURCE_HASH_MISMATCH")
    rel = Path("datasets") / dataset_id / dataset_version
    out = registry.root() / rel
    out.mkdir(parents=True, exist_ok=False)
    dst = out / "market_research.sqlite"
    shutil.copyfile(src_sqlite, dst)
    if sha_file(dst) != src_sha:
        raise ValueError("SNAPSHOT_COPY_HASH_MISMATCH")
    con = sqlite3.connect(f"file:{dst.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        series = [{"symbol": s, "autype": a, "rows": n, "start": lo, "end": hi} for s, a, n, lo, hi in
                  con.execute("SELECT symbol, autype, COUNT(*), MIN(local_date), MAX(local_date) FROM research_daily GROUP BY 1, 2 ORDER BY 1, 2")]
        gaps = [list(r) for r in con.execute("SELECT symbol, frequency, local_date FROM unresolved_dates ORDER BY 1, 3")]
    finally:
        con.close()
    manifest = {"schema": "quant_research_dataset_bundle_v1", "dataset_id": dataset_id, "dataset_version": dataset_version, "title": title,
                "symbol": ",".join(sorted({s["symbol"] for s in series})), "frequency": "DAY",
                "adjustment": "per series (see meta.adjustment_semantics)", "start_date": min(s["start"] for s in series),
                "end_date": max(s["end"] for s in series), "rows": sum(s["rows"] for s in series), "content_sha256": src_sha,
                "files": ["market_research.sqlite", "manifest.json"], "series": series, "unresolved_dates": gaps,
                "audit_status": "EXTERNAL_SNAPSHOT_HASH_BOUND", "point_in_time": False}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    registry.register_dataset(db, manifest, rel.as_posix())
    register_dataset_meta(db, dataset_id, dataset_version, meta)
    return manifest


# ---------------------------------------------------------------- factors / strategies
def register_spec(db, spec: dict) -> str:
    """Full canonical v2 spec. Same id/version + same spec = UNCHANGED; any difference = conflict."""
    for k in ("factor_id", "factor_version", "title", "entry", "params", "input_requirements", "output_definition", "missing_rule") + FACTOR_V2_FIELDS:
        if k not in spec:
            raise ValueError(f"FACTOR_SPEC_INCOMPLETE: {k}")
    if spec["evaluation_mode"] not in EVALUATION_MODES:
        raise ValueError(f"unknown evaluation_mode {spec['evaluation_mode']}")
    key = (spec["factor_id"], spec["factor_version"])
    body = {k: v for k, v in spec.items() if k != "fn"}
    text = canonical(body); h = sha_text(text)
    row = db.execute("SELECT spec_json, spec_sha256 FROM factor_specs WHERE factor_id=? AND factor_version=?", key).fetchone()
    if row:
        if row[1] == h:
            return "UNCHANGED"
        old = json.loads(row[0])
        diff = sorted(k for k in set(old) | set(body) if old.get(k) != body.get(k))
        raise ValueError(f"FACTOR_SPEC_CONFLICT: {key[0]}@{key[1]} already registered with different {diff}; register a new version")
    base = db.execute("SELECT entry, params_json, impl_sha256 FROM factors WHERE factor_id=? AND factor_version=?", key).fetchone()
    if base:
        if (base[0], base[1]) != (spec["entry"], canonical(spec["params"])):
            raise ValueError("FACTOR_SPEC_CONFLICT: base registry row differs (entry/params)")
        if base[2] and spec.get("implementation_package_hash") not in (base[2], "UNKNOWN") and spec["kind"] == "FACTOR_LEGACY":
            raise ValueError("FACTOR_SPEC_CONFLICT: implementation fingerprint differs")
    else:
        db.execute("""INSERT INTO factors(factor_id,factor_version,title,entry,params_json,input_requirements_json,output_definition,
                      missing_rule,registered_at,impl_sha256) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (*key, spec["title"], spec["entry"], canonical(spec["params"]), canonical(spec["input_requirements"]),
                    spec["output_definition"], spec["missing_rule"], registry.utcnow(), spec["implementation_package_hash"]))
    db.execute("INSERT INTO factor_specs VALUES(?,?,?,?,?)", (*key, text, h, registry.utcnow())); db.commit()
    return "REGISTERED"


def phase5_files(repo: Path = REPO) -> dict:
    d = repo / PHASE5_DIR
    return {n: sha_file(d / n) for n in ("run.py", "check.py", "config.json", "PROTOCOL_zh.md", "environment.json", "requirements.txt")}


def builtin_specs(repo: Path = REPO) -> list[dict]:
    from .engine import FACTORS, impl_fingerprint
    m = FACTORS[("mom20", "1")]
    out = [{**{k: m[k] for k in ("factor_id", "factor_version", "title", "entry", "params", "input_requirements", "output_definition", "missing_rule")},
            "kind": "FACTOR_LEGACY", "evaluation_mode": "MARKET_TIME_SERIES", "input_data_kind": "OHLC_DAILY_SINGLE_CSV",
            "signal_available_at_rule": "close of t, executed at next open", "warmup_rule": "20 rows", "implementation_package_hash": impl_fingerprint(m["fn"]),
            "model_revision_if_any": "NONE", "expected_direction": "POSITIVE (long when > 0)", "candidate_search_family": "single pre-registered engineering baseline",
            "definition": m["output_definition"], "engine": "legacy_mom20_v1@1"}]
    f = phase5_files(repo); cfg = json.loads((repo / PHASE5_DIR / "config.json").read_text(encoding="utf-8"))
    pkg = sha_text(canonical(f))
    common = {"evaluation_mode": "MARKET_TIME_SERIES", "input_data_kind": "OHLC_DAILY_BUNDLE_SQLITE", "signal_available_at_rule": "close of t; executed at next eligible open t+1; marked at open t+2",
              "warmup_rule": f"{cfg['warmup_closes']} closes per gap-free episode", "implementation_package_hash": pkg, "model_revision_if_any": "NONE",
              "candidate_search_family": "phase5_strategy_v1 (3 price candidates x 4 markets = 12 primary tests, Holm)", "engine": "phase5_o2o_v1@1",
              "entry": f"{PHASE5_DIR}/run.py", "missing_rule": "no fill; unresolved dates split episodes and reset warm-up"}
    inputs = {"data_kind": "OHLC_DAILY_BUNDLE_SQLITE", "series": cfg["series"], "fields": ["open", "close"]}
    out.append({**common, "factor_id": "phase5_strategy_v1", "factor_version": "1", "title": "phase5 三项价格规则（P1/P2/P3 + 基准）", "kind": "STRATEGY_FAMILY",
                "params": cfg, "input_requirements": inputs, "output_definition": "per market x strategy x cost x period ledgers, metrics, 12 primary contrasts vs MA200",
                "expected_direction": "POSITIVE increment vs MA200 (one-sided)", "definition": "see PROTOCOL_zh.md (frozen)"})
    for sid, title, defin, key in (("P1_TREND", "P1 趋势确认", "60-bar risk-normalised momentum > 0.5 and C > MA120 enter; score <= 0 or C < MA60 exit", "trend"),
                                   ("P2_RECOVERY", "P2 恐慌修复", "stress (dd252 <= -15% and r20 <= -8%) within 20 bars, r5 > 0 and C > MA20 enter; 40-bar max hold, 8% close trailing stop", "recovery"),
                                   ("P3_RISK_BUDGET", "P3 波动预算", "P1 direction x min(1, 10% / (vol20*sqrt(252)))", "risk_budget")):
        out.append({**common, "factor_id": f"phase5_{sid.lower()}", "factor_version": "1", "title": title, "kind": "STRATEGY_MEMBER",
                    "params": {"member_of": "phase5_strategy_v1@1", "strategy": sid, **({key: cfg[key]} if key in cfg else {}), **({"trend": cfg["trend"]} if sid == "P3_RISK_BUDGET" else {})},
                    "input_requirements": inputs, "output_definition": f"target weight {sid} (0..1, long/cash)", "expected_direction": "POSITIVE increment vs MA200", "definition": defin})
    for tid, title, kind_in, miss in (("T1", "T1 新闻情绪均值 x 时间波动", "NEWS_PANEL_HISTORICAL", "historical article panel with availability time, dedup, company mapping and scoring version"),
                                      ("T2", "T2 利空承受力", "NEWS_EVENTS_WITH_EXPECTED_RESPONSE", "time-stamped negative events and a training-window expected-response model")):
        out.append({"factor_id": f"phase5_{tid.lower()}", "factor_version": "1", "title": title, "kind": "TEXT_CANDIDATE", "entry": "NOT_IMPLEMENTED",
                    "params": {"member_of": "phase5_strategy_v1@1", "candidate": tid}, "input_requirements": {"data_kind": kind_in, "missing": miss},
                    "output_definition": "not computed", "missing_rule": "blocked without inputs; never replaced by price variables",
                    "evaluation_mode": "MARKET_TIME_SERIES", "input_data_kind": kind_in, "signal_available_at_rule": "article/event availability time (required, not yet available)",
                    "warmup_rule": "30 trading days (T1)" if tid == "T1" else "event-dependent", "implementation_package_hash": "NONE", "model_revision_if_any": "UNKNOWN (scoring model not frozen)",
                    "expected_direction": "UNKNOWN", "candidate_search_family": "phase5_strategy_v1 text candidates", "definition": title, "engine": "NONE"})
    return out


def register_builtin_specs(db, repo: Path = REPO) -> dict:
    return {f"{s['factor_id']}@{s['factor_version']}": register_spec(db, s) for s in builtin_specs(repo)}


# ---------------------------------------------------------------- eligibility (backend decides)
def capabilities(db) -> dict:
    caps = {}
    for r in db.execute("SELECT image_id, engine_id, engine_version, detail_json, checked_at FROM worker_capabilities"):
        d = json.loads(r[3])
        if d.get("ok"):
            caps.setdefault((r[1], r[2]), []).append({"image_id": r[0], "checked_at": r[4], **d})
    caps.setdefault(("legacy_mom20_v1", "1"), [{"image_id": "app image", "ok": True, "note": "built into the app image worker"}])
    return caps


def eligibility(db, dataset: tuple | None, factor: tuple, engine: tuple | None = None) -> dict:
    reasons = []
    row = db.execute("SELECT spec_json FROM factor_specs WHERE factor_id=? AND factor_version=?", factor).fetchone()
    if not row:
        return {"status": "BLOCKED_UNKNOWN_SPEC", "reasons": ["factor/strategy has no registered v2 spec"]}
    spec = json.loads(row[0])
    if spec["kind"] == "TEXT_CANDIDATE":
        reasons.append(f"BLOCKED_INPUTS: needs {spec['input_data_kind']} ({spec['input_requirements'].get('missing')})")
    eng = tuple(spec["engine"].split("@")) if spec["engine"] != "NONE" else None
    engine = engine or eng
    if engine is None:
        reasons.append("NO_ENGINE: no executable engine for this entry")
    elif engine not in ENGINES:
        reasons.append(f"UNKNOWN_ENGINE {engine}")
    elif engine not in capabilities(db):
        reasons.append(f"ENGINE_NOT_BUILT: no worker image has registered a verified capability for {engine[0]}@{engine[1]}")
    if dataset:
        m = db.execute("SELECT meta_json FROM dataset_meta WHERE dataset_id=? AND dataset_version=?", dataset).fetchone()
        meta = json.loads(m[0]) if m else {k: "UNKNOWN" for k in DATASET_V2_FIELDS}
        if spec["evaluation_mode"] == "EQUITY_CROSS_SECTION" and meta["data_kind"] != "EQUITY_UNIVERSE_PANEL":
            reasons.append("WRONG_EVALUATION_MODE: cross-section needs a single-market historical equity universe with membership/industry/size/status evidence")
        if spec["input_data_kind"] != meta["data_kind"]:
            reasons.append(f"INPUT_KIND_MISMATCH: needs {spec['input_data_kind']}, dataset is {meta['data_kind']}")
        if "POINT_IN_TIME" in spec.get("signal_available_at_rule", "") and meta["history_kind"] != "POINT_IN_TIME":
            reasons.append("NOT_POINT_IN_TIME: dataset history is not point-in-time with availability evidence")
        rights = meta["rights_scope"] if isinstance(meta["rights_scope"], dict) else {}
        if rights.get("research_analysis") == "NOT_GRANTED":
            reasons.append("RIGHTS: research use not granted for this dataset")
        if engine in ENGINES and meta["data_kind"] not in ENGINES[engine]["dataset_kinds"]:
            reasons.append(f"ENGINE_DATASET_KIND: {engine[0]} reads {ENGINES[engine]['dataset_kinds']}")
    blocked_inputs = any(r.startswith(("BLOCKED_INPUTS", "INPUT_KIND", "WRONG_EVALUATION", "NOT_POINT")) for r in reasons)
    status = "ELIGIBLE" if not reasons else ("BLOCKED_INPUTS" if blocked_inputs else "BLOCKED")
    return {"status": status, "reasons": reasons, "spec_kind": spec["kind"], "engine": "@".join(engine) if engine else None}


# ---------------------------------------------------------------- runs v2
def dataset_mask_hash(content_sha256: str, sample_mask: dict) -> str:
    return sha_text(canonical({"dataset_content_sha256": content_sha256, "sample_mask": sample_mask}))


def create_run_v2(db, dataset: tuple, factor: tuple, protocol: dict, engine: tuple, requested_by: str | None = None,
                  attempt_of: str | None = None) -> str:
    el = eligibility(db, dataset, factor, engine)
    if el["status"] != "ELIGIBLE":
        raise ValueError(f"NOT_ELIGIBLE: {el['reasons']}")
    ds = db.execute("SELECT content_sha256 FROM datasets WHERE dataset_id=? AND dataset_version=?", dataset).fetchone()
    run_id = registry.create_run(db, dataset, factor, protocol, requested_by=requested_by, attempt_of=attempt_of)
    contracts = {**engine_contracts(*engine), "benchmark_id": protocol.get("benchmark_id", "UNKNOWN"),
                 "evaluation_mode": protocol.get("evaluation_mode", "MARKET_TIME_SERIES")}
    mask = dataset_mask_hash(ds[0], protocol.get("sample_mask", {}))
    db.execute("""UPDATE runs SET engine_id=?, engine_version=?, evaluation_mode=?, benchmark_id=?, contracts_json=?, dataset_mask_hash=?
                  WHERE run_id=?""", (*engine, contracts["evaluation_mode"], contracts["benchmark_id"], canonical(contracts), mask, run_id))
    db.commit()
    return run_id


def run_contracts(row) -> dict:
    """Contracts of a run; legacy rows derive them from the legacy engine and their frozen config."""
    keys = row.keys()
    if "contracts_json" in keys and row["contracts_json"]:
        c = json.loads(row["contracts_json"]); c["dataset_mask_hash"] = row["dataset_mask_hash"]; return c
    cfg = json.loads(row["config_json"]) if "config_json" in keys and row["config_json"] else {}
    c = {**engine_contracts(*registry.LEGACY_ENGINE), "evaluation_mode": "MARKET_TIME_SERIES",
         "benchmark_id": "BUY_HOLD_SAME_SERIES_LEGACY", "legacy_derived": True}
    c["dataset_mask_hash"] = sha_text(canonical({"dataset": [row["dataset_id"], row["dataset_version"]], "segments": cfg.get("segments")}))
    return c


def compare(db, run_a: str, run_b: str) -> dict:
    ra = db.execute("SELECT * FROM runs WHERE run_id=?", (run_a,)).fetchone(); rb = db.execute("SELECT * FROM runs WHERE run_id=?", (run_b,)).fetchone()
    if ra is None or rb is None:
        raise LookupError("run not found")
    ca, cb = run_contracts(ra), run_contracts(rb)
    diffs = {k: {"a": ca.get(k), "b": cb.get(k)} for k in COMPARE_REQUIRED_EQUAL if ca.get(k) != cb.get(k)}
    same_snapshot = (ra["dataset_id"], ra["dataset_version"]) == (rb["dataset_id"], rb["dataset_version"])
    if diffs:
        return {"result": "NOT_COMPARABLE", "differences": diffs, "side_by_side_allowed": True, "ranking": None}
    return {"result": "COMPARABLE", "differences": {}, "same_snapshot_and_mask": same_snapshot, "ranking_allowed": same_snapshot}


# ---------------------------------------------------------------- grades
def record_grade(db, subject_kind: str, subject_id: str, *, software_status: str, mechanical_check: str, data_pit_eligibility: str,
                 scientific_status: str, replication_scope: str, evidence: dict) -> int:
    if scientific_status.split(":")[0] not in SCIENTIFIC_STATUSES:
        raise ValueError(f"unknown scientific status {scientific_status}")
    cur = db.execute("""INSERT INTO result_grades(subject_kind,subject_id,software_status,mechanical_check,data_pit_eligibility,
                        scientific_status,replication_scope,evidence_json,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                     (subject_kind, subject_id, software_status, mechanical_check, data_pit_eligibility, scientific_status, replication_scope,
                      canonical(evidence), registry.utcnow()))
    db.commit(); return cur.lastrowid


def latest_grades(rows: list[dict]) -> dict:
    out = {}
    for r in rows:
        out[(r["subject_kind"], r["subject_id"])] = {k: r[k] for k in ("software_status", "mechanical_check", "data_pit_eligibility", "scientific_status",
                                                                     "replication_scope", "recorded_at", "grade_id")} | {"evidence": json.loads(r["evidence_json"])}
    return out


# ---------------------------------------------------------------- artifacts
ARTIFACT_KINDS = {".json": "json", ".csv": "csv", ".gz": "csv.gz", ".log": "log", ".txt": "text"}


def register_artifacts(db, run_id: str, run_dir: Path, names: list[str]) -> list[dict]:
    out = []
    for n in names:
        f = run_dir / n
        if not f.is_file():
            raise FileNotFoundError(f"ARTIFACT_MISSING: {n}")
        rec = {"name": n, "kind": ARTIFACT_KINDS.get(f.suffix, "file"), "rel_path": f"runs/{run_id}/{n}", "bytes": f.stat().st_size, "sha256": sha_file(f)}
        db.execute("INSERT INTO artifacts(run_id,name,kind,rel_path,bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?)",
                   (run_id, n, rec["kind"], rec["rel_path"], rec["bytes"], rec["sha256"], registry.utcnow()))
        out.append(rec)
    db.commit(); return out


# ---------------------------------------------------------------- historical import (never a platform run)
def phase5_import_record(repo: Path = REPO) -> dict:
    d = repo / PHASE5_DIR
    man = json.loads((d / "results/run_manifest.json").read_text(encoding="utf-8"))
    contrasts = list(csv.DictReader(io.StringIO((d / "results/contrasts.csv").read_text(encoding="utf-8"))))
    metrics = list(csv.DictReader(io.StringIO((d / "results/metrics.csv").read_text(encoding="utf-8"))))
    prim = [c for c in contrasts if c["primary_test"] == "True"]
    close = json.loads((d / "claude_validation_record/CLOSEOUT.json").read_text(encoding="utf-8"))
    fl = lambda x: None if x in ("", None) else float(x)
    headline = [{k: (fl(m[k]) if k in ("cagr", "max_drawdown", "mean_exposure", "cost_fraction_sum", "terminal_wealth", "descriptive_sharpe_sqrt252", "annualized_turnover") else m[k])
                 for k in ("symbol", "strategy", "cost_bps", "cagr", "max_drawdown", "mean_exposure", "cost_fraction_sum", "terminal_wealth", "descriptive_sharpe_sqrt252",
                           "annualized_turnover", "first_exec", "last_mark", "intervals")} for m in metrics if m["period"] == "primary" and m["status"] == "EXECUTED_PRICE_ONLY"]
    return {"schema": "external_import_v1", "kind": "EXTERNAL_IMPORT", "not_a_platform_run": True,
            "study_id": "phase5_strategy_v1", "freeze_commit": man["freeze_commit"], "freeze_commit_time_utc": close["freeze_commit_time_utc"],
            "original_run": {"started_at_utc": man["started_at_utc"], "finished_at_utc": man["finished_at_utc"], "python": man["python"],
                             "numpy": man["numpy"], "pandas": man["pandas"], "mode": man["mode"]},
            "inputs": {"input_db_sha256": man["input_db_sha256"], "data_end": man["data_end"], "input_vintage": man["input_vintage"]},
            "code": {"config_sha256": man["config_sha256"], "code_sha256": man["code_sha256"], "protocol_sha256": man["protocol_sha256"]},
            "results": {"outputs_sha256": man["outputs_sha256"], "summary_rows": man["simulation_summary_rows"], "private_ledger_files": man["private_ledger_files"],
                        "primary_family_size": man["primary_family_size"], "primary_pass_count": man["primary_pass_count"],
                        "primary_tests": [{k: c[k] for k in ("symbol", "strategy", "n", "mean_daily_net_delta", "ci_low_daily", "ci_high_daily", "p_one_sided_positive",
                                                             "holm_p", "mean_daily_delta_cost25", "first_screen_return_increment_pass")} for c in prim],
                        "primary_metrics": headline},
            "errata": [], "validation_layers": close["layers"], "text_candidates": close["text_candidates"],
            "evidence_files": {n: sha_file(d / n) for n in ("results/run_manifest.json", "results/contrasts.csv", "results/metrics.csv", "AUDIT_FINAL.json",
                                                          "claude_validation_record/CLOSEOUT.json", "claude_validation_record/VALIDATION.json")}}


def import_phase5(db, repo: Path = REPO) -> str:
    rec = phase5_import_record(repo); text = canonical(rec); h = sha_text(text)
    import_id = f"ext-phase5_strategy_v1-{rec['original_run']['started_at_utc'][:19].replace(':', '').replace('-', '')}Z"
    row = db.execute("SELECT record_sha256 FROM external_runs WHERE import_id=?", (import_id,)).fetchone()
    if row:
        if row[0] == h:
            return import_id
        raise ValueError("EXTERNAL_IMPORT_CONFLICT: same import id with different content; import a corrected record under a new id")
    db.execute("INSERT INTO external_runs VALUES(?,?,?,?,?,?,?,?,?,?)", (import_id, "phase5_strategy_v1", "phase5 首轮（历史外部运行导入）", "phase5_o2o_v1", "1",
                                                                     "phase5_strategy_v1", "MARKET_TIME_SERIES", text, h, registry.utcnow()))
    db.commit()
    record_grade(db, "external", import_id, software_status="EXTERNAL_IMPORT_NOT_PLATFORM_EXECUTED",
                 mechanical_check="SAME_AUTHOR_PASS; CLAUDE_A_NUMERIC_TOLERANCE; CLAUDE_B_INDEPENDENT_RECONCILED (imported evidence)",
                 data_pit_eligibility="CURRENT_VINTAGE_NOT_POINT_IN_TIME", scientific_status="FIRST_SCREEN_NOT_PASSED",
                 replication_scope="A numeric tolerance; B independent implementation; C same-batch only, new-data BLOCKED_NO_NEW_DATA",
                 evidence={"record_sha256": h, "closeout": "research/market_sentiment/phase5_strategy_v1/claude_validation_record/CLOSEOUT.json"})
    return import_id


PHASE5_DATASET_META = {
    "data_kind": "OHLC_DAILY_BUNDLE_SQLITE", "provider": "moomoo OpenAPI (quote:read, owner account)", "source_contract_version": "moomoo history-kline REST 2026-11-18 download + patch",
    "market": {"US.QQQ": "US ETF", "US..SOX": "US index (non-investible proxy)", "SH.000001": "CN index (non-investible proxy)", "SH.000688": "CN index (non-investible proxy)"},
    "timezone": {"US": "America/New_York local dates", "SH": "Asia/Shanghai local dates"},
    "calendar_id": "VENDOR_SERIES_DATES", "calendar_version": "not exchange-certified; vendor calendar diagnostic only",
    "adjustment_semantics": {"US.QQQ": "a1 vendor forward-adjusted, not certified total return", "others": "a0 unadjusted index points"},
    "dividend_treatment": "UNCERTIFIED", "history_kind": "CURRENT_VINTAGE_BACKFILL", "availability_evidence": "UNKNOWN (not point-in-time)",
    "gap_policy": "split before first bar after each unresolved date; no fill; warm-up restarts", "first_tradable_date": {"SH.000688": "2020-07-23 publication; first executable 2021-08-04 after 252 closes"},
    "source_manifest_sha256": {"moomoo_history_20261118T214803Z_41e00a.zip": "85aa32f5749ceadd3b5eb28245b693063136552a81756edb302acf49b1eeb309",
                               "moomoo_patch_20261118T221352Z_fa2b41.zip": "08ff7106037db53ddc45dc86f93f5d7dcfd3df5e47471d679e02317f1db0f170"},
    "rights_scope": {"display": "UNKNOWN", "store": "PRIVATE_ONLY", "research_analysis": "OWNER_PRIVATE_RESEARCH_EXISTING_USE", "model_training": "UNKNOWN",
                     "redistribution": "NOT_PERMITTED", "evidence": "owner's own moomoo export used in phase5; no new grant asserted"},
}
