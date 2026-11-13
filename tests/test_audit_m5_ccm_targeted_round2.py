"""SYNTHETIC_TEST_ONLY: targeted CCM representation and saved-output checks."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import audit_m5_ccm_query11905330 as prior  # noqa: E402
import audit_m5_ccm_targeted_round2 as target  # noqa: E402


def synthetic_row(**overrides: str) -> dict[str, str]:
    values = {name: "" for name in prior.EXPECTED}
    values.update({"gvkey": "SYNTHETIC_GVKEY", "liid": "SYNTHETIC_CLASS",
                   "linkdt": "2020-01-02", "linkenddt": "2020-12-31",
                   "linkprim": "P", "linktype": "LC", "lpermco": "1",
                   "lpermno": "2", "conm": "Synthetic Corporation",
                   "tic": "SYNTHETIC_TICKER", "cusip": "SYNTHETIC_CUSIP",
                   "cik": "SYNTHETIC_CIK", "sic": "1111", "naics": "111111",
                   "priusa": "SYNTHETIC_HEADER"})
    values.update(overrides)
    return values


def make_saved_index(base: Path) -> tuple[Path, dict[str, int]]:
    rows = [synthetic_row(),
            synthetic_row(linktype=" XY "),
            synthetic_row(linktype=" xy ", linkenddt="E"),
            synthetic_row(linkenddt="X"),
            synthetic_row(linkenddt=".E")]
    source = base / "synthetic.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(prior.EXPECTED)
        writer.writerows([[row[name] for name in prior.EXPECTED] for row in rows])
    index = base / prior.INDEX_NAME
    prior.scan_csv(source, index, base / "synthetic_exceptions.jsonl",
                   source_binding_id="SYNTHETIC_TEST_ONLY",
                   seed_ids=None, page_rows=len(rows),
                   source_zip_sha256="SYNTHETIC_TEST_ONLY",
                   source_csv_sha256="SYNTHETIC_TEST_ONLY")
    return index, {name: i for i, name in enumerate(prior.EXPECTED)}


def scan_synthetic(index: Path, ledger: Path, mapping: dict[str, int],
                   *, expected_other: int = 2,
                   expected_unparsed: int = 2) -> dict:
    return target.scan_index(index, ledger, mapping,
                             expected_rows=5,
                             expected_other=expected_other,
                             expected_unparsed=expected_unparsed,
                             expected_source_zip="SYNTHETIC_TEST_ONLY",
                             expected_source_csv="SYNTHETIC_TEST_ONLY",
                             expected_binding_id="SYNTHETIC_TEST_ONLY")


def fake_bound(base: Path, index: Path, mapping: dict[str, int]) -> dict:
    return {"saved": base, "mapping": mapping,
            "source_zip_sha256": "SYNTHETIC_TEST_ONLY",
            "source_csv_sha256": "SYNTHETIC_TEST_ONLY",
            "source_binding_id": "SYNTHETIC_TEST_ONLY",
            "index_sha256": target.digest(index),
            "index_bytes": index.stat().st_size,
            "prior_manifest_sha256": "SYNTHETIC_TEST_ONLY",
            "prior_registry_sha256": "SYNTHETIC_TEST_ONLY",
            "prior_summary_sha256": "SYNTHETIC_TEST_ONLY"}


def fake_code() -> dict:
    return {"targeted_script_sha256": target.digest(Path(target.__file__)),
            "prior_intake_script_sha256": "SYNTHETIC_TEST_ONLY",
            "decoder_script_sha256": "SYNTHETIC_TEST_ONLY",
            "candidate_sha256": "SYNTHETIC_TEST_ONLY",
            "config_sha256": "SYNTHETIC_TEST_ONLY"}


class TargetedSyntheticTests(unittest.TestCase):
    def test_overlap_raw_preservation_and_safe_suppression(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            index, mapping = make_saved_index(base)
            ledger = base / "targeted.jsonl"
            safe = scan_synthetic(index, ledger, mapping)
            private = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual((safe["source_record_count"], safe["linktype_other_source_records"],
                              safe["unparsed_end_source_records"], safe["overlap_source_records"],
                              safe["union_target_source_records"]), (5, 2, 2, 1, 3))
            self.assertEqual(len(private), 3)
            self.assertEqual(safe["linktype_other_category_frequency"], {"UNDEFINED_LINKTYPE_1": 2})
            self.assertEqual(safe["unparsed_end_category_frequency"], {"SINGLE_ASCII_LETTER": 2})
            self.assertEqual(safe["raw_linkenddt_preserved_source_records"], 2)
            self.assertEqual(private[1]["raw_linktype"], " xy ")
            self.assertEqual(private[1]["normalized_linktype"], "XY")
            self.assertEqual(private[1]["raw_linkenddt"], "E")
            self.assertEqual(private[1]["raw_cells_if_linktype_other"][mapping["cik"]],
                             "SYNTHETIC_CIK")
            self.assertNotIn("SYNTHETIC_CIK", json.dumps(safe))
            self.assertNotIn(" xy ", json.dumps(safe))
            self.assertNotIn("\"E\"", json.dumps(safe))
            self.assertTrue(all(v["source_definition_status"] == "NOT_SUPPLIED_SOURCE_BOUND"
                                for v in safe["linktype_other_categories"].values()))
            self.assertEqual(prior.parse_endpoint(".E")["status"],
                             "SENTINEL_UNKNOWN_ENDPOINT_SEMANTICS")
            self.assertEqual(prior.parse_endpoint("E")["status"],
                             "UNPARSED_DATE_REPRESENTATION")
            self.assertEqual(safe["supported_open_endpoint_rows"], 0)

    def test_expected_counts_fail_closed_without_repair(self):
        for changed, code in (({"expected_other": 3}, "LINKTYPE_OTHER_COUNT_MISMATCH"),
                              ({"expected_unparsed": 3}, "UNPARSED_ENDPOINT_COUNT_MISMATCH")):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                index, mapping = make_saved_index(base)
                with self.assertRaisesRegex(target.TargetError, f"^{code}$"):
                    scan_synthetic(index, base / "targeted.jsonl", mapping, **changed)

    def test_source_hash_and_saved_parse_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            index, mapping = make_saved_index(base)
            lines = index.read_text(encoding="utf-8").splitlines()
            row = json.loads(lines[1])
            row["source_csv_sha256"] = "WRONG_SYNTHETIC_HASH"
            lines[1] = json.dumps(row)
            index.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(target.TargetError, "^INDEX_ROW_IDENTITY_MISMATCH$"):
                scan_synthetic(index, base / "targeted.jsonl", mapping)

    def test_independent_verify_rederives_private_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            index, mapping = make_saved_index(base)
            bound = fake_bound(base, index, mapping)
            config = dict(target.CONFIG, expected_rows=5,
                          expected_linktype_other=2, expected_unparsed_end=2)
            out = base / "new_output"
            with patch.object(target.prior, "ensure_ignored", side_effect=lambda p: p), \
                 patch.object(target, "_bound_identity", return_value=bound), \
                 patch.object(target, "code_identity", side_effect=fake_code), \
                 patch.object(target, "CONFIG", config):
                safe = target.run(base, out)
                self.assertEqual(target.verify(out), safe)
                private = out / target.PRIVATE_NAME
                private.write_bytes(private.read_bytes() + b"SYNTHETIC_TAMPER\n")
                manifest_path = out / target.MANIFEST_NAME
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["files"][target.PRIVATE_NAME] = {
                    "sha256": target.digest(private), "bytes": private.stat().st_size}
                manifest_path.write_bytes(prior.json_bytes(manifest))
                with self.assertRaisesRegex(target.TargetError, "^TARGETED_RECOMPUTE_MISMATCH$"):
                    target.verify(out)

    def test_independent_verify_rejects_safe_summary_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            index, mapping = make_saved_index(base)
            bound = fake_bound(base, index, mapping)
            config = dict(target.CONFIG, expected_rows=5,
                          expected_linktype_other=2, expected_unparsed_end=2)
            out = base / "new_output"
            with patch.object(target.prior, "ensure_ignored", side_effect=lambda p: p), \
                 patch.object(target, "_bound_identity", return_value=bound), \
                 patch.object(target, "code_identity", side_effect=fake_code), \
                 patch.object(target, "CONFIG", config):
                target.run(base, out)
                safe_path = out / target.SAFE_NAME
                safe = json.loads(safe_path.read_text(encoding="utf-8"))
                safe["overlap_source_records"] = 0
                safe_path.write_bytes(prior.json_bytes(safe))
                manifest_path = out / target.MANIFEST_NAME
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["files"][target.SAFE_NAME] = {
                    "sha256": target.digest(safe_path), "bytes": safe_path.stat().st_size}
                manifest_path.write_bytes(prior.json_bytes(manifest))
                with self.assertRaisesRegex(target.TargetError, "^TARGETED_RECOMPUTE_MISMATCH$"):
                    target.verify(out)


if __name__ == "__main__":
    unittest.main()
