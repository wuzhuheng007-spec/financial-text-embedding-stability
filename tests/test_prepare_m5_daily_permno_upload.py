"""SYNTHETIC_TEST_ONLY checks for the offline WRDS upload-list converter."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_m5_daily_permno_upload as daily
from audit_m5_wrds_intake import AuditError


def synthetic_seed(values: list[str]) -> dict:
    return {"schema": "m5_daily_seed_identifiers_private_v1",
            "scope": "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT",
            "source_prior_index_sha256": "synthetic-index-hash",
            "permno_count": len(values), "permnos": values,
            "successor_ids_auto_added": False}


def synthetic_index(values: list[str]) -> dict:
    return {"accepted": False,
            "starting_securities": [{"permno": value} for value in values]}


def test_synthetic_seed_matches_exact_starting_set():
    seed = synthetic_seed(["101", "102", "201"])
    index = synthetic_index(["201", "101", "102"])
    assert daily.verify_seed_relationship(
        seed, index, {"101", "102", "201"}, "synthetic-index-hash") == {
            "101", "102", "201"}


@pytest.mark.parametrize("seed_ids,index_ids,snapshot_ids", [
    (["101", "101"], ["101"], {"101"}),
    (["101", "102"], ["101", "103"], {"101", "102"}),
    (["101", "102"], ["101", "102"], {"101", "103"}),
    (["0101"], ["101"], {"101"}),
    (["101.0"], ["101"], {"101"}),
    (["102", "101"], ["101", "102"], {"101", "102"}),
    (["101", "102"], ["101", "101"], {"101", "102"}),
])
def test_synthetic_bad_starting_set_rejected(seed_ids, index_ids, snapshot_ids):
    with pytest.raises(AuditError):
        daily.verify_seed_relationship(synthetic_seed(seed_ids),
                                       synthetic_index(index_ids), snapshot_ids,
                                       "synthetic-index-hash")


def test_synthetic_prior_index_and_successor_flags_rejected():
    seed = synthetic_seed(["101"])
    seed["successor_ids_auto_added"] = True
    with pytest.raises(AuditError, match="SEED_SOURCE_BINDING_INVALID"):
        daily.verify_seed_relationship(seed, synthetic_index(["101"]),
                                       {"101"}, "synthetic-index-hash")
    seed["successor_ids_auto_added"] = False
    index = synthetic_index(["101"])
    index["accepted"] = True
    with pytest.raises(AuditError, match="PRIOR_INDEX_UNEXPECTEDLY_ACCEPTED"):
        daily.verify_seed_relationship(seed, index, {"101"}, "synthetic-index-hash")


def test_synthetic_upload_is_ascii_decimal_and_round_trips(tmp_path):
    ids = {"101", "5", "200"}
    data = daily.upload_bytes(ids)
    assert data == b"5\n101\n200\n"
    path = tmp_path / daily.UPLOAD_NAME
    path.write_bytes(data)
    assert daily.readback_upload(path, ids, hashlib.sha256(data).hexdigest()) == 3


@pytest.mark.parametrize("data", [
    b"101\n200", b"\xef\xbb\xbf101\n200\n", b"101\r\n200\r\n",
    b"101\n101\n", b"101\n201\n", b"101\n2.0\n",
    b"101,200\n", b"0\n101\n", b"101\n\n", b"\xff\n",
])
def test_synthetic_bad_upload_rejected(tmp_path, data):
    path = tmp_path / daily.UPLOAD_NAME
    path.write_bytes(data)
    with pytest.raises(AuditError):
        daily.readback_upload(path, {"101", "200"}, hashlib.sha256(data).hexdigest())


def test_synthetic_source_hash_change_rejected(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"original synthetic bytes")
    digest = daily.sha256_file(source)
    daily.check_hash(source, digest, "SYNTHETIC_SOURCE_CHANGED")
    source.write_bytes(b"changed synthetic bytes")
    with pytest.raises(AuditError, match="SYNTHETIC_SOURCE_CHANGED"):
        daily.check_hash(source, digest, "SYNTHETIC_SOURCE_CHANGED")


def test_synthetic_import_shadow_rejected(monkeypatch):
    monkeypatch.setattr(daily.wrds_module, "__file__", "C:/elsewhere/audit_m5_wrds_intake.py")
    with pytest.raises(AuditError, match="IMPORTED_AUDIT_CODE_PATH_MISMATCH"):
        daily.verify_import_sources()


def make_saved(tmp_path: Path, bundle: dict, code_hash: str, doc_hash: str) -> tuple[Path, str]:
    output = tmp_path / "m5_daily_download_prepare_20261111_v2"
    output.mkdir()
    upload = daily.upload_bytes(bundle["ids"])
    upload_hash = hashlib.sha256(upload).hexdigest()
    plan = daily.plan_for(upload_hash, len(bundle["ids"]))
    registry = {"schema": "m5_daily_upload_registry_private_v1",
                "scope": "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT",
                "status": "M5_DAILY_PERMNO_UPLOAD_READY",
                "formal_m5_cohort_selected": False, "wrds_queries_submitted": 0,
                "conversion_code_sha256": code_hash, "download_doc_sha256": doc_hash,
                "input_bindings": {key: value for key, value in bundle.items() if key != "ids"},
                "upload_sha256": upload_hash, "upload_file": daily.UPLOAD_NAME,
                "encoding": "ASCII", "newline": "LF_FINAL_NEWLINE",
                "plan_sha256": hashlib.sha256(daily.json_bytes(plan)).hexdigest(),
                "line_count": len(bundle["ids"]), "unique_count": len(bundle["ids"]),
                "set_equal_to_seed_index_and_snapshot": True}
    (output / daily.UPLOAD_NAME).write_bytes(upload)
    (output / daily.PLAN_NAME).write_bytes(daily.json_bytes(plan))
    (output / daily.REGISTRY_NAME).write_bytes(daily.json_bytes(registry))
    return output, upload_hash


def test_synthetic_saved_readback_rejects_source_and_output_changes(tmp_path):
    bundle = {"ids": {"101", "200"}, "seed_sha256": "synthetic-seed"}
    output, upload_hash = make_saved(tmp_path, bundle, "synthetic-code", "synthetic-doc")
    daily.verify_saved(output, bundle, "synthetic-code", "synthetic-doc", upload_hash)
    with pytest.raises(AuditError, match="UPLOAD_SOURCE_BINDING_MISMATCH"):
        daily.verify_saved(output, {**bundle, "seed_sha256": "changed"},
                           "synthetic-code", "synthetic-doc", upload_hash)
    with pytest.raises(AuditError, match="UPLOAD_REGISTRY_INVALID"):
        daily.verify_saved(output, bundle, "changed-code", "synthetic-doc", upload_hash)
    (output / daily.UPLOAD_NAME).write_bytes(b"101\n201\n")
    with pytest.raises(AuditError, match="UPLOAD_SET_MISMATCH"):
        daily.verify_saved(output, bundle, "synthetic-code", "synthetic-doc", upload_hash)


def test_synthetic_publish_checks_staging_and_preserves_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "ensure_ignored", lambda _: None)
    bundle = {"ids": {"101", "200"}, "seed_sha256": "synthetic-seed"}
    upload = daily.upload_bytes(bundle["ids"])
    upload_hash = hashlib.sha256(upload).hexdigest()
    plan = daily.plan_for(upload_hash, 2)
    registry = {"schema": "m5_daily_upload_registry_private_v1",
                "scope": "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT",
                "status": "M5_DAILY_PERMNO_UPLOAD_READY",
                "formal_m5_cohort_selected": False, "wrds_queries_submitted": 0,
                "conversion_code_sha256": "synthetic-code",
                "download_doc_sha256": "synthetic-doc",
                "input_bindings": {"seed_sha256": "synthetic-seed"},
                "upload_sha256": upload_hash, "upload_file": daily.UPLOAD_NAME,
                "encoding": "ASCII", "newline": "LF_FINAL_NEWLINE",
                "plan_sha256": hashlib.sha256(daily.json_bytes(plan)).hexdigest(),
                "line_count": 2, "unique_count": 2,
                "set_equal_to_seed_index_and_snapshot": True}
    output = tmp_path / "m5_daily_download_prepare_20261111_v1"
    daily.publish(output, upload, plan, registry, bundle,
                  "synthetic-code", "synthetic-doc")
    daily.verify_saved(output, bundle, "synthetic-code", "synthetic-doc", upload_hash)
    original = (output / daily.UPLOAD_NAME).read_bytes()
    with pytest.raises(AuditError, match="OUTPUT_ALREADY_EXISTS"):
        daily.publish(output, upload, plan, registry, bundle,
                      "synthetic-code", "synthetic-doc")
    assert (output / daily.UPLOAD_NAME).read_bytes() == original


def test_synthetic_existing_different_version_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "ensure_ignored", lambda _: None)
    bundle = {"ids": {"101"}, "seed_sha256": "synthetic-seed"}
    base = tmp_path / "m5_daily_download_prepare_20261111_v1"
    base.mkdir()
    (base / daily.UPLOAD_NAME).write_bytes(b"unexpected synthetic bytes")
    expected_hash = hashlib.sha256(daily.upload_bytes(bundle["ids"])).hexdigest()
    selected, reused = daily.choose_output(base, bundle, "synthetic-code",
                                           "synthetic-doc", expected_hash)
    assert selected.name.endswith("_v2") and reused is False
    assert (base / daily.UPLOAD_NAME).read_bytes() == b"unexpected synthetic bytes"
    output, _ = make_saved(tmp_path, bundle, "synthetic-code", "synthetic-doc")
    selected, reused = daily.choose_output(base, bundle, "synthetic-code",
                                           "synthetic-doc", expected_hash)
    assert selected == output and reused is True


def test_synthetic_window_conflict_rejected(tmp_path):
    doc = tmp_path / "download.md"
    doc.write_text("| 6. Next: StkDlySecurityData 2019-11-01 through 2026-02-27 |\n",
                   encoding="utf-8")
    with pytest.raises(AuditError, match="DAILY_BUFFER_DOCUMENT_CONFLICT"):
        daily.verify_download_window(doc)


def test_synthetic_window_row_matches_exact_buffer(tmp_path):
    doc = tmp_path / "download.md"
    doc.write_text("| 6. Next: StkDlySecurityData 2019-11-01 through 2026-02-28 |\n",
                   encoding="utf-8")
    assert daily.verify_download_window(doc) == daily.sha256_file(doc)


def test_synthetic_failure_keeps_private_fixed_code(tmp_path, monkeypatch):
    (tmp_path / "artifacts").mkdir()
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(daily, "ensure_ignored", lambda _: None)
    daily.save_rejection("SYNTHETIC_SOURCE_MISMATCH", None)
    rejected = tmp_path / "artifacts" / "m5_daily_download_prepare_20261111_rejected_v1"
    record = daily.read_json(rejected / "m5_daily_upload_rejection_private.json")
    assert record["issue_code"] == "SYNTHETIC_SOURCE_MISMATCH"
    assert record["command_exit_code"] == 2
    assert record["wrds_queries_submitted"] == 0


def test_synthetic_segments_cover_buffer_without_overlap():
    from datetime import date, timedelta
    segments = [(date.fromisoformat(start), date.fromisoformat(end))
                for start, end in daily.SEGMENTS]
    assert segments[0][0] == daily.WINDOW_START
    assert segments[-1][1] == daily.WINDOW_END
    assert all(left[1] + timedelta(days=1) == right[0]
               for left, right in zip(segments, segments[1:]))
