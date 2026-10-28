"""Software checks for local LlamaIndex retrieval, independent of research outcomes."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from finembed.model import Chunk
from finembed.rag import (
    RagError, _filter_indices, _node_records, _rank_scores, _rrf,
    _source_for_chunk, index_documents, load_rag_settings, retrieve,
)
from finembed.run import sha256


ROOT = Path(__file__).resolve().parents[1]


def _optional_runtime():
    pytest.importorskip("llama_index.core")
    pytest.importorskip("tokenizers")
    if not (ROOT / ".cache/models/all-MiniLM-L6-v2-1110a243/tokenizer.json").is_file():
        pytest.skip("Pinned local tokenizer unavailable")


def test_source_span_and_private_review_path_are_not_embedded():
    _optional_runtime()
    text = "Quarterly net sales increased."
    chunk = Chunk(0, 5, 0, len(text), (1, 2, 3, 4, 5), sha256(text.encode()))
    doc = {"ticker": "TST", "cik": "0000000001", "accession": "0000000001-25-000001",
           "form": "10-Q", "section": "PART_I_ITEM_2_MDA", "text": text,
           "input_type": "original_extract", "text_sha256": sha256(text.encode()),
           "raw_sha256": "a" * 64, "review_references": {
               "acceptance_event": {"path": "private/researcher_reply.txt", "sha256": "b" * 64}},
           "limitations": ["Software fixture only"]}
    descriptor, records = _node_records(ROOT, doc, [chunk])
    assert descriptor["node_count"] == 1
    assert records[0]["text"] == text
    assert records[0]["metadata"]["parent_section_unicode_span"] == [0, len(text)]
    assert "private/researcher_reply.txt" not in json.dumps(records)


def test_table_crossing_has_no_false_single_parent_span():
    text = "alpha TABLE beta"
    chunk = Chunk(0, 3, 0, len(text), (1, 2, 3), sha256(text.encode()))
    replacement = {"table_id": "MDA-1", "derivative_unicode_span": [6, 11],
                   "parent_section_unicode_span": [6, 20],
                   "source_html_unicode_span": [100, 200],
                   "raw_submission_byte_span": [300, 400],
                   "source_html_fragment_sha256": "c" * 64}
    source = _source_for_chunk(text, chunk, [replacement], None)
    assert source["parent_section_unicode_span"] is None
    assert source["table_ids"] == ["MDA-1"]
    assert source["source_mapping"] == "DERIVED_OR_CROSS_TABLE_BOUNDARY"


def test_filters_rank_ties_and_weighted_rrf_are_deterministic():
    records = [
        {"node_id": "z", "metadata": {"ticker": "AMZN", "section": "MDA"}},
        {"node_id": "a", "metadata": {"ticker": "AMZN", "section": "MDA"}},
        {"node_id": "g", "metadata": {"ticker": "GOOGL", "section": "MDA"}},
    ]
    allowed = _filter_indices(records, {"ticker": "AMZN", "section": "MDA"})
    assert allowed == [0, 1]
    tied = _rank_scores(records, allowed, [2.0, 2.0, 9.0], 2)
    assert [hit["node_id"] for hit in tied] == ["a", "z"]
    settings = SimpleNamespace(rrf_k=60, bm25_weight=1.0, dense_weight=1.0)
    fused = _rrf(records, {"bm25": tied, "dense": tied[::-1]}, settings, 2)
    assert {hit["node_id"] for hit in fused} == {"a", "z"}
    with pytest.raises(RagError, match="Nonfinite"):
        _rank_scores(records, allowed, [float("nan"), 1.0, 0.0], 2)


def test_real_config_is_separate_rag_policy_and_rejects_bad_overlap():
    _optional_runtime()
    settings = load_rag_settings(ROOT / "configs/rag_example.yaml")
    assert (settings.body_tokens, settings.overlap_tokens) == (160, 0)
    raw = (ROOT / "configs/rag_example.yaml").read_text(encoding="utf-8")
    # Config loader's root is the source's grandparent: place the temporary
    # software fixture beside the real config, then remove it.
    target = ROOT / "configs" / "rag_bad_overlap_test.yaml"
    try:
        target.write_text(raw.replace("overlap_tokens: 0", "overlap_tokens: 4"), encoding="utf-8")
        with pytest.raises(RagError, match="zero overlap"):
            load_rag_settings(target)
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.parametrize("missing", ["k1", "b"])
def test_missing_bm25_parameter_fails_as_rag_error(missing):
    _optional_runtime()
    raw = yaml.safe_load((ROOT / "configs/rag_example.yaml").read_text(encoding="utf-8"))
    del raw["rag"]["bm25"][missing]
    target = ROOT / "configs" / f"rag_missing_{missing}_test.yaml"
    try:
        target.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with pytest.raises(RagError, match=f"rag.bm25.{missing}"):
            load_rag_settings(target)
    finally:
        target.unlink(missing_ok=True)


def test_index_rejects_output_outside_ignored_project_dirs(tmp_path):
    _optional_runtime()
    with pytest.raises(RagError, match="under ignored"):
        index_documents([ROOT / "configs/rag_example.yaml"], ROOT / "configs/rag_example.yaml",
                        tmp_path / "source_bearing_index")


def test_lexical_only_index_filters_and_stale_binding(monkeypatch):
    _optional_runtime()
    from finembed import validated_inputs
    import shutil
    import tempfile
    source_dir = Path(tempfile.mkdtemp(prefix="rag-test-", dir=ROOT / ".cache"))
    spec = source_dir / "synthetic_spec.json"
    output = source_dir / "synthetic_index"
    spec.write_text('{"software_fixture":true}', encoding="utf-8")
    text = "Alpha quarterly revenue increased while cloud costs remained stable."
    fake_doc = {"ticker": "TST", "cik": "0000000001", "accession": "0000000001-25-000001",
                "form": "10-Q", "section": "PART_I_ITEM_2_MDA", "text": text,
                "input_type": "original_extract", "text_sha256": sha256(text.encode()),
                "raw_sha256": "a" * 64, "limitations": ["Software fixture only"]}
    audit = {"validation_status": "PASS", "validated_files": [
        {"path": spec.relative_to(ROOT).as_posix(), "sha256": sha256(spec.read_bytes())}]}
    monkeypatch.setattr(validated_inputs, "load_validated_input",
                        lambda *args, **kwargs: (fake_doc, audit))
    try:
        built = index_documents([spec], ROOT / "configs/rag_example.yaml", output, dense=False)
        assert built["dense_status"] == "NOT_BUILT"
        lexical = retrieve(output, "quarterly revenue", "bm25", ticker="TST")
        assert lexical["status"] == "OK"
        assert lexical["results"][0]["source"] == json.loads(
            (output / "nodes.jsonl").read_text(encoding="utf-8").splitlines()[0])["metadata"]
        assert retrieve(output, "quarterly revenue", "bm25", ticker="OTHER")["status"] == "NO_MATCH"
        assert retrieve(output, "quarterly revenue", "dense")["status"] == "UNAVAILABLE"
        spec.write_text('{"software_fixture":"changed"}', encoding="utf-8")
        with pytest.raises(RagError, match="Bound input changed"):
            retrieve(output, "quarterly revenue", "bm25")
    finally:
        shutil.rmtree(source_dir)


def test_pinned_onnx_is_a_real_llamaindex_embedding_adapter():
    _optional_runtime()
    pytest.importorskip("onnxruntime")
    import numpy as np
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from finembed.rag_embedding import PinnedONNXEmbedding
    from finembed.model import Encoder, aggregate

    settings = load_rag_settings(ROOT / "configs/rag_example.yaml")
    embedding = PinnedONNXEmbedding(settings.model_config, settings.body_tokens)
    assert isinstance(embedding, BaseEmbedding)
    vector = np.asarray(embedding.get_query_embedding("quarterly revenue"))
    assert vector.shape == (384,)
    assert np.isfinite(vector).all()
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-8)
    encoder = Encoder(settings.model_config)
    old_path = aggregate(encoder.embed_chunks(encoder.chunks("quarterly revenue", settings.body_tokens)),
                         normalize_chunks=True, normalize_documents=True)
    assert np.allclose(vector, old_path, atol=1e-10)
