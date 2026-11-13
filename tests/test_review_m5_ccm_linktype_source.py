"""SYNTHETIC_TEST_ONLY: CCM source-code matching and saved-output checks."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import review_m5_ccm_linktype_source as source_review  # noqa: E402


def synthetic_ledger(path: Path, raw_codes: list[str], *, binding: str = "SYNTHETIC_TEST_ONLY") -> None:
    rows = []
    for ordinal, code in enumerate(raw_codes, 1):
        rows.append({"schema": "m5_ccm_targeted_private_row_v1",
                     "source_binding_id": binding,
                     "source_row_ordinal": ordinal,
                     "linktype_other": ordinal <= 4,
                     "raw_linktype": code,
                     "normalized_linktype": code.strip().upper(),
                     "type_category": "UNDEFINED_LINKTYPE_1" if ordinal <= 4 else None,
                     "real_security_id": "SYNTHETIC_TEST_ONLY"})
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                            for row in rows), encoding="utf-8")


def fake_saved(base: Path) -> dict:
    return {"targeted_output": base,
            "ledger": base / "synthetic_target_ledger.jsonl",
            "prior_source_binding_id": "SYNTHETIC_TEST_ONLY",
            "targeted_ledger_sha256": "SYNTHETIC_TEST_ONLY",
            "prior_index_sha256": "SYNTHETIC_TEST_ONLY",
            "source_zip_sha256": "SYNTHETIC_TEST_ONLY",
            "source_csv_sha256": "SYNTHETIC_TEST_ONLY",
            "targeted_summary_sha256": "SYNTHETIC_TEST_ONLY",
            "targeted_receipt_sha256": "SYNTHETIC_TEST_ONLY"}


def fake_source(base: Path) -> dict:
    return {"packet_dir": base, "outer_zip": base / "packet.zip",
            "extra_images": base, "packet_sha256": "SYNTHETIC_TEST_ONLY",
            "packet_manifest_sha256": "SYNTHETIC_TEST_ONLY",
            "dictionary_json_sha256": "SYNTHETIC_TEST_ONLY",
            "dictionary_image_sha256": "SYNTHETIC_TEST_ONLY",
            "end_help_json_sha256": "SYNTHETIC_TEST_ONLY",
            "end_help_image_sha256": "SYNTHETIC_TEST_ONLY",
            "partial_page_json_sha256": "SYNTHETIC_TEST_ONLY",
            "partial_page_image_sha256": "SYNTHETIC_TEST_ONLY",
            "additional_user_image_sha256": dict.fromkeys(source_review.USER_IMAGE_SHA256,
                                                       "SYNTHETIC_TEST_ONLY")}


def fake_code() -> dict:
    return {"source_review_code_sha256": "SYNTHETIC_TEST_ONLY",
            "targeted_code_sha256": "SYNTHETIC_TEST_ONLY",
            "candidate_sha256": "SYNTHETIC_TEST_ONLY"}


class SourceReviewSyntheticTests(unittest.TestCase):
    def test_four_exact_lo_matches_raw_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ledger = base / "synthetic_target_ledger.jsonl"
            synthetic_ledger(ledger, ["LO", " lo ", "Lo", "\tlo\t", "LC"])
            counts, private = source_review.compare_four(ledger, "SYNTHETIC_TEST_ONLY",
                                                         expected_rows=5)
            self.assertEqual(counts["metadata_code_lo_match_source_records"], 4)
            self.assertEqual(counts["unmatched_linktype_other_source_records"], 0)
            self.assertTrue(counts["all_four_metadata_code_lo"])
            self.assertEqual([row["raw_linktype"] for row in private["source_records"]],
                             ["LO", " lo ", "Lo", "\tlo\t"])
            safe = source_review.safe_summary(counts, fake_saved(base),
                                              fake_source(base), fake_code())
            self.assertEqual(safe["metadata_code_definition"]["code"], "LO")
            self.assertFalse(safe["metadata_code_definition"]["issue_level_security_bridge_accepted"])
            self.assertNotIn("SYNTHETIC_TEST_ONLY", json.dumps(safe["metadata_code_definition"]))

    def test_unknown_token_stays_private_and_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ledger = base / "synthetic_target_ledger.jsonl"
            synthetic_ledger(ledger, ["LO", "LO", " ZZ ", "LO", "LC"])
            counts, private = source_review.compare_four(ledger, "SYNTHETIC_TEST_ONLY",
                                                         expected_rows=5)
            safe = source_review.safe_summary(counts, fake_saved(base),
                                              fake_source(base), fake_code())
            self.assertEqual((counts["metadata_code_lo_match_source_records"],
                              counts["unmatched_linktype_other_source_records"]), (3, 1))
            self.assertEqual(private["source_records"][2]["normalized_linktype"], "ZZ")
            self.assertNotIn("ZZ", json.dumps(safe))
            self.assertNotIn("source_row_ordinal", json.dumps(safe))

    def test_count_and_binding_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ledger = base / "synthetic_target_ledger.jsonl"
            synthetic_ledger(ledger, ["LO", "LO", "LO", "LO", "LC"])
            with self.assertRaisesRegex(source_review.SourceReviewError,
                                        "^TARGET_LEDGER_ENTRY_COUNT_MISMATCH$"):
                source_review.compare_four(ledger, "SYNTHETIC_TEST_ONLY", expected_rows=6)
            with self.assertRaisesRegex(source_review.SourceReviewError,
                                        "^TARGET_LEDGER_OTHER_COUNT_MISMATCH$"):
                source_review.compare_four(ledger, "SYNTHETIC_TEST_ONLY", expected_rows=5,
                                           expected_other=3)
            with self.assertRaisesRegex(source_review.SourceReviewError,
                                        "^TARGET_LEDGER_ROW_IDENTITY_MISMATCH$"):
                source_review.compare_four(ledger, "WRONG_BINDING", expected_rows=5)

    def test_verify_rederives_and_rejects_manifest_rewritten_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ledger = base / "synthetic_target_ledger.jsonl"
            synthetic_ledger(ledger, ["LO", "LO", "LO", "LO"])
            bound = fake_saved(base)
            source = fake_source(base)
            out = base / "new_output"
            original_compare = source_review.compare_four

            def small_compare(path, binding):
                return original_compare(path, binding, expected_rows=4)

            with patch.object(source_review, "ignored_output", side_effect=lambda p: p), \
                 patch.object(source_review, "bind_saved", return_value=bound), \
                 patch.object(source_review, "bind_packet", return_value=source), \
                 patch.object(source_review, "code_identity", side_effect=fake_code), \
                 patch.object(source_review, "compare_four", side_effect=small_compare):
                safe = source_review.run(base, base, base / "packet.zip", base, out)
                self.assertEqual(source_review.verify(out), safe)
                safe_path = out / source_review.SAFE_NAME
                tampered = json.loads(safe_path.read_text(encoding="utf-8"))
                tampered["metadata_code_lo_match_source_records"] = 0
                safe_path.write_bytes(source_review.json_bytes(tampered))
                manifest_path = out / source_review.MANIFEST_NAME
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["files"][source_review.SAFE_NAME] = {
                    "sha256": source_review.sha256_file(safe_path),
                    "bytes": safe_path.stat().st_size}
                manifest_path.write_bytes(source_review.json_bytes(manifest))
                with self.assertRaisesRegex(source_review.SourceReviewError,
                                            "^SOURCE_REVIEW_SAVED_RECOMPUTE_MISMATCH$"):
                    source_review.verify(out)


if __name__ == "__main__":
    unittest.main()
