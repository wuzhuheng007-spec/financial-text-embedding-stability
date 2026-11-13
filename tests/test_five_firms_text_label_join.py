"""SYNTHETIC_TEST_ONLY tests for the offline five-filing join.

The real-data entry points run_local and main are never invoked.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "check_five_firms_text_label_join", ROOT / "scripts/check_five_firms_text_label_join.py"
)
assert SPEC and SPEC.loader
join = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(join)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _csv(path: Path, fields: set[str] | tuple[str, ...], rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(fields) if isinstance(fields, set)
                                else list(fields))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def synthetic_case(tmp_path: Path) -> dict:
    axis = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)]
    # These dates are a software fixture, not a real exchange calendar.
    filing_day = "2025-01-02"
    daily_path = tmp_path / "SYNTHETIC_TEST_ONLY_daily.csv"
    daily_path.write_text("SYNTHETIC_TEST_ONLY\n", encoding="utf-8")
    filings, vectors, codes, labels = {}, {}, [], []
    for i, ticker in enumerate(join.FIRMS, start=1):
        cik = f"{i:010d}"
        permno = str(900000 + i)
        accession = f"{i:010d}-25-000001"
        item_hash = _digest(f"{ticker}:SYNTHETIC_TEST_ONLY:Item 1")
        filings[ticker] = {"ticker": ticker, "cik": cik, "accession": accession,
                           "filing_date": filing_day, "item1_sha256": item_hash}
        vectors[ticker] = {"path": f"SYNTHETIC_TEST_ONLY/{ticker}.npy",
                           "sha256": _digest(ticker), "shape": [384], "dtype": "float64"}
        codes.append(permno)
        labels.append({"ticker": ticker, "filing_date": filing_day,
                       "pilot_decision_session": axis[2], "fwd5_session": axis[7],
                       "fwd21_session": axis[23], "price_return_5": "0.01",
                       "price_return_21": "-0.02", "scope": "ENGINEERING_ONLY",
                       "daily_sha256": join.sha256(daily_path),
                       "permno_file_sha256": ""})
    codes_path = tmp_path / "SYNTHETIC_TEST_ONLY_five_firms_permno_codes.txt"
    codes_path.write_text("\n".join(codes) + "\n", encoding="utf-8")
    for row in labels:
        row["permno_file_sha256"] = join.sha256(codes_path)
    label_path = _csv(tmp_path / "SYNTHETIC_TEST_ONLY_labels.csv", join.LABEL_HEADER, labels)
    return {"axis": axis, "filings": filings, "vectors": vectors, "codes": codes,
            "labels": labels, "label_path": label_path, "codes_path": codes_path,
            "daily_path": daily_path, "output": tmp_path / "SYNTHETIC_TEST_ONLY_index.json"}


def _relabel(case: dict) -> None:
    _csv(case["label_path"], join.LABEL_HEADER, case["labels"])


def _build(case: dict, *, recomputed_labels: list[tuple] | None = None) -> dict:
    return join.build_index(case["label_path"], case["daily_path"], case["codes_path"],
                            case["filings"], case["vectors"], case["axis"],
                            {"fixture": "SYNTHETIC_TEST_ONLY"},
                            recomputed_labels=recomputed_labels)


def _blocked(case: dict, category: str, code: str,
             *, recomputed_labels: list[tuple] | None = None) -> None:
    with pytest.raises(join.JoinError) as caught:
        index = _build(case, recomputed_labels=recomputed_labels)
        join._write_atomic_no_overwrite(case["output"], index)
    assert (caught.value.category, caught.value.code) == (category, code)
    assert not case["output"].exists()


def test_five_synthetic_rows_build_without_storing_returns(synthetic_case: dict):
    index = _build(synthetic_case)
    assert index["scope"] == "ENGINEERING_ONLY"
    assert index["label_count"] == 5
    assert [row["ticker"] for row in index["rows"]] == list(join.FIRMS)
    assert [row["accession"] for row in index["rows"]] == [
        synthetic_case["filings"][ticker]["accession"] for ticker in join.FIRMS]
    assert index["return_values_stored"] is False
    assert all("price_return_5" not in row and "price_return_21" not in row
               for row in index["rows"])
    assert index["historical_security_source_verified"] is False
    assert not synthetic_case["output"].exists()


def test_missing_independent_linkage_blocks_output(synthetic_case: dict):
    index = _build(synthetic_case)
    with pytest.raises(join.JoinError) as caught:
        join._require_independent_linkage_source(index)
        join._write_atomic_no_overwrite(synthetic_case["output"], index)
    assert (caught.value.category, caught.value.code) == (
        "PERMNO_LINK", "INDEPENDENT_SOURCE_NOT_LOCATED")
    assert not synthetic_case["output"].exists()


@pytest.mark.parametrize("problem,code", [
    ("duplicate", "DUPLICATE_OR_MISSING_FIRM"),
    ("missing", "FIVE_ROW_SET_MISMATCH"),
])
def test_duplicate_or_missing_company_blocks_output(synthetic_case: dict,
                                                     problem: str, code: str):
    if problem == "duplicate":
        synthetic_case["labels"][-1] = dict(synthetic_case["labels"][0])
    else:
        synthetic_case["labels"].pop()
    _relabel(synthetic_case)
    _blocked(synthetic_case, "LABEL", code)


def test_sec_filing_date_mismatch_blocks_output(synthetic_case: dict):
    synthetic_case["labels"][0]["filing_date"] = "2025-01-01"
    _relabel(synthetic_case)
    _blocked(synthetic_case, "LABEL", "SEC_FILING_DATE_MISMATCH")


@pytest.mark.parametrize("problem,code", [
    ("missing", "FIVE_CODE_SET_MISMATCH"),
    ("duplicate", "DUPLICATE_CODE"),
])
def test_invalid_ordered_codes_block_output(synthetic_case: dict,
                                            problem: str, code: str):
    codes = synthetic_case["codes"][:]
    if problem == "missing":
        codes.pop()
    else:
        codes[-1] = codes[0]
    synthetic_case["codes_path"].write_text("\n".join(codes) + "\n", encoding="utf-8")
    for row in synthetic_case["labels"]:
        row["permno_file_sha256"] = join.sha256(synthetic_case["codes_path"])
    _relabel(synthetic_case)
    _blocked(synthetic_case, "PERMNO_CODES", code)


def test_label_header_must_match_producer_order(synthetic_case: dict):
    _csv(synthetic_case["label_path"], join.LABEL_FIELDS, synthetic_case["labels"])
    _blocked(synthetic_case, "LABEL", "SCHEMA_MISMATCH")


@pytest.mark.parametrize("source", ["daily_path", "codes_path"])
def test_either_source_hash_change_blocks_output(synthetic_case: dict, source: str):
    if source == "codes_path":
        codes = synthetic_case["codes"][:]
        codes[0] = str(int(codes[0]) + 100)
        synthetic_case[source].write_text("\n".join(codes) + "\n", encoding="utf-8")
    else:
        with synthetic_case[source].open("a", encoding="utf-8") as handle:
            handle.write("SYNTHETIC_TEST_ONLY tamper\n")
    _blocked(synthetic_case, "LABEL", "SOURCE_HASH_MISMATCH")


@pytest.mark.parametrize("field", ["price_return_5", "price_return_21"])
def test_nonfinite_label_blocks_output(synthetic_case: dict, field: str):
    synthetic_case["labels"][2][field] = "nan"
    _relabel(synthetic_case)
    _blocked(synthetic_case, "LABEL", "NONFINITE_RETURN")


def test_wrong_scope_blocks_output(synthetic_case: dict):
    synthetic_case["labels"][0]["scope"] = "SYNTHETIC_TEST_ONLY_WRONG_SCOPE"
    _relabel(synthetic_case)
    _blocked(synthetic_case, "LABEL", "SCOPE_MISMATCH")


@pytest.mark.parametrize("field", ["pilot_decision_session", "fwd5_session", "fwd21_session"])
def test_wrong_calendar_endpoint_blocks_output(synthetic_case: dict, field: str):
    synthetic_case["labels"][1][field] = "2025-02-01"
    _relabel(synthetic_case)
    _blocked(synthetic_case, "LABEL", "SESSION_ENDPOINT_MISMATCH")


def _synthetic_recomputed(case: dict) -> list[tuple]:
    return [(row["ticker"], date.fromisoformat(row["filing_date"]),
             date.fromisoformat(row["pilot_decision_session"]),
             date.fromisoformat(row["fwd5_session"]),
             date.fromisoformat(row["fwd21_session"]),
             Decimal(row["price_return_5"]), Decimal(row["price_return_21"]),
             row["scope"], row["daily_sha256"], row["permno_file_sha256"])
            for row in case["labels"]]


def test_producer_recompute_matches_synthetic_labels(synthetic_case: dict):
    index = _build(synthetic_case, recomputed_labels=_synthetic_recomputed(synthetic_case))
    assert index["label_count"] == 5
    assert index["historical_security_source_verified"] is False


def test_producer_recompute_mismatch_blocks_output(synthetic_case: dict):
    recomputed = _synthetic_recomputed(synthetic_case)
    first = list(recomputed[0])
    first[5] = Decimal("0.03")
    recomputed[0] = tuple(first)
    _blocked(synthetic_case, "LABEL", "PRODUCER_RECOMPUTE_MISMATCH",
             recomputed_labels=recomputed)


def test_pilot_source_hash_change_is_rejected_before_execution(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic_case: dict):
    folder = tmp_path / "SYNTHETIC_TEST_ONLY_pilot_code"
    folder.mkdir()
    check_path = folder / "check_five_firms_wrds_daily.py"
    make_path = folder / "make_five_firms_pilot_labels.py"
    marker = folder / "SYNTHETIC_TEST_ONLY_executed.txt"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    check_path.write_text(source, encoding="utf-8")
    make_path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(join, "PILOT_CHECK_CODE_SHA256", _digest("different synthetic source"))
    monkeypatch.setattr(join, "PILOT_MAKE_CODE_SHA256", join.sha256(make_path))
    with pytest.raises(join.JoinError) as caught:
        join._load_pilot_source(folder, synthetic_case["filings"])
    assert (caught.value.category, caught.value.code) == (
        "PILOT_CODE", "SOURCE_HASH_MISMATCH")
    assert not marker.exists()


def _synthetic_wrds_link_module(case: dict) -> tuple[SimpleNamespace, Path, Path]:
    """Exercise the join against synthetic exports without loading WRDS files."""
    folder = case["codes_path"].parent
    link_rows = []
    names_rows = []
    for index, ticker in enumerate(join.FIRMS, start=1):
        filing = case["filings"][ticker]
        short_cusip = f"{index:08d}"
        link_rows.append({"cik": filing["cik"], "cusip": short_cusip,
                          "cikdate1": "2024-01-01"})
        names_rows.append({"ticker": ticker, "cusip": short_cusip,
                           "permno": case["codes"][index - 1],
                           "secinfostartdt": "2024-01-01",
                           "secinfoenddt": "2026-01-01"})
    link_path = _csv(folder / "SYNTHETIC_TEST_ONLY_wrds_cik_cusip.csv",
                     ("cik", "cusip", "cikdate1"), link_rows)
    names_path = _csv(folder / "SYNTHETIC_TEST_ONLY_wrds_names.csv",
                      ("ticker", "cusip", "permno", "secinfostartdt",
                       "secinfoenddt"), names_rows)
    code_path = folder / "SYNTHETIC_TEST_ONLY_wrds_link_code.py"
    code_path.write_text("# SYNTHETIC_TEST_ONLY link-code identity\n", encoding="utf-8")

    def group_export_rows(left: Path, right: Path):
        with left.open(encoding="utf-8", newline="") as stream:
            links = list(csv.DictReader(stream))
        with right.open(encoding="utf-8", newline="") as stream:
            names = list(csv.DictReader(stream))
        return ({filing["cik"].lstrip("0"): [row for row in links
                if row["cik"] == filing["cik"]] for filing in case["filings"].values()},
                {ticker: [row for row in names if row["ticker"] == ticker]
                 for ticker in join.FIRMS})

    def filing_cusips(rows: list[dict], filing_date: date):
        return {row["cusip"] for row in rows
                if date.fromisoformat(row["cikdate1"]) <= filing_date}

    def historical_permno(rows: list[dict], linked_cusips: set[str], filing_date: date):
        matches = {row["permno"] for row in rows
                   if (row["cusip"] in linked_cusips and
                       date.fromisoformat(row["secinfostartdt"]) <= filing_date <=
                       date.fromisoformat(row["secinfoenddt"]))}
        if len(matches) != 1:
            raise ValueError("SYNTHETIC_TEST_ONLY link ambiguity")
        return matches.pop()

    return (SimpleNamespace(__file__=str(code_path), FILINGS={ticker: (
                                case["filings"][ticker]["cik"].lstrip("0"),
                                date.fromisoformat(case["filings"][ticker]["filing_date"]))
                             for ticker in join.FIRMS},
                            group_export_rows=group_export_rows,
                            filing_cusips=filing_cusips,
                            historical_permno=historical_permno),
            link_path, names_path)


def test_synthetic_wrds_links_match_all_five_ordered_codes(synthetic_case: dict):
    module, link_path, names_path = _synthetic_wrds_link_module(synthetic_case)
    bindings = join._verify_wrds_security_link(
        link_path, names_path, synthetic_case["codes_path"],
        synthetic_case["filings"], module)
    assert bindings["wrds_link_csv"] == join.sha256(link_path)
    assert bindings["wrds_names_csv"] == join.sha256(names_path)
    assert bindings["wrds_link_code"] == join.sha256(Path(module.__file__))
    assert not synthetic_case["output"].exists()


def test_wrds_export_dialog_classifies_reverse_selection(
        synthetic_case: dict, monkeypatch: pytest.MonkeyPatch):
    _, link_path, names_path = _synthetic_wrds_link_module(synthetic_case)

    class NotLocated(Exception):
        pass

    def not_located(*args, **kwargs):
        raise NotLocated("SYNTHETIC_TEST_ONLY")

    module = SimpleNamespace(
        ReviewNeeded=NotLocated, locate_exports=not_located,
        EXPORT_HEADERS={"LINK": {"cik", "cusip", "cikdate1"},
                        "NAMES": {"permno", "cusip", "ticker",
                                  "secinfostartdt", "secinfoenddt"}},
    )
    selections = iter((names_path, link_path))
    monkeypatch.setattr(join, "_choose", lambda *args: next(selections))
    assert join._select_wrds_exports(module) == (link_path, names_path)


def test_wrds_auto_search_includes_known_e_drive_root(synthetic_case: dict):
    _, link_path, names_path = _synthetic_wrds_link_module(synthetic_case)

    def locate(folders: list[Path], *, known_names_only: bool):
        assert Path("E:/") in folders
        assert known_names_only is True
        return link_path, names_path

    module = SimpleNamespace(locate_exports=locate)
    assert join._select_wrds_exports(module) == (link_path, names_path)


def test_synthetic_wrds_link_mismatch_blocks_output(synthetic_case: dict):
    module, link_path, names_path = _synthetic_wrds_link_module(synthetic_case)
    codes = synthetic_case["codes"][:]
    codes[:2] = codes[1::-1]
    synthetic_case["codes_path"].write_text("\n".join(codes) + "\n", encoding="utf-8")
    with pytest.raises(join.JoinError) as caught:
        join._verify_wrds_security_link(link_path, names_path,
                                        synthetic_case["codes_path"],
                                        synthetic_case["filings"], module)
    assert (caught.value.category, caught.value.code) == (
        "PERMNO_LINK", "WRDS_CODE_MAPPING_MISMATCH")
    assert not synthetic_case["output"].exists()


def test_synthetic_wrds_duplicate_security_blocks_output(synthetic_case: dict):
    module, link_path, names_path = _synthetic_wrds_link_module(synthetic_case)
    with names_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[-1]["permno"] = rows[0]["permno"]
    _csv(names_path, ("ticker", "cusip", "permno", "secinfostartdt",
                      "secinfoenddt"), rows)
    with pytest.raises(join.JoinError) as caught:
        join._verify_wrds_security_link(link_path, names_path,
                                        synthetic_case["codes_path"],
                                        synthetic_case["filings"], module)
    assert (caught.value.category, caught.value.code) == (
        "PERMNO_LINK", "DUPLICATE_PERMNO_ACROSS_FILERS")
    assert not synthetic_case["output"].exists()


def test_synthetic_wrds_filing_identity_conflict_blocks_output(synthetic_case: dict):
    module, link_path, names_path = _synthetic_wrds_link_module(synthetic_case)
    cik, _ = module.FILINGS["AAPL"]
    module.FILINGS["AAPL"] = (cik, date(2025, 1, 3))
    with pytest.raises(join.JoinError) as caught:
        join._verify_wrds_security_link(link_path, names_path,
                                        synthetic_case["codes_path"],
                                        synthetic_case["filings"], module)
    assert (caught.value.category, caught.value.code) == (
        "PERMNO_LINK", "SEC_FILING_MAP_MISMATCH")
    assert not synthetic_case["output"].exists()


def test_synthetic_wrds_export_change_during_check_is_rejected(synthetic_case: dict):
    module, link_path, names_path = _synthetic_wrds_link_module(synthetic_case)
    original = module.group_export_rows

    def tampering_group_export_rows(left: Path, right: Path):
        rows = original(left, right)
        with left.open("a", encoding="utf-8") as stream:
            stream.write("SYNTHETIC_TEST_ONLY source changed\n")
        return rows

    module.group_export_rows = tampering_group_export_rows
    with pytest.raises(join.JoinError) as caught:
        join._verify_wrds_security_link(link_path, names_path,
                                        synthetic_case["codes_path"],
                                        synthetic_case["filings"], module)
    assert (caught.value.category, caught.value.code) == (
        "PERMNO_LINK", "SOURCE_CHANGED_DURING_CHECK")
    assert not synthetic_case["output"].exists()


def test_wrds_link_source_hash_change_is_rejected_before_execution(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic_case: dict):
    folder = tmp_path / "SYNTHETIC_TEST_ONLY_wrds_link_code"
    folder.mkdir()
    source_path = folder / "check_five_firms_wrds.py"
    marker = folder / "SYNTHETIC_TEST_ONLY_executed.txt"
    source_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8")
    monkeypatch.setattr(join, "WRDS_LINK_CODE_SHA256", _digest("other synthetic source"))
    with pytest.raises(join.JoinError) as caught:
        join._load_wrds_link_source(folder, synthetic_case["filings"])
    assert (caught.value.category, caught.value.code) == (
        "PERMNO_LINK_CODE", "SOURCE_HASH_MISMATCH")
    assert not marker.exists()


def test_pinned_wrds_link_code_with_synthetic_exports_respects_effective_dates(
        tmp_path: Path):
    """Run reviewed link functions on fake rows, never on actual WRDS exports."""
    source_path = join.WRDS_LINK_FOLDER / "check_five_firms_wrds.py"
    if not source_path.is_file():
        pytest.skip("Pinned WRDS link source code is unavailable on this machine")
    mapping = json.loads((ROOT / "configs/five_firms_text_label_filing_map.json")
                         .read_text(encoding="utf-8"))
    filings = {row["ticker"]: row for row in mapping["filings"]}
    module = join._load_wrds_link_source(join.WRDS_LINK_FOLDER, filings)
    link_rows, names_rows, fake_codes = [], [], []
    for index, ticker in enumerate(join.FIRMS, start=1):
        row = filings[ticker]
        short_cusip = f"{index:08d}"
        permno = str(900000 + index)
        fake_codes.append(permno)
        link_rows.append({"cik": row["cik"].lstrip("0"), "cusip": short_cusip,
                          "cusip_full": short_cusip + "0",
                          "cikdate1": "2024-01-01", "cikdate2": "2025-12-31",
                          "validated": "3"})
        names_rows.append({"permno": permno, "cusip": short_cusip + "0",
                           "ticker": ticker, "secinfostartdt": "2024-01-01",
                           "secinfoenddt": "2025-12-31"})
    link_path = _csv(tmp_path / "SYNTHETIC_TEST_ONLY_link.csv",
                     module.LINK_COLUMNS, link_rows)
    names_path = _csv(tmp_path / "SYNTHETIC_TEST_ONLY_names.csv",
                      module.NAMES_COLUMNS, names_rows)
    codes_path = tmp_path / "SYNTHETIC_TEST_ONLY_codes.txt"
    codes_path.write_text("\n".join(fake_codes) + "\n", encoding="utf-8")
    bindings = join._verify_wrds_security_link(
        link_path, names_path, codes_path, filings, module)
    assert bindings["wrds_link_csv"] == join.sha256(link_path)
    assert bindings["wrds_names_csv"] == join.sha256(names_path)
    assert bindings["wrds_link_code"] == join.WRDS_LINK_CODE_SHA256

    names_rows[0]["secinfoenddt"] = "2024-12-31"
    _csv(names_path, module.NAMES_COLUMNS, names_rows)
    with pytest.raises(join.JoinError) as caught:
        join._verify_wrds_security_link(link_path, names_path, codes_path,
                                        filings, module)
    assert (caught.value.category, caught.value.code) == (
        "PERMNO_LINK", "NO_HISTORICAL_PERMNO_MATCH")


def test_sec_metadata_must_match_official_filing_date(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch,
                                                     synthetic_case: dict):
    candidate = _json(tmp_path / "candidate.json", {"input": {
        "company_order": list(join.FIRMS), "form": "10-K", "section": "ITEM_1_BUSINESS"}})
    monkeypatch.setattr(join, "ROUND4_CANDIDATE_SHA256", join.sha256(candidate))
    inventory = _json(tmp_path / "sec_inventory.json", {
        "firms": {ticker: {"cik": row["cik"], "rows": [{
            "form": "10-K", "filingDate": "2025-01-01",
            "accessionNumber": row["accession"], "cik": row["cik"]}]}
            for ticker, row in synthetic_case["filings"].items()}})
    monkeypatch.setattr(join, "SEC_INVENTORY_SHA256", join.sha256(inventory))
    filings = []
    for row in synthetic_case["filings"].values():
        filings.append({**row, "sec_filing_url": (
            f"https://www.sec.gov/Archives/edgar/data/{int(row['cik'])}/"
            f"{row['accession'].replace('-', '')}/{row['accession']}-index.html")})
    mapping = _json(tmp_path / "filing_map.json", {
        "schema": "five_firms_text_label_filing_map_v1", "scope": "ENGINEERING_ONLY",
        "sec_inventory_sha256": join.sha256(inventory),
        "round4_manifest_sha256": join.ROUND4_MANIFEST_SHA256,
        "filings": filings})
    with pytest.raises(join.JoinError) as caught:
        join.verify_filing_map(mapping, inventory, candidate)
    assert (caught.value.category, caught.value.code) == (
        "SEC_METADATA", "FILING_DATE_ACCESSION_MISMATCH")


def _synthetic_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                        case: dict, bad_vector: str | None = None) -> tuple[Path, Path]:
    import finembed.config
    import finembed.sensitivity_preflight

    root = tmp_path / "SYNTHETIC_TEST_ONLY_repo"
    contract = root / "docs/SMALL_CHUNK_METRICS.md"
    contract.parent.mkdir(parents=True)
    contract.write_text("SYNTHETIC_TEST_ONLY metrics\n", encoding="utf-8")
    monkeypatch.setattr(join, "ROUND4_METRICS_CONTRACT_SHA256", join.sha256(contract))
    config = root / "configs/smoke.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("SYNTHETIC_TEST_ONLY config\n", encoding="utf-8")
    config_value = SimpleNamespace(provider="CPUExecutionProvider", body_tokens=128,
                                   normalize_chunks=True, normalize_documents=True,
                                   source_sha256=_digest("SYNTHETIC_TEST_ONLY config"))
    model = {"model_id": "SYNTHETIC_TEST_ONLY_model", "revision": "synthetic-revision",
             "tokenizer_revision": "synthetic-tokenizer-revision",
             "files": {"synthetic-file": {"sha256": _digest("synthetic model bytes")}}}
    monkeypatch.setattr(finembed.config, "load_config", lambda path: config_value)
    monkeypatch.setattr(finembed.sensitivity_preflight, "inspect_model_files",
                        lambda loaded: model)
    run = tmp_path / "SYNTHETIC_TEST_ONLY_round4"
    jobs = []
    for ticker in join.FIRMS:
        row = case["filings"][ticker]
        for length in (118, 123, 128, 133, 138):
            job = {"ticker": ticker, "body_tokens_per_chunk": length,
                   "status": "COMPLETE", "accession": row["accession"],
                   "item1_sha256": row["item1_sha256"], "files": {}}
            if length == 128:
                vector_path = run / f"vectors/128/{ticker}_equal_chunks.npy"
                vector_path.parent.mkdir(parents=True, exist_ok=True)
                if ticker == "AAPL" and bad_vector == "nan":
                    vector = np.full(384, np.nan, dtype=np.float64)
                elif ticker == "AAPL" and bad_vector == "zero":
                    vector = np.zeros(384, dtype=np.float64)
                elif ticker == "AAPL" and bad_vector == "shape":
                    vector = np.ones(383, dtype=np.float64)
                elif ticker == "AAPL" and bad_vector == "dtype":
                    vector = np.ones(384, dtype=np.float32)
                elif ticker == "AAPL" and bad_vector == "object":
                    vector = np.array(["SYNTHETIC_TEST_ONLY"] * 384, dtype=object)
                else:
                    vector = np.ones(384, dtype=np.float64)
                np.save(vector_path, vector)
                job["files"]["equal_chunks"] = {
                    "path": f"vectors/128/{ticker}_equal_chunks.npy",
                    "shape": [384], "dtype": "float64", "sha256": join.sha256(vector_path)}
            jobs.append(job)
    manifest = _json(run / "manifest.json", {
        "kind": "SMALL_CHUNK_PROBE_RUN", "status": "COMPLETE",
        "evidence_class": "SYNTHETIC_TEST_ONLY", "failures": [],
        "candidate_sha256": join.ROUND4_CANDIDATE_SHA256,
        "metric_contract_sha256": join.ROUND4_METRICS_CONTRACT_SHA256,
        "logical_jobs_completed": 25, "company_vectors_completed": 50,
        "input_versions": [{**case["filings"][ticker], "form": "10-K",
                            "section": "ITEM_1_BUSINESS"} for ticker in join.FIRMS],
        "model_identity": {"model_id": model["model_id"], "revision": model["revision"],
                           "tokenizer_revision": model["tokenizer_revision"],
                           "provider": "CPUExecutionProvider",
                           "reference_config_sha256": config_value.source_sha256,
                           "model_file_sha256": {name: entry["sha256"]
                                                 for name, entry in model["files"].items()}},
        "jobs": jobs})
    monkeypatch.setattr(join, "ROUND4_MANIFEST_SHA256", join.sha256(manifest))
    manifest.with_name("manifest.sha256").write_text(
        join.ROUND4_MANIFEST_SHA256 + "\n", encoding="ascii")
    return manifest, root


def test_synthetic_vector_records_pass_without_encoder(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch,
                                                       synthetic_case: dict):
    manifest, root = _synthetic_manifest(tmp_path, monkeypatch, synthetic_case)
    _, vectors = join.verify_round4_vectors(
        manifest, synthetic_case["filings"], root,
        expected_evidence_class="SYNTHETIC_TEST_ONLY")
    assert set(vectors) == set(join.FIRMS)
    assert all(value["shape"] == [384] and value["dtype"] == "float64"
               for value in vectors.values())


def test_synthetic_manifest_cannot_pass_the_real_model_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic_case: dict):
    manifest, root = _synthetic_manifest(tmp_path, monkeypatch, synthetic_case)
    with pytest.raises(join.JoinError) as caught:
        join.verify_round4_vectors(manifest, synthetic_case["filings"], root)
    assert (caught.value.category, caught.value.code) == (
        "ROUND4_MANIFEST", "RUN_IDENTITY_MISMATCH")


@pytest.mark.parametrize("problem,code", [
    ("nan", "INVALID_ARRAY"), ("zero", "INVALID_ARRAY"),
    ("shape", "INVALID_ARRAY"),
    ("dtype", "INVALID_ARRAY"), ("object", "NUMPY_UNREADABLE"),
])
def test_invalid_synthetic_vector_is_rejected(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch,
                                              synthetic_case: dict,
                                              problem: str, code: str):
    manifest, root = _synthetic_manifest(tmp_path, monkeypatch, synthetic_case, problem)
    with pytest.raises(join.JoinError) as caught:
        join.verify_round4_vectors(
            manifest, synthetic_case["filings"], root,
            expected_evidence_class="SYNTHETIC_TEST_ONLY")
    assert (caught.value.category, caught.value.code) == ("VECTOR", code)


def test_vector_tamper_is_rejected_before_numpy_load(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch,
                                                     synthetic_case: dict):
    manifest, root = _synthetic_manifest(tmp_path, monkeypatch, synthetic_case)
    vector_path = manifest.parent / "vectors/128/AAPL_equal_chunks.npy"
    with vector_path.open("ab") as handle:
        handle.write(b"SYNTHETIC_TEST_ONLY tamper")
    with pytest.raises(join.JoinError) as caught:
        join.verify_round4_vectors(
            manifest, synthetic_case["filings"], root,
            expected_evidence_class="SYNTHETIC_TEST_ONLY")
    assert (caught.value.category, caught.value.code) == ("VECTOR", "HASH_MISMATCH")


def test_synthetic_model_file_hash_change_is_rejected(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch,
                                                      synthetic_case: dict):
    import finembed.sensitivity_preflight

    manifest, root = _synthetic_manifest(tmp_path, monkeypatch, synthetic_case)
    monkeypatch.setattr(finembed.sensitivity_preflight, "inspect_model_files",
                        lambda config: {"model_id": "SYNTHETIC_TEST_ONLY_model",
                                        "revision": "synthetic-revision",
                                        "tokenizer_revision": "synthetic-tokenizer-revision",
                                        "files": {"synthetic-file": {"sha256": _digest("changed")}}})
    with pytest.raises(join.JoinError) as caught:
        join.verify_round4_vectors(
            manifest, synthetic_case["filings"], root,
            expected_evidence_class="SYNTHETIC_TEST_ONLY")
    assert (caught.value.category, caught.value.code) == (
        "MODEL", "MANIFEST_IDENTITY_MISMATCH")


def test_existing_different_index_is_not_overwritten(synthetic_case: dict):
    path = synthetic_case["output"]
    path.write_text("SYNTHETIC_TEST_ONLY previous result\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(join.JoinError) as caught:
        join._write_atomic_no_overwrite(path, _build(synthetic_case))
    assert (caught.value.category, caught.value.code) == ("OUTPUT", "DIFFERENT_RESULT_EXISTS")
    assert path.read_bytes() == before


def test_output_must_be_git_ignored_and_untracked(tmp_path: Path):
    root = tmp_path / "SYNTHETIC_TEST_ONLY_repo"
    root.mkdir()
    (root / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    target = root / "artifacts" / "SYNTHETIC_TEST_ONLY_index.json"
    join._ignored_output(root, target)
    target.parent.mkdir()
    target.write_text("SYNTHETIC_TEST_ONLY\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-f", "artifacts/SYNTHETIC_TEST_ONLY_index.json"],
                   check=True, capture_output=True)
    with pytest.raises(join.JoinError) as caught:
        join._ignored_output(root, target)
    assert caught.value.category == "OUTPUT"
