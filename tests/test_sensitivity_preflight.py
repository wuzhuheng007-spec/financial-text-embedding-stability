"""Synthetic software checks only; no encoder, retrieval or financial result."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import numpy as np
import pytest

from finembed.config import load_config
from finembed.model import Chunk, ModelError, aggregate, chunk_text
from finembed.run import sha256
from finembed import sensitivity_preflight as preflight


class LetterTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return SimpleNamespace(ids=list(range(1, len(text) + 1)),
                               offsets=[(i, i + 1) for i in range(len(text))])


def test_chunk_text_covers_complete_body_and_rejects_invalid_length():
    text = "abcdefghij"
    chunks = chunk_text(LetterTokenizer(), text, 4, 2, 6)
    assert [(c.token_start, c.token_end_exclusive) for c in chunks] == [(0, 4), (4, 8), (8, 10)]
    assert [len(c.token_ids) for c in chunks] == [4, 4, 2]
    assert [c.source_span_sha256 for c in chunks] == [sha256(text[a:b].encode()) for a, b in ((0, 4), (4, 8), (8, 10))]
    with pytest.raises(ModelError, match="positive integer"):
        chunk_text(LetterTokenizer(), text, 0, 2, 6)
    with pytest.raises(ModelError, match="exceeds"):
        chunk_text(LetterTokenizer(), text, 5, 2, 6)
    with pytest.raises(ModelError, match="No body tokens"):
        chunk_text(LetterTokenizer(), "", 4, 2, 6)


def test_pooling_weights_match_aggregate_and_reject_empty():
    chunks = [Chunk(0, 4, 0, 4, (1, 2, 3, 4), "x"),
              Chunk(4, 5, 4, 5, (5,), "y")]
    definitions = preflight.pooling_weights(chunks)
    assert definitions["equal_chunks"]["tail_weight"] == pytest.approx(0.5)
    assert definitions["body_token_weighted"]["tail_weight"] == pytest.approx(0.2)
    vectors = np.array([[3., 0.], [0., 4.]])
    equal = aggregate(vectors, True, True)
    weighted = aggregate(vectors, True, True, weights=[4, 1])
    assert equal == pytest.approx(np.array([1., 1.]) / np.sqrt(2))
    assert weighted == pytest.approx(np.array([4., 1.]) / np.sqrt(17))
    with pytest.raises(ModelError, match="nonempty"):
        preflight.pooling_weights([])


def test_candidate_is_not_an_inference_config():
    candidate = Path(__file__).parents[1] / "configs" / "small_chunk_probe_round2.json"
    assert preflight.load_candidate(candidate)["researcher_accepted"] is False
    with pytest.raises(ValueError, match="plan-only"):
        load_config(candidate)


def test_synthetic_preflight_never_constructs_encoder_and_detects_changed_binding(monkeypatch, tmp_path):
    root = tmp_path
    (root / "configs").mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "SMALL_CHUNK_METRICS.md").write_text(
        "# Synthetic metric contract\n", encoding="utf-8")
    (root / "src" / "finembed").mkdir(parents=True)
    code = root / "src" / "finembed" / "placeholder.py"
    code.write_text("# Synthetic code binding.\n", encoding="utf-8")
    smoke = root / "configs" / "smoke.yaml"
    smoke.write_text("synthetic: true\n", encoding="utf-8")
    corpus = root / "corpus_index.json"
    corpus.write_text('{"synthetic":true}\n', encoding="utf-8")
    candidate = root / "configs" / "candidate.json"
    company_order = ["AAPL", "MSFT", "AMZN", "GOOGL", "META"]
    candidate.write_text(json.dumps({
        "schema_version": 1, "kind": "new_engineering_probe_candidate",
        "status": "PROPOSED", "researcher_accepted": False,
        "real_inference_status": "NOT_RUN",
        "input": {"form": "10-K", "section": "ITEM_1_BUSINESS",
                  "company_order": company_order, "corpus_index_sha256": preflight.file_sha256(corpus)},
        "model_config": {"path": "configs/smoke.yaml", "sha256": preflight.file_sha256(smoke)},
        "chunking": {"reference_body_tokens": 128,
                     "candidate_body_tokens": [118, 123, 128, 133, 138], "overlap_tokens": 0},
        "pooling": ["equal_chunks", "body_token_weighted"],
        "query_rewriting": False, "generation": False, "training": False,
    }), encoding="utf-8")
    source = root / "source.txt"
    source.write_text("abc", encoding="utf-8")
    documents = [{"ticker": ticker, "cik": f"{i:010d}", "accession": f"SYNTHETIC-{i}",
                  "item1_text": "abc", "item1_sha256": sha256(b"abc"),
                  "files": {"source": {"path": str(source)}}}
                 for i, ticker in enumerate(company_order)]
    audit = {"valid_count": 5, "entries": [
        {"ticker": row["ticker"], "status": "VALID", "accession": row["accession"],
         "item1_sha256": row["item1_sha256"]} for row in documents]}
    config = SimpleNamespace(body_tokens=128, overlap_tokens=0, normalize_chunks=True,
                             normalize_documents=True,
                             companies=[SimpleNamespace(ticker=ticker) for ticker in company_order],
                             source_sha256=preflight.file_sha256(smoke), model_cache=root / "models")
    monkeypatch.setattr(preflight, "capture_runtime_provenance", lambda _: {
        "kind": "SMALL_CHUNK_RUNTIME_PROVENANCE", "synthetic": True})
    monkeypatch.setattr(preflight, "verify_runtime_provenance", lambda *_: {
        "status": "PASS_RUNTIME_PROVENANCE_UNCHANGED"})
    monkeypatch.setattr(preflight, "load_config", lambda _: config)
    monkeypatch.setattr(preflight, "validate_corpus", lambda *_: (documents, audit))
    monkeypatch.setattr(preflight, "inspect_model_files", lambda _: {
        "max_input_tokens": 256, "files": {}, "encoder_session_created": False})
    monkeypatch.setattr(preflight, "load_local_tokenizer", lambda _: (LetterTokenizer(), 2))
    monkeypatch.setattr("finembed.model.Encoder.__init__", lambda *_: pytest.fail("Encoder constructed"))
    output = root / "artifacts" / "synthetic"
    receipt = preflight.create_preflight(candidate, corpus, output)
    assert receipt["status"] == "PASS_TOKENIZATION_ONLY"
    assert receipt["actual_model_forward_calls"] == 0
    assert receipt["planned_counts"]["document_length_jobs"] == 25
    assert preflight.verify_receipt(output / "receipt.json")["status"] == "PASS_BOUND_FILES_UNCHANGED"
    receipt_path = output / "receipt.json"
    original_receipt = receipt_path.read_bytes()
    altered = json.loads(original_receipt)
    altered["historical_128_alignment"] = {
        "status": "PASS", "historical_map_sha256": "0" * 64}
    receipt_path.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="unique bound map"):
        preflight.verify_receipt(receipt_path)
    altered = json.loads(original_receipt)
    altered["planned_counts"]["chunk_vectors"] += 1
    receipt_path.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="planned counts"):
        preflight.verify_receipt(receipt_path)
    receipt_path.write_bytes(original_receipt)
    source.write_text("abcd", encoding="utf-8")
    with pytest.raises(ValueError, match="Bound input/config/code changed"):
        preflight.verify_receipt(output / "receipt.json")
    source.write_text("abc", encoding="utf-8")
    candidate.write_text(candidate.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Bound input/config/code changed"):
        preflight.verify_receipt(output / "receipt.json")
    candidate.write_text(candidate.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    code.write_text("# Changed synthetic code binding.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Bound input/config/code changed"):
        preflight.verify_receipt(output / "receipt.json")
