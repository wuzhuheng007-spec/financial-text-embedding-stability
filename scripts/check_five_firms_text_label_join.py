"""Offline five-filing text/price-label identity check; no model or scoring calls.

The command-line entry is restricted to the pinned real round-4 output. Tests
exercise the pure join with SYNTHETIC_TEST_ONLY files and never call main().
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
FIRMS = ("AAPL", "MSFT", "AMZN", "GOOGL", "META")
M5_SHA256 = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
ROUND4_CANDIDATE_SHA256 = "a307536980057c227d63fafb11b7b25b4615a2923455717616a7431f05d82388"
ROUND4_MANIFEST_SHA256 = "698942ddc8a7b395108a3ce2b72deab0f370ed3cd9de2bca827b693632182d5e"
ROUND4_METRICS_CONTRACT_SHA256 = "58a7b2f1389be691ef979f4fc4710f11d29027617c93f5a4eab580f73f3a6402"
SEC_INVENTORY_SHA256 = "dea6d376e1f613cf968de09d75347fe3b0ae9a3919260d4153c05493f34ac76b"
PILOT_FOLDER = Path("E:/five_firms_wrds_pilot_labels")
WRDS_LINK_FOLDER = Path("E:/five_firms_wrds_local_check")
WRDS_LINK_CODE_SHA256 = "06c9717c44bec5ea8236612f2bc1595290db19b84dc5dd8f1ce7d825bad4a8a6"
PILOT_CHECK_CODE_SHA256 = "b258a651a9bf139725ca657e39aa5e9c4f8aab47fd0b8b191f5f157aa95c6dee"
PILOT_MAKE_CODE_SHA256 = "d6932ebdf1ea9260f6f659576539ef83d724817982fff9c6988b6d0cecd27164"
PILOT_DAILY_NAME = re.compile(
    r"(?:u0i2mjrvf2dgfrml|five_firms_crsp_daily)(?: ?\([0-9]+\))?\.csv", re.I)
LABEL_HEADER = (
    "ticker", "filing_date", "pilot_decision_session", "fwd5_session",
    "fwd21_session", "price_return_5", "price_return_21", "scope",
    "daily_sha256", "permno_file_sha256",
)
LABEL_FIELDS = set(LABEL_HEADER)


class JoinError(ValueError):
    """A redacted failure identified only by input category and issue code."""

    def __init__(self, category: str, code: str):
        super().__init__(f"{category}:{code}")
        self.category = category
        self.code = code


def sha256(path: Path, category: str = "INPUT") -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise JoinError(category, "FILE_UNREADABLE") from exc
    return digest.hexdigest()


def _json(path: Path, category: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JoinError(category, "INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise JoinError(category, "INVALID_JSON_ROOT")
    return value


def _date(value: object, category: str) -> date:
    try:
        if not isinstance(value, str):
            raise ValueError("not a string")
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("not canonical")
        return parsed
    except ValueError as exc:
        raise JoinError(category, "INVALID_DATE") from exc


def _csv_rows(path: Path, required: set[str], category: str,
              *, exact_header: tuple[str, ...] | None = None) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames
            if (not headers or len(headers) != len(set(headers)) or
                    not required.issubset(headers) or any(not name for name in headers)):
                raise JoinError(category, "SCHEMA_MISMATCH")
            if exact_header is not None and tuple(headers) != exact_header:
                raise JoinError(category, "SCHEMA_MISMATCH")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise JoinError(category, "CSV_UNREADABLE") from exc
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise JoinError(category, "MALFORMED_ROW")
    return rows


def _digest(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64 and
            all(char in "0123456789abcdef" for char in value))


def verify_filing_map(mapping_path: Path, inventory_path: Path, candidate_path: Path) -> dict[str, dict]:
    mapping = _json(mapping_path, "SEC_MAP")
    inventory = _json(inventory_path, "SEC_METADATA")
    candidate = _json(candidate_path, "ROUND4_CANDIDATE")
    if (mapping.get("schema") != "five_firms_text_label_filing_map_v1" or
            mapping.get("scope") != "ENGINEERING_ONLY" or
            mapping.get("sec_inventory_sha256") != SEC_INVENTORY_SHA256 or
            sha256(inventory_path, "SEC_METADATA") != SEC_INVENTORY_SHA256 or
            mapping.get("round4_manifest_sha256") != ROUND4_MANIFEST_SHA256 or
            sha256(candidate_path, "ROUND4_CANDIDATE") != ROUND4_CANDIDATE_SHA256):
        raise JoinError("SEC_MAP", "SOURCE_BINDING_MISMATCH")
    filings = mapping.get("filings")
    if (not isinstance(filings, list) or len(filings) != len(FIRMS) or
            [row.get("ticker") for row in filings if isinstance(row, dict)] != list(FIRMS)):
        raise JoinError("SEC_MAP", "FIVE_FILING_SET_MISMATCH")
    input_spec = candidate.get("input")
    if (not isinstance(input_spec, dict) or
            input_spec.get("company_order") != list(FIRMS) or
            input_spec.get("form") != "10-K" or
            input_spec.get("section") != "ITEM_1_BUSINESS"):
        raise JoinError("ROUND4_CANDIDATE", "INPUT_SET_MISMATCH")
    inventory_firms = inventory.get("firms")
    if not isinstance(inventory_firms, dict):
        raise JoinError("SEC_METADATA", "FIRM_SET_MISSING")
    for row in filings:
        ticker = row["ticker"]
        _date(row.get("filing_date"), "SEC_MAP")
        if (not isinstance(row.get("cik"), str) or
                re.fullmatch(r"[0-9]{10}", row["cik"]) is None or
                not isinstance(row.get("accession"), str) or
                re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", row["accession"]) is None):
            raise JoinError("SEC_MAP", "INVALID_FILING_IDENTITY")
        if not _digest(row.get("item1_sha256")):
            raise JoinError("SEC_MAP", "INVALID_ITEM1_HASH")
        official_url = (f"https://www.sec.gov/Archives/edgar/data/{int(row['cik'])}/"
                        f"{row['accession'].replace('-', '')}/{row['accession']}-index.html")
        if row.get("sec_filing_url") != official_url:
            raise JoinError("SEC_MAP", "OFFICIAL_URL_MISMATCH")
        firm = inventory_firms.get(ticker)
        if not isinstance(firm, dict) or firm.get("cik") != row["cik"]:
            raise JoinError("SEC_METADATA", "CIK_MISMATCH")
        entries = firm.get("rows")
        if not isinstance(entries, list):
            raise JoinError("SEC_METADATA", "FILING_ROWS_MISSING")
        matches = [entry for entry in entries if isinstance(entry, dict) and
                   entry.get("form") == "10-K" and entry.get("filingDate") == row["filing_date"] and
                   entry.get("accessionNumber") == row["accession"] and entry.get("cik") == row["cik"]]
        if len(matches) != 1:
            raise JoinError("SEC_METADATA", "FILING_DATE_ACCESSION_MISMATCH")
    return {row["ticker"]: row for row in filings}


def verify_round4_vectors(manifest_path: Path, filings: dict[str, dict], root: Path,
                          *, expected_evidence_class: str = "REAL_MODEL") -> tuple[dict, dict]:
    """Validate only the five requested arrays, without constructing Encoder."""
    try:
        import numpy as np
    except ImportError as exc:
        raise JoinError("DEPENDENCY", "NUMPY_UNAVAILABLE") from exc
    if sha256(manifest_path, "ROUND4_MANIFEST") != ROUND4_MANIFEST_SHA256:
        raise JoinError("ROUND4_MANIFEST", "HASH_MISMATCH")
    sidecar = manifest_path.with_name("manifest.sha256")
    try:
        sidecar_digest = sidecar.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise JoinError("ROUND4_MANIFEST", "SIDECAR_UNREADABLE") from exc
    if sidecar_digest != ROUND4_MANIFEST_SHA256:
        raise JoinError("ROUND4_MANIFEST", "SIDECAR_MISMATCH")
    manifest = _json(manifest_path, "ROUND4_MANIFEST")
    if (manifest.get("kind") != "SMALL_CHUNK_PROBE_RUN" or manifest.get("status") != "COMPLETE" or
            manifest.get("evidence_class") != expected_evidence_class or
            manifest.get("failures") != [] or
            manifest.get("candidate_sha256") != ROUND4_CANDIDATE_SHA256 or
            manifest.get("metric_contract_sha256") != ROUND4_METRICS_CONTRACT_SHA256 or
            manifest.get("logical_jobs_completed") != 25 or
            manifest.get("company_vectors_completed") != 50):
        raise JoinError("ROUND4_MANIFEST", "RUN_IDENTITY_MISMATCH")
    versions = manifest.get("input_versions")
    if not isinstance(versions, list) or len(versions) != len(FIRMS):
        raise JoinError("ROUND4_MANIFEST", "INPUT_SET_MISMATCH")
    for ticker, version in zip(FIRMS, versions):
        expected = filings[ticker]
        if (not isinstance(version, dict) or version.get("ticker") != ticker or
                version.get("cik") != expected["cik"] or
                version.get("accession") != expected["accession"] or
                version.get("item1_sha256") != expected["item1_sha256"] or
                version.get("form") != "10-K" or version.get("section") != "ITEM_1_BUSINESS"):
            raise JoinError("ROUND4_MANIFEST", "INPUT_VERSION_MISMATCH")
    contract_path = root / "docs" / "SMALL_CHUNK_METRICS.md"
    if sha256(contract_path, "ROUND4_MANIFEST") != ROUND4_METRICS_CONTRACT_SHA256:
        raise JoinError("ROUND4_MANIFEST", "METRIC_CONTRACT_MISMATCH")
    config_path = root / "configs" / "smoke.yaml"
    try:
        from finembed.config import load_config
        from finembed.sensitivity_preflight import inspect_model_files
        config = load_config(config_path)
        model = inspect_model_files(config)
    except Exception as exc:
        raise JoinError("MODEL", "PINNED_FILES_MISMATCH") from exc
    identity = manifest.get("model_identity")
    if (not isinstance(identity, dict) or identity.get("model_id") != model["model_id"] or
            identity.get("revision") != model["revision"] or
            identity.get("tokenizer_revision") != model["tokenizer_revision"] or
            identity.get("provider") != "CPUExecutionProvider" or
            config.provider != "CPUExecutionProvider" or config.body_tokens != 128 or
            config.normalize_chunks is not True or config.normalize_documents is not True or
            identity.get("reference_config_sha256") != config.source_sha256 or
            identity.get("model_file_sha256") !=
            {name: item["sha256"] for name, item in model["files"].items()}):
        raise JoinError("MODEL", "MANIFEST_IDENTITY_MISMATCH")
    jobs = manifest.get("jobs")
    if (not isinstance(jobs, list) or len(jobs) != 25 or
            any(not isinstance(job, dict) for job in jobs)):
        raise JoinError("ROUND4_MANIFEST", "JOB_GRID_MISMATCH")
    if len({(job.get("ticker"), job.get("body_tokens_per_chunk")) for job in jobs}) != 25:
        raise JoinError("ROUND4_MANIFEST", "JOB_GRID_MISMATCH")
    vectors = {}
    for ticker in FIRMS:
        jobs_128 = [job for job in jobs if job.get("ticker") == ticker and
                    job.get("body_tokens_per_chunk") == 128]
        if len(jobs_128) != 1 or jobs_128[0].get("status") != "COMPLETE":
            raise JoinError("ROUND4_MANIFEST", "REFERENCE_JOB_MISSING")
        job = jobs_128[0]
        if job.get("accession") != filings[ticker]["accession"] or job.get("item1_sha256") != filings[ticker]["item1_sha256"]:
            raise JoinError("ROUND4_MANIFEST", "REFERENCE_IDENTITY_MISMATCH")
        record = job.get("files", {}).get("equal_chunks")
        relative = f"vectors/128/{ticker}_equal_chunks.npy"
        if (not isinstance(record, dict) or record.get("path") != relative or
                record.get("shape") != [384] or record.get("dtype") != "float64" or
                not _digest(record.get("sha256"))):
            raise JoinError("VECTOR", "REFERENCE_RECORD_MISMATCH")
        vector_path = (manifest_path.parent / relative).resolve()
        if (not vector_path.is_relative_to(manifest_path.parent.resolve()) or
                sha256(vector_path, "VECTOR") != record["sha256"]):
            raise JoinError("VECTOR", "HASH_MISMATCH")
        try:
            vector = np.load(vector_path, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise JoinError("VECTOR", "NUMPY_UNREADABLE") from exc
        if not isinstance(vector, np.ndarray):
            raise JoinError("VECTOR", "INVALID_ARRAY")
        with np.errstate(over="ignore", invalid="ignore"):
            norm = np.linalg.norm(vector) if vector.shape == (384,) and vector.dtype == np.float64 else None
        if (vector.dtype != np.float64 or vector.shape != (384,) or
                not np.isfinite(vector).all() or norm is None or
                not np.isfinite(norm) or norm == 0):
            raise JoinError("VECTOR", "INVALID_ARRAY")
        vectors[ticker] = {"path": relative, "sha256": record["sha256"],
                           "shape": [384], "dtype": "float64"}
    return manifest, vectors


def _ordered_codes(path: Path) -> dict[str, str]:
    """Read the five original codes in the producer's fixed filing order."""
    try:
        values = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
                  if line.strip()]
    except (OSError, UnicodeError) as exc:
        raise JoinError("PERMNO_CODES", "FILE_UNREADABLE") from exc
    if len(values) != len(FIRMS):
        raise JoinError("PERMNO_CODES", "FIVE_CODE_SET_MISMATCH")
    if any(re.fullmatch(r"[0-9]{1,10}", value) is None or int(value) == 0
           for value in values):
        raise JoinError("PERMNO_CODES", "INVALID_CODE")
    normalized = [value.lstrip("0") for value in values]
    if len(set(normalized)) != len(FIRMS):
        raise JoinError("PERMNO_CODES", "DUPLICATE_CODE")
    return dict(zip(FIRMS, normalized))


def _finite_decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise JoinError("LABEL", "NONFINITE_RETURN") from exc
    if not number.is_finite():
        raise JoinError("LABEL", "NONFINITE_RETURN")
    return number


def build_index(label_path: Path, daily_path: Path, codes_path: Path,
                filings: dict[str, dict], vectors: dict[str, dict],
                calendar_dates: list[str], source_bindings: dict,
                *, recomputed_labels: list[tuple] | None = None) -> dict:
    """Join all five identities before producing an index; never retain returns."""
    try:
        from audit_m5_horizon_round8 import coverage_on_reference_dates
    except ImportError as exc:
        raise JoinError("DEPENDENCY", "CALENDAR_HELPER_UNAVAILABLE") from exc
    labels = _csv_rows(label_path, LABEL_FIELDS, "LABEL", exact_header=LABEL_HEADER)
    if len(labels) != len(FIRMS):
        raise JoinError("LABEL", "FIVE_ROW_SET_MISMATCH")
    if sorted(row["ticker"] for row in labels) != sorted(FIRMS):
        raise JoinError("LABEL", "DUPLICATE_OR_MISSING_FIRM")
    daily_hash, codes_hash = sha256(daily_path), sha256(codes_path)
    codes = _ordered_codes(codes_path)
    if recomputed_labels is not None:
        if (len(recomputed_labels) != len(FIRMS) or
                [row[0] for row in recomputed_labels] != list(FIRMS)):
            raise JoinError("LABEL", "PRODUCER_RESULT_INCOMPLETE")
        recomputed = dict(zip(FIRMS, recomputed_labels))
    else:
        recomputed = None
    output_rows = []
    for row in sorted(labels, key=lambda item: FIRMS.index(item["ticker"])):
        ticker = row["ticker"]
        expected = filings[ticker]
        if row["filing_date"] != expected["filing_date"]:
            raise JoinError("LABEL", "SEC_FILING_DATE_MISMATCH")
        if (row["daily_sha256"] != daily_hash or
                row["permno_file_sha256"] != codes_hash):
            raise JoinError("LABEL", "SOURCE_HASH_MISMATCH")
        if row["scope"] != "ENGINEERING_ONLY":
            raise JoinError("LABEL", "SCOPE_MISMATCH")
        return_5 = _finite_decimal(row["price_return_5"])
        return_21 = _finite_decimal(row["price_return_21"])
        decision = _date(row["pilot_decision_session"], "LABEL")
        end_5 = _date(row["fwd5_session"], "LABEL")
        end_21 = _date(row["fwd21_session"], "LABEL")
        try:
            five = coverage_on_reference_dates(calendar_dates, set(), row["filing_date"], 5)
            twenty_one = coverage_on_reference_dates(calendar_dates, set(), row["filing_date"], 21)
        except ValueError as exc:
            raise JoinError("CALENDAR", "REFERENCE_AXIS_INVALID") from exc
        if (five["reference_start_date"] != decision.isoformat() or
                twenty_one["reference_start_date"] != decision.isoformat() or
                five["reference_end_date"] != end_5.isoformat() or
                twenty_one["reference_end_date"] != end_21.isoformat()):
            raise JoinError("LABEL", "SESSION_ENDPOINT_MISMATCH")
        if recomputed is not None:
            source = recomputed[ticker]
            comparison = (ticker, expected["filing_date"], decision.isoformat(),
                          end_5.isoformat(), end_21.isoformat(), return_5, return_21,
                          "ENGINEERING_ONLY", daily_hash, codes_hash)
            if comparison != tuple(str(value) if isinstance(value, date) else value
                                   for value in source):
                raise JoinError("LABEL", "PRODUCER_RECOMPUTE_MISMATCH")
        vector = vectors.get(ticker)
        if not isinstance(vector, dict):
            raise JoinError("VECTOR", "REFERENCE_MISSING")
        output_rows.append({"ticker": ticker, "cik": expected["cik"],
                            "accession": expected["accession"],
                            "sec_filing_date": expected["filing_date"],
                            "item1_sha256": expected["item1_sha256"],
                            "permno": codes[ticker],
                            "code_join_basis": "FIVE_LINE_FILE_IN_PINNED_PRODUCER_FILING_ORDER",
                            "decision_date": decision.isoformat(),
                            "end_date_5": end_5.isoformat(),
                            "end_date_21": end_21.isoformat(),
                            "vector": vector})
    return {"schema": "five_firms_text_label_pilot_index_v1", "scope": "ENGINEERING_ONLY",
            "status": "PASS_BYTE_BOUND_PILOT_JOIN_ONLY",
            "calendar_scope": "NASDAQ_PILOT_SESSIONS_FROM_PINNED_LOCAL_CODE",
            "historical_security_source_verified": False,
            "historical_security_source_limit": "ORDERED_CODES_FILE_ONLY_NO_INDEPENDENT_CIK_PERMNO_PROVENANCE",
            "formal_m5_admission": None, "finance_prediction_or_backtest_run": False,
            "return_definition_checked": "PINNED_PRODUCER_CLOSE_RATIO_C_END_OVER_C_DECISION_MINUS_ONE",
            "label_count": len(output_rows), "return_values_stored": False,
            "source_sha256": {**source_bindings, "labels_csv": sha256(label_path),
                              "daily_file": daily_hash, "permno_codes_file": codes_hash,
                              "checker_code": sha256(Path(__file__)),
                              "calendar_endpoint_helper": sha256(
                                  ROOT / "scripts" / "audit_m5_horizon_round8.py"),
                              "finembed_config_code": sha256(
                                  ROOT / "src" / "finembed" / "config.py"),
                              "finembed_model_identity_code": sha256(
                                  ROOT / "src" / "finembed" / "sensitivity_preflight.py")},
            "rows": output_rows}


def _verify_code_origin(root: Path) -> None:
    try:
        import audit_m5_horizon_round8
        import finembed
        import finembed.config
        import finembed.sensitivity_preflight
    except ImportError as exc:
        raise JoinError("CODE", "IMPORT_UNAVAILABLE") from exc
    expected = root / "src" / "finembed"
    for module, path in ((finembed, expected / "__init__.py"),
                         (finembed.config, expected / "config.py"),
                         (finembed.sensitivity_preflight, expected / "sensitivity_preflight.py"),
                         (audit_m5_horizon_round8, root / "scripts" / "audit_m5_horizon_round8.py")):
        if Path(module.__file__).resolve() != path.resolve():
            raise JoinError("CODE", "IMPORT_ORIGIN_MISMATCH")


def _load_pilot_source(folder: Path, filings: dict[str, dict]):
    """Import reviewed local producer code after checking exact source bytes."""
    check_path = folder / "check_five_firms_wrds_daily.py"
    make_path = folder / "make_five_firms_pilot_labels.py"
    try:
        check_bytes, make_bytes = check_path.read_bytes(), make_path.read_bytes()
    except OSError as exc:
        raise JoinError("PILOT_CODE", "FILE_UNREADABLE") from exc
    if (hashlib.sha256(check_bytes).hexdigest() != PILOT_CHECK_CODE_SHA256 or
            hashlib.sha256(make_bytes).hexdigest() != PILOT_MAKE_CODE_SHA256):
        raise JoinError("PILOT_CODE", "SOURCE_HASH_MISMATCH")
    try:
        check_module = ModuleType("check_five_firms_wrds_daily")
        check_module.__file__ = str(check_path)
        sys.modules[check_module.__name__] = check_module
        exec(compile(check_bytes, str(check_path), "exec"), check_module.__dict__)
        make_module = ModuleType("make_five_firms_pilot_labels")
        make_module.__file__ = str(make_path)
        sys.modules[make_module.__name__] = make_module
        exec(compile(make_bytes, str(make_path), "exec"), make_module.__dict__)
    except Exception as exc:
        raise JoinError("PILOT_CODE", "IMPORT_FAILED") from exc
    if (Path(check_module.__file__).resolve() != check_path.resolve() or
            Path(make_module.__file__).resolve() != make_path.resolve() or
            list(check_module.FILINGS) != list(FIRMS) or
            (check_module.SHORT_HORIZON, check_module.LONG_HORIZON,
             check_module.LOOKBACK_CLOSES) != (5, 21, 21) or
            make_module.OUTPUT != "five_firms_price_label_pilot.csv"):
        raise JoinError("PILOT_CODE", "PROTOCOL_MISMATCH")
    for ticker, (cik, filing_date) in check_module.FILINGS.items():
        if (cik.zfill(10) != filings[ticker]["cik"] or
                filing_date.isoformat() != filings[ticker]["filing_date"]):
            raise JoinError("PILOT_CODE", "SEC_FILING_MAP_MISMATCH")
    return check_module, make_module


def _load_wrds_link_source(folder: Path, filings: dict[str, dict]):
    """Load the reviewed five-filing link checker without running its CLI."""
    source_path = folder / "check_five_firms_wrds.py"
    try:
        source = source_path.read_bytes()
    except OSError as exc:
        raise JoinError("PERMNO_LINK_CODE", "FILE_UNREADABLE") from exc
    if hashlib.sha256(source).hexdigest() != WRDS_LINK_CODE_SHA256:
        raise JoinError("PERMNO_LINK_CODE", "SOURCE_HASH_MISMATCH")
    try:
        module = ModuleType("five_firms_wrds_link_source")
        module.__file__ = str(source_path)
        exec(compile(source, str(source_path), "exec"), module.__dict__)
    except Exception as exc:
        raise JoinError("PERMNO_LINK_CODE", "IMPORT_FAILED") from exc
    if (Path(module.__file__).resolve() != source_path.resolve() or
            list(module.FILINGS) != list(FIRMS) or
            module.CODE_FILE != "five_firms_permno_codes.txt"):
        raise JoinError("PERMNO_LINK_CODE", "PROTOCOL_MISMATCH")
    for ticker, (cik, filing_date) in module.FILINGS.items():
        if (cik.zfill(10) != filings[ticker]["cik"] or
                filing_date.isoformat() != filings[ticker]["filing_date"]):
            raise JoinError("PERMNO_LINK_CODE", "SEC_FILING_MAP_MISMATCH")
    return module


def _wrds_export_kind(path: Path, module) -> str:
    """Classify a selected CSV by the reviewed source's required headers."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            headers = [field.strip().lower() for field in next(csv.reader(handle, strict=True), [])]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise JoinError("WRDS_EXPORT", "HEADER_UNREADABLE") from exc
    if len(headers) != len(set(headers)):
        raise JoinError("WRDS_EXPORT", "HEADER_AMBIGUOUS")
    kinds = [kind for kind, required in module.EXPORT_HEADERS.items()
             if required.issubset(headers)]
    if len(kinds) != 1:
        raise JoinError("WRDS_EXPORT", "HEADER_UNRECOGNIZED")
    return kinds[0]


def _select_wrds_exports(module) -> tuple[Path, Path]:
    """Search only the named folders, then ask the user for the two exports."""
    folders = [WRDS_LINK_FOLDER, Path.home() / "Downloads", Path("E:/")]
    try:
        link_path, names_path = module.locate_exports(folders, known_names_only=True)
        return link_path, names_path
    except module.ReviewNeeded:
        pass
    first = _choose([], "Select either of the two WRDS link/Names CSV exports", "WRDS_EXPORT")
    second = _choose([], "Select the other WRDS link/Names CSV export", "WRDS_EXPORT")
    if first.resolve() == second.resolve():
        raise JoinError("WRDS_EXPORT", "SAME_FILE_SELECTED_TWICE")
    kinds = {_wrds_export_kind(path, module): path for path in (first, second)}
    if set(kinds) != {"LINK", "NAMES"}:
        raise JoinError("WRDS_EXPORT", "SELECTED_FILE_KIND_MISMATCH")
    return kinds["LINK"], kinds["NAMES"]


def _verify_wrds_security_link(link_path: Path, names_path: Path, codes_path: Path,
                               filings: dict[str, dict], module) -> dict[str, str]:
    """Recheck the bounded CIK-CUSIP/Names join on the user's machine."""
    hashes = {"wrds_link_csv": sha256(link_path, "WRDS_LINK_CSV"),
              "wrds_names_csv": sha256(names_path, "WRDS_NAMES_CSV"),
              "wrds_link_code": sha256(Path(module.__file__), "PERMNO_LINK_CODE")}
    try:
        links, names = module.group_export_rows(link_path, names_path)
        derived = {}
        for ticker in FIRMS:
            cik, filing_date = module.FILINGS[ticker]
            if (str(cik).zfill(10) != filings[ticker]["cik"] or
                    filing_date.isoformat() != filings[ticker]["filing_date"]):
                raise JoinError("PERMNO_LINK", "SEC_FILING_MAP_MISMATCH")
            derived[ticker] = module.historical_permno(
                names[ticker], module.filing_cusips(links[cik], filing_date), filing_date)
    except JoinError:
        raise
    except Exception as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", code):
            code = "SOURCE_CHECK_FAILED"
        raise JoinError("PERMNO_LINK", code) from exc
    if len(set(derived.values())) != len(FIRMS):
        raise JoinError("PERMNO_LINK", "DUPLICATE_PERMNO_ACROSS_FILERS")
    if derived != _ordered_codes(codes_path):
        raise JoinError("PERMNO_LINK", "WRDS_CODE_MAPPING_MISMATCH")
    if (sha256(link_path, "WRDS_LINK_CSV") != hashes["wrds_link_csv"] or
            sha256(names_path, "WRDS_NAMES_CSV") != hashes["wrds_names_csv"] or
            sha256(Path(module.__file__), "PERMNO_LINK_CODE") != hashes["wrds_link_code"]):
        raise JoinError("PERMNO_LINK", "SOURCE_CHANGED_DURING_CHECK")
    return hashes


def _choose(candidates: list[Path], title: str, category: str) -> Path:
    present = [path for path in candidates if path.is_file()]
    if len(present) == 1:
        return present[0]
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        selected = filedialog.askopenfilename(title=title)
        window.destroy()
    except Exception as exc:
        raise JoinError(category, "FILE_DIALOG_UNAVAILABLE") from exc
    if not selected:
        raise JoinError(category, "SELECTION_CANCELLED")
    path = Path(selected)
    if not path.is_file():
        raise JoinError(category, "FILE_UNREADABLE")
    return path


def _ignored_output(root: Path, path: Path) -> None:
    target = path.resolve()
    if not target.is_relative_to((root / "artifacts").resolve()):
        raise JoinError("OUTPUT", "OUTSIDE_LOCAL_ARTIFACTS")
    relative = target.relative_to(root.resolve()).as_posix()
    result = subprocess.run(["git", "check-ignore", "-q", "--", relative],
                            cwd=root, capture_output=True, check=False)
    if result.returncode != 0:
        raise JoinError("OUTPUT", "NOT_GIT_IGNORED")
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative],
                             cwd=root, capture_output=True, check=False)
    if tracked.returncode == 0:
        raise JoinError("OUTPUT", "ALREADY_GIT_TRACKED")


def _private_input_not_tracked(root: Path, path: Path, category: str) -> None:
    selected = path.resolve()
    if not selected.is_relative_to(root.resolve()):
        return
    relative = selected.relative_to(root.resolve()).as_posix()
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative],
                             cwd=root, capture_output=True, check=False)
    if tracked.returncode == 0:
        raise JoinError(category, "PRIVATE_INPUT_GIT_TRACKED")


def _write_atomic_no_overwrite(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and path.read_bytes() == payload:
            return
        raise JoinError("OUTPUT", "DIFFERENT_RESULT_EXISTS")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".five_firms_join_",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        if path.is_file() and path.read_bytes() == payload:
            return
        raise JoinError("OUTPUT", "DIFFERENT_RESULT_EXISTS") from exc
    except OSError as exc:
        raise JoinError("OUTPUT", "ATOMIC_WRITE_FAILED") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _require_independent_linkage_source(index: dict) -> None:
    if index.get("historical_security_source_verified") is not True:
        raise JoinError("PERMNO_LINK", "INDEPENDENT_SOURCE_NOT_LOCATED")


def run_local() -> None:
    """Real-file processing is performed only when the user launches this script."""
    root = ROOT
    if sys.version_info[:2] != (3, 11):
        raise JoinError("PYTHON", "VERSION_MISMATCH")
    _verify_code_origin(root)
    m5_path = root / "configs" / "m5_financial_protocol_round8.json"
    m5 = _json(m5_path, "M5_CANDIDATE")
    if (sha256(m5_path) != M5_SHA256 or m5.get("status") != "PROPOSED" or
            m5.get("researcher_accepted") is not False or
            m5.get("formal_financial_experiment_status") != "NOT_RUN"):
        raise JoinError("M5_CANDIDATE", "STATUS_OR_HASH_MISMATCH")
    local = root / "artifacts" / "five_firms_text_label_pilot"
    inventory_path = _choose([root / "artifacts" / "round8" / "sec_metadata_inventory_v1.json"],
                             "Select sec_metadata_inventory_v1.json", "SEC_METADATA")
    manifest_path = _choose([root / "artifacts" / "round4" /
                             "20261031-small-chunk-real5-run-v1" / "manifest.json"],
                            "Select the round-4 real-model manifest.json", "ROUND4_MANIFEST")
    mapping_path = root / "configs" / "five_firms_text_label_filing_map.json"
    candidate_path = root / "configs" / "small_chunk_probe_round2.json"
    inventory_hash_before = sha256(inventory_path, "SEC_METADATA")
    mapping_hash_before = sha256(mapping_path, "SEC_MAP")
    filings = verify_filing_map(mapping_path, inventory_path, candidate_path)
    manifest, vectors = verify_round4_vectors(manifest_path, filings, root)
    pilot_check, pilot_maker = _load_pilot_source(PILOT_FOLDER, filings)
    wrds_link = _load_wrds_link_source(WRDS_LINK_FOLDER, filings)
    try:
        daily_candidates = sorted(path for path in PILOT_FOLDER.iterdir()
                                  if path.is_file() and PILOT_DAILY_NAME.fullmatch(path.name))
    except OSError as exc:
        raise JoinError("DAILY_FILE", "PILOT_FOLDER_UNREADABLE") from exc
    label_path = _choose([PILOT_FOLDER / "five_firms_price_label_pilot.csv"],
                         "Select five_firms_price_label_pilot.csv", "LABEL")
    daily_path = _choose(daily_candidates,
                         "Select the exact CRSP daily CSV hashed in the pilot labels", "DAILY_FILE")
    codes_path = _choose([PILOT_FOLDER / "five_firms_permno_codes.txt"],
                         "Select five_firms_permno_codes.txt", "PERMNO_CODES")
    for selected, category in ((label_path, "LABEL"), (daily_path, "DAILY_FILE"),
                               (codes_path, "PERMNO_CODES")):
        _private_input_not_tracked(root, selected, category)
    wrds_link_path, wrds_names_path = _select_wrds_exports(wrds_link)
    for selected, category in ((wrds_link_path, "WRDS_LINK_CSV"),
                               (wrds_names_path, "WRDS_NAMES_CSV")):
        _private_input_not_tracked(root, selected, category)
    wrds_bindings = _verify_wrds_security_link(
        wrds_link_path, wrds_names_path, codes_path, filings, wrds_link)
    original_codes_path = WRDS_LINK_FOLDER / wrds_link.CODE_FILE
    if original_codes_path.is_file():
        if _ordered_codes(original_codes_path) != _ordered_codes(codes_path):
            raise JoinError("PERMNO_LINK", "ORIGINAL_CODE_FILE_MISMATCH")
        wrds_bindings["wrds_generated_codes_file"] = sha256(
            original_codes_path, "PERMNO_LINK")
    try:
        producer_codes = pilot_check.read_codes(codes_path)
    except Exception as exc:
        raise JoinError("PERMNO_CODES", "PRODUCER_CODE_READ_FAILED") from exc
    if _ordered_codes(codes_path) != producer_codes:
        raise JoinError("PERMNO_CODES", "PRODUCER_CODE_MAPPING_MISMATCH")
    label_hash_before = sha256(label_path, "LABEL")
    try:
        with io.StringIO() as suppressed:
            from contextlib import redirect_stdout
            with redirect_stdout(suppressed):
                recomputed = pilot_maker.make_labels(daily_path, codes_path)
    except Exception as exc:
        raise JoinError("LABEL_SOURCE", "PRODUCER_RECOMPUTE_FAILED") from exc
    calendar_dates = [day.isoformat() for day in pilot_check.trading_sessions()]
    index = build_index(label_path, daily_path, codes_path, filings, vectors,
                        calendar_dates,
                        {"sec_inventory": sha256(inventory_path),
                         "filing_map": sha256(mapping_path),
                         "round4_manifest": sha256(manifest_path),
                         "round4_candidate": ROUND4_CANDIDATE_SHA256,
                         "round4_metric_contract": ROUND4_METRICS_CONTRACT_SHA256,
                         "m5_proposed_candidate": sha256(m5_path),
                         "pilot_check_code": sha256(PILOT_FOLDER / "check_five_firms_wrds_daily.py"),
                         "pilot_producer_code": sha256(PILOT_FOLDER / "make_five_firms_pilot_labels.py"),
                         **wrds_bindings},
                        recomputed_labels=recomputed)
    if (index["source_sha256"]["labels_csv"] != label_hash_before or
            index["source_sha256"]["pilot_check_code"] != PILOT_CHECK_CODE_SHA256 or
            index["source_sha256"]["pilot_producer_code"] != PILOT_MAKE_CODE_SHA256 or
            index["source_sha256"]["sec_inventory"] != inventory_hash_before or
            index["source_sha256"]["filing_map"] != mapping_hash_before):
        raise JoinError("INPUT", "SOURCE_CHANGED_DURING_CHECK")
    if (sha256(daily_path, "DAILY_FILE") != index["source_sha256"]["daily_file"] or
            sha256(codes_path, "PERMNO_CODES") != index["source_sha256"]["permno_codes_file"] or
            sha256(manifest_path, "ROUND4_MANIFEST") != ROUND4_MANIFEST_SHA256):
        raise JoinError("INPUT", "SOURCE_CHANGED_DURING_CHECK")
    if (sha256(wrds_link_path, "WRDS_LINK_CSV") != wrds_bindings["wrds_link_csv"] or
            sha256(wrds_names_path, "WRDS_NAMES_CSV") != wrds_bindings["wrds_names_csv"] or
            sha256(WRDS_LINK_FOLDER / "check_five_firms_wrds.py", "PERMNO_LINK_CODE") !=
            WRDS_LINK_CODE_SHA256):
        raise JoinError("PERMNO_LINK", "SOURCE_CHANGED_DURING_CHECK")
    if ("wrds_generated_codes_file" in wrds_bindings and
            sha256(original_codes_path, "PERMNO_LINK") !=
            wrds_bindings["wrds_generated_codes_file"]):
        raise JoinError("PERMNO_LINK", "SOURCE_CHANGED_DURING_CHECK")
    for reference in index["rows"]:
        vector_path = manifest_path.parent / reference["vector"]["path"]
        if sha256(vector_path, "VECTOR") != reference["vector"]["sha256"]:
            raise JoinError("VECTOR", "SOURCE_CHANGED_DURING_CHECK")
    if (sha256(inventory_path, "SEC_METADATA") != inventory_hash_before or
            sha256(mapping_path, "SEC_MAP") != mapping_hash_before):
        raise JoinError("SEC_MAP", "SOURCE_CHANGED_DURING_CHECK")
    if sha256(candidate_path, "ROUND4_CANDIDATE") != ROUND4_CANDIDATE_SHA256:
        raise JoinError("ROUND4_CANDIDATE", "SOURCE_CHANGED_DURING_CHECK")
    if sha256(m5_path, "M5_CANDIDATE") != M5_SHA256:
        raise JoinError("M5_CANDIDATE", "SOURCE_CHANGED_DURING_CHECK")
    index["model_identity"] = manifest["model_identity"]
    index["historical_security_source_verified"] = True
    index["historical_security_source_limit"] = (
        "PINNED_WRDS_CIK_CUSIP_AND_CRSP_NAMES_CHECK_ON_SEC_FILING_DATE_ONLY")
    index["wrds_source_claim"] = "USER_REPORTED_WRDS_EXPORTS_NOT_LICENSE_CERTIFIED"
    _require_independent_linkage_source(index)
    output = local / "five_firms_text_label_pilot_index.json"
    _ignored_output(root, output)
    _write_atomic_no_overwrite(output, index)


def main() -> int:
    try:
        run_local()
    except JoinError as exc:
        print(f"JOIN_FAIL {exc.category}:{exc.code}")
        return 1
    except Exception:
        print("JOIN_FAIL INTERNAL:UNEXPECTED")
        return 1
    print("WRDS_FIVE_FIRMS_TEXT_LABEL_JOIN_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
