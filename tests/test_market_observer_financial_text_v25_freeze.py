"""Small adversarial checks for the private v2.5 reference freeze."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.market_observer_financial_text_v25.freeze_reference import validate_document_hashes


class FreezeReferenceTests(unittest.TestCase):
    def test_source_bytes_and_two_ledgers_must_agree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / "saved.html"
            source.write_bytes(b"<table><tr><td>2</td></tr></table>")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            document = {
                "document_id": "test-doc",
                "event_group_id": "test-event",
                "announcement_date": "2025-06-01",
                "raw_body_path": "saved.html",
                "raw_body_sha256": digest,
            }
            validate_document_hashes(repo, [document], [document])
            source.write_bytes(b"modified source")
            with self.assertRaisesRegex(ValueError, "file hash mismatch"):
                validate_document_hashes(repo, [document], [document])

    def test_out_of_window_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / "saved.html"
            source.write_bytes(b"source")
            document = {
                "document_id": "test-doc",
                "event_group_id": "test-event",
                "announcement_date": "2026-04-01",
                "raw_body_path": "saved.html",
                "raw_body_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
            with self.assertRaisesRegex(ValueError, "outside training window"):
                validate_document_hashes(repo, [document], [document])


if __name__ == "__main__":
    unittest.main()
