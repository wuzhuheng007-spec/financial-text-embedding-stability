"""S3 tokenizer-only software fixtures; no model inference or research result."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from finembed.model import ModelError, chunk_text
from finembed.run import sha256
from finembed import boundary_shift_preflight as s3


class CountingTokenizer:
    def __init__(self):
        self.calls = 0

    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        self.calls += 1
        return SimpleNamespace(ids=list(range(1, len(text) + 1)),
                               offsets=[(index, index + 1) for index in range(len(text))])


def sliced(text: str, delta: int, *, base: int = 4, limit: int = 20):
    encoded = CountingTokenizer().encode(text)
    return s3.boundary_shift_chunks(encoded.ids, encoded.offsets, text,
                                    base, delta, 2, limit)


def test_zero_shift_matches_historical_fixed_chunks_and_positive_negative_cuts():
    text = "abcdefghij"
    zero = sliced(text, 0)
    historical = chunk_text(CountingTokenizer(), text, 4, 2, 20)
    assert zero == historical
    assert [(c.token_start, c.token_end_exclusive) for c in sliced(text, -1)] == [
        (0, 3), (3, 7), (7, 10)]
    assert [(c.token_start, c.token_end_exclusive) for c in sliced(text, 1)] == [
        (0, 5), (5, 9), (9, 10)]
    for delta in (-1, 0, 1):
        chunks = sliced(text, delta)
        assert tuple(token for chunk in chunks for token in chunk.token_ids) == tuple(range(1, 11))
        assert len(chunks) == 3
        assert all(a.token_end_exclusive == b.token_start for a, b in zip(chunks, chunks[1:]))


def test_negative_shift_may_make_tail_longer_than_base_without_clipping():
    text = "a" * 248
    encoded = CountingTokenizer().encode(text)
    chunks = s3.boundary_shift_chunks(encoded.ids, encoded.offsets, text, 128, -10, 2, 132)
    assert [len(chunk.token_ids) for chunk in chunks] == [118, 130]
    assert chunks[-1].token_end_exclusive == 248
    assert tuple(token for chunk in chunks for token in chunk.token_ids) == tuple(encoded.ids)


@pytest.mark.parametrize("delta", [-4, 2, 3])
def test_empty_or_out_of_bounds_shifted_cut_is_rejected(delta):
    with pytest.raises(ModelError, match="cuts"):
        sliced("abcdefghij", delta)


def test_invalid_body_tokens_offsets_and_model_budget_are_rejected():
    text = "abcdefghij"
    encoded = CountingTokenizer().encode(text)
    with pytest.raises(ModelError, match="valid integers"):
        s3.boundary_shift_chunks(encoded.ids, encoded.offsets, text, 0, 0, 2, 20)
    with pytest.raises(ModelError, match="matching offsets"):
        s3.boundary_shift_chunks(encoded.ids, encoded.offsets[:-1], text, 4, 0, 2, 20)
    with pytest.raises(ModelError, match="offsets"):
        s3.boundary_shift_chunks(encoded.ids, [(0, 99)] * 10, text, 4, 0, 2, 20)
    with pytest.raises(ModelError, match="budget"):
        s3.boundary_shift_chunks(encoded.ids, encoded.offsets, text, 4, 1, 2, 6)
    with pytest.raises(ModelError, match="budget"):
        s3.boundary_shift_chunks(encoded.ids, encoded.offsets, text, 4, -1, 2, 5)


def test_wrong_import_source_is_rejected(tmp_path):
    (tmp_path / "src/finembed").mkdir(parents=True)
    (tmp_path / "src/finembed/boundary_shift_preflight.py").write_text("# synthetic\n")
    with pytest.raises(s3.RuntimeProvenanceError, match="another checkout"):
        s3._ensure_imported_module(tmp_path)


def test_candidate_rejects_nonstring_source_identity(monkeypatch, tmp_path):
    candidate, *_ = synthetic_project(monkeypatch, tmp_path)
    data = json.loads(candidate.read_text())
    data["input"]["versions"][0]["cik"] = 1
    candidate.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="input version"):
        s3.load_boundary_candidate(candidate)


def synthetic_project(monkeypatch, tmp_path):
    root = tmp_path
    (root / "configs").mkdir()
    (root / "docs").mkdir()
    (root / "src/finembed").mkdir(parents=True)
    (root / "src/finembed/placeholder.py").write_text("# SYNTHETIC_TEST_ONLY\n")
    smoke = root / "configs/smoke.yaml"
    smoke.write_text("synthetic: true\n")
    metric = root / "docs/SMALL_CHUNK_METRICS.md"
    metric.write_text("# SYNTHETIC_TEST_ONLY\n")
    (root / "docs/S3_BOUNDARY_SHIFT_METRICS.md").write_text(
        "# SYNTHETIC_TEST_ONLY S3 metric contract\n")
    corpus = root / "corpus_index.json"
    corpus.write_text('{"synthetic_test_only":true}\n')
    source = root / "source.txt"
    source.write_text("a" * 250)
    text = source.read_text()
    digest = sha256(text.encode())
    versions = [{"ticker": ticker, "cik": f"{index+1:010d}",
                 "accession": f"{index+1:010d}-25-000001", "form": "10-K",
                 "section": "ITEM_1_BUSINESS", "item1_sha256": digest}
                for index, ticker in enumerate(s3.COMPANIES)]
    docs = [{**version, "item1_text": text,
             "files": {"source": {"path": str(source)}}} for version in versions]
    tokenizer = CountingTokenizer()
    encoded = tokenizer.encode(text)
    historical = root / "historical_map.jsonl"
    with historical.open("w", encoding="utf-8") as stream:
        for doc in docs:
            chunks = s3.boundary_shift_chunks(encoded.ids, encoded.offsets, text, 128, 0, 2, 256)
            for row in s3._historical_rows(doc, chunks, 2):
                stream.write(json.dumps(row) + "\n")
    tokenizer.calls = 0
    candidate = root / "configs/boundary_shift_probe_round5.json"
    candidate.write_text(json.dumps({
        "schema_version": 1, "kind": "boundary_shift_engineering_probe_candidate",
        "status": "PROPOSED", "researcher_accepted": False,
        "real_inference_status": "NOT_RUN",
        "input": {"form": "10-K", "section": "ITEM_1_BUSINESS",
                  "company_order": list(s3.COMPANIES), "versions": versions,
                  "corpus_index_sha256": s3.file_sha256(corpus)},
        "model_config": {"path": "configs/smoke.yaml",
                         "sha256": s3.file_sha256(smoke)},
        "provider": "CPUExecutionProvider",
        "normalization": {"normalize_chunks": True, "normalize_documents": True,
                          "order": s3.NORMALIZATION_ORDER},
        "chunking": {"reference_body_tokens": 128,
                     "boundary_shifts": list(s3.SHIFTS),
                     "overlap_tokens": 0, "rule": "shift_all_internal_cuts"},
        "pooling": list(s3.POOLING),
        "metric_family_reference": {"path": "docs/SMALL_CHUNK_METRICS.md",
                                    "sha256": s3.file_sha256(metric)},
        "historical_128_map_sha256": s3.file_sha256(historical),
        "query_rewriting": False, "generation": False, "training": False,
    }), encoding="utf-8")
    config = SimpleNamespace(body_tokens=128, overlap_tokens=0,
                             normalize_chunks=True, normalize_documents=True,
                             provider="CPUExecutionProvider", source_sha256=s3.file_sha256(smoke),
                             companies=[SimpleNamespace(ticker=t) for t in s3.COMPANIES],
                             model_cache=root / "models")
    audit = {"valid_count": 5, "entries": [
        {"ticker": d["ticker"], "status": "VALID", "accession": d["accession"],
         "item1_sha256": d["item1_sha256"]} for d in docs]}
    monkeypatch.setattr(s3, "_ensure_imported_module", lambda _: None)
    monkeypatch.setattr(s3, "capture_runtime_provenance", lambda _: {
        "kind": "SMALL_CHUNK_RUNTIME_PROVENANCE", "synthetic_test_only": True})
    monkeypatch.setattr(s3, "verify_runtime_provenance", lambda *_: {"status": "PASS"})
    monkeypatch.setattr(s3, "load_config", lambda _: config)
    monkeypatch.setattr(s3, "validate_corpus", lambda *_: (docs, audit))
    monkeypatch.setattr(s3, "inspect_model_files", lambda _: {
        "max_input_tokens": 256, "files": {}, "encoder_session_created": False})
    monkeypatch.setattr(s3, "load_local_tokenizer", lambda _: (tokenizer, 2))
    monkeypatch.setattr("finembed.model.Encoder.__init__",
                        lambda *_: pytest.fail("Real Encoder constructed in S3 preflight"))
    return candidate, corpus, historical, tokenizer, source, metric


def test_synthetic_five_by_five_plan_is_tokenizer_only_and_detects_drift(monkeypatch, tmp_path):
    candidate, corpus, historical, tokenizer, source, metric = synthetic_project(monkeypatch, tmp_path)
    output = tmp_path / "artifacts/s3"
    receipt = s3.create_preflight(candidate, corpus, historical, output)
    assert receipt["status"] == "PASS_TOKENIZATION_ONLY"
    assert receipt["historical_128_alignment"]["status"] == "PASS"
    assert receipt["planned_counts"]["document_shift_jobs"] == 25
    assert receipt["planned_counts"]["chunk_vectors"] == 50
    assert receipt["planned_counts"]["company_vectors"] == 50
    assert receipt["actual_model_forward_calls"] == receipt["actual_model_vectors"] == 0
    assert receipt["s3_metric_contract_sha256"] == s3.file_sha256(
        tmp_path / "docs/S3_BOUNDARY_SHIFT_METRICS.md")
    assert tokenizer.calls == 5  # One full-body tokenization per company, not per shift.
    assert s3.verify_receipt(output / "receipt.json")["status"] == \
        "PASS_BOUND_FILES_AND_TOKENIZATION_UNCHANGED"
    receipt_path = output / "receipt.json"
    original_receipt = receipt_path.read_bytes()
    edited = json.loads(original_receipt)
    edited["documents"][0]["shifts"]["-10"]["tail_tokens"] += 1
    receipt_path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(ValueError, match="conclusions differ"):
        s3.verify_receipt(receipt_path)
    receipt_path.write_bytes(original_receipt)
    source.write_text("changed synthetic source")
    with pytest.raises(ValueError, match="bound input"):
        s3.verify_receipt(output / "receipt.json")
    source.write_text("a" * 250)
    metric.write_text("changed synthetic metric family")
    with pytest.raises(ValueError, match="bound input"):
        s3.verify_receipt(output / "receipt.json")
    metric.write_text("# SYNTHETIC_TEST_ONLY\n")
    s3_metric = tmp_path / "docs/S3_BOUNDARY_SHIFT_METRICS.md"
    s3_metric.write_text("changed synthetic S3 metric contract")
    with pytest.raises(ValueError, match="bound input"):
        s3.verify_receipt(output / "receipt.json")
    s3_metric.write_text("# SYNTHETIC_TEST_ONLY S3 metric contract\n")
    candidate.write_text(candidate.read_text() + "\n")
    with pytest.raises(ValueError, match="bound input"):
        s3.verify_receipt(output / "receipt.json")
    candidate.write_text(candidate.read_text().rstrip("\n"))
    (tmp_path / "src/finembed/placeholder.py").write_text("# modified synthetic code\n")
    with pytest.raises(ValueError, match="bound input"):
        s3.verify_receipt(output / "receipt.json")


def test_synthetic_reference_row_mismatch_and_candidate_version_rejected(monkeypatch, tmp_path):
    candidate, corpus, historical, *_ = synthetic_project(monkeypatch, tmp_path)
    rows = historical.read_text().splitlines()
    altered = json.loads(rows[0])
    altered["body_token_ids"][0] = 999
    rows[0] = json.dumps(altered)
    historical.write_text("\n".join(rows) + "\n")
    candidate_data = json.loads(candidate.read_text())
    candidate_data["historical_128_map_sha256"] = s3.file_sha256(historical)
    candidate.write_text(json.dumps(candidate_data))
    receipt = s3.build_preflight(candidate, corpus, historical)
    assert receipt["status"] == "FAILED"
    assert "Delta-zero map differs row-by-row" in receipt["reason"]
    data = json.loads(candidate.read_text())
    data["input"]["versions"][0]["item1_sha256"] = "0" * 64
    candidate.write_text(json.dumps(data))
    receipt = s3.build_preflight(candidate, corpus, historical)
    assert receipt["status"] == "FAILED"
    assert "input versions differ" in receipt["reason"]


def test_invalid_source_retains_failed_receipt_without_encoder(monkeypatch, tmp_path):
    candidate, corpus, historical, *_ = synthetic_project(monkeypatch, tmp_path)
    def invalid_corpus(*_):
        raise s3.AnalysisError("synthetic source mismatch")
    monkeypatch.setattr(s3, "validate_corpus", invalid_corpus)
    output = tmp_path / "artifacts/failed_s3"
    receipt = s3.create_preflight(candidate, corpus, historical, output)
    assert receipt["status"] == "FAILED"
    assert "synthetic source mismatch" in receipt["reason"]
    assert json.loads((output / "receipt.json").read_text())["status"] == "FAILED"
    assert "FAILED" in (output / "summary.md").read_text()
