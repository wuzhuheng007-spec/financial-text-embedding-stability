"""Synthetic software tests for source-bound RAG development metrics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finembed.rag_eval import EvalError, evaluate_set, load_development_set, run_evaluation


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fixture(tmp_path: Path):
    text = "Alpha beta gamma. Target phrase is here. Last sentence."
    source = tmp_path / "software_source.txt"
    source.write_text(text, encoding="utf-8")
    a, b = text.index("Target phrase"), text.index(" is here")
    evidence = {"ticker": "TEST", "accession": "0000000000-00-000001",
                "input_text_sha256": _sha(text), "char_start": a,
                "char_end_exclusive": b, "anchor_text": text[a:b],
                "anchor_sha256": _sha(text[a:b])}
    data = {"status": "SOFTWARE_DEVELOPMENT_LABELS_NOT_INDEPENDENT_HUMAN_EVALUATION",
            "source_files": {"TEST": {"path": str(source), "sha256": _sha(text)}},
            "questions": [
                {"id": "E", "question": "Where is target?", "filters": {"ticker": "TEST"},
                 "expected_evidence": evidence},
                {"id": "U", "question": "What exact second was it public?", "filters": {},
                 "expected_evidence": None, "requires_abstention": True}]}
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps(data), encoding="utf-8")
    return questions, data, {"TEST": text}


def _hit(text: str, start: int, end: int, *, ticker: str = "TEST") -> dict:
    chunk = text[start:end]
    return {"node_id": f"n:{start}:{end}", "rank": 1, "score": 1.0, "text": chunk,
            "source": {"ticker": ticker, "accession": "0000000000-00-000001",
                       "text_sha256": _sha(text), "chunk_char_start": start,
                       "chunk_char_end_exclusive": end, "chunk_text_sha256": _sha(chunk)}}


def test_development_anchor_must_match_exact_saved_bytes(tmp_path: Path) -> None:
    questions, data, sources = _fixture(tmp_path)
    loaded, bound = load_development_set(questions)
    assert loaded == data and bound == sources
    data["questions"][0]["expected_evidence"]["anchor_text"] = "fabricated"
    questions.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvalError, match="Anchor text or hash"):
        load_development_set(questions)


def test_full_span_hit_and_partial_overlap_are_distinct(tmp_path: Path) -> None:
    _, data, sources = _fixture(tmp_path)
    text = sources["TEST"]
    anchor = data["questions"][0]["expected_evidence"]
    calls = []

    def retrieve(_index, query, mode, **kwargs):
        calls.append((query, mode, kwargs))
        hit = (_hit(text, 0, len(text)) if mode == "bm25" else
               _hit(text, anchor["char_start"], anchor["char_end_exclusive"] - 2))
        return {"mode": mode, "query": query, "status": "OK", "results": [hit], "branches": {}}

    out = evaluate_set(tmp_path, data, sources, retrieve, modes=("bm25", "dense"), top_k=1)
    assert out["summary"]["bm25"]["hit_at_k"] == 1.0
    assert out["summary"]["dense"]["hit_at_k"] == 0.0
    assert out["cases"][2]["partial_overlap_ranks"] == [1]
    assert all(call[2]["top_k"] == 1 for call in calls)
    assert calls[0][2]["ticker"] == "TEST"


def test_unavailable_keeps_denominator_null_and_never_falls_back(tmp_path: Path) -> None:
    _, data, sources = _fixture(tmp_path)

    def retrieve(_index, query, mode, **_kwargs):
        return {"mode": mode, "query": query, "status": "UNAVAILABLE",
                "reason": "Dense index absent", "results": [], "branches": {}}

    out = evaluate_set(tmp_path, data, sources, retrieve, modes=("dense",))
    assert out["summary"]["dense"]["evaluable_denominator"] == 0
    assert out["summary"]["dense"]["hit_at_k"] is None
    assert out["summary"]["dense"]["status_counts"] == {"UNAVAILABLE": 2}


def test_no_match_is_evaluable_miss_not_unavailable(tmp_path: Path) -> None:
    _, data, sources = _fixture(tmp_path)

    def retrieve(_index, query, mode, **_kwargs):
        return {"mode": mode, "query": query, "status": "NO_MATCH", "results": [], "branches": {}}

    out = evaluate_set(tmp_path, data, sources, retrieve, modes=("bm25",))
    assert out["summary"]["bm25"]["evaluable_denominator"] == 1
    assert out["summary"]["bm25"]["hit_at_k"] == 0.0


def test_branch_filter_and_rank_are_checked(tmp_path: Path) -> None:
    _, data, sources = _fixture(tmp_path)
    text = sources["TEST"]
    good = _hit(text, 0, len(text))
    branch = _hit(text, 0, len(text), ticker="OTHER")
    branch["node_id"] = "other-node"

    def retrieve(_index, query, mode, **_kwargs):
        return {"mode": mode, "query": query, "status": "OK", "results": [good],
                "branches": {"bm25": [{"node_id": "other-node", "rank": 1, "score": 1.0}]}}

    out = evaluate_set(tmp_path, data, sources, retrieve, modes=("bm25",),
                       node_sources={good["node_id"]: good["source"],
                                     "other-node": branch["source"]})
    assert out["cases"][0]["status"] == "ERROR"
    assert "filter" in out["cases"][0]["error"]


def test_required_limitation_is_not_assumed_from_caption_hit(tmp_path: Path) -> None:
    _, data, sources = _fixture(tmp_path)
    text = sources["TEST"]
    data["questions"] = [data["questions"][0]]
    data["questions"][0]["required_limitation"] = "UNRESOLVED_TABLE_MAPPING"

    def retrieve(_index, query, mode, **_kwargs):
        return {"mode": mode, "query": query, "status": "OK",
                "results": [_hit(text, 0, len(text))], "branches": {}}

    out = evaluate_set(tmp_path, data, sources, retrieve, modes=("bm25",))
    assert out["cases"][0]["hit_at_k"] is True
    assert out["cases"][0]["required_limitation_exposed"] is False


def test_filter_violation_is_error_not_hit(tmp_path: Path) -> None:
    _, data, sources = _fixture(tmp_path)
    text = sources["TEST"]

    def retrieve(_index, query, mode, **_kwargs):
        return {"mode": mode, "query": query, "status": "OK",
                "results": [_hit(text, 0, len(text), ticker="OTHER")], "branches": {}}

    out = evaluate_set(tmp_path, data, sources, retrieve, modes=("bm25",))
    assert out["cases"][0]["status"] == "ERROR"
    assert out["summary"]["bm25"]["evaluable_denominator"] == 0


def test_unsupported_probe_retrieval_only_is_not_semantic_abstention(tmp_path: Path) -> None:
    _, data, sources = _fixture(tmp_path)
    data["questions"] = [data["questions"][1]]
    text = sources["TEST"]

    def retrieve(_index, query, mode, **_kwargs):
        return {"mode": mode, "query": query, "status": "OK",
                "results": [_hit(text, 0, len(text))], "branches": {}}

    out = evaluate_set(tmp_path, data, sources, retrieve, modes=("bm25",))
    probe = out["summary"]["bm25"]["unsupported_probe"][0]
    assert probe["no_generated_answer"] is True
    assert probe["response_mode"] == "retrieval_only"
    assert probe["semantic_abstention_verified"] is False
    assert out["summary"]["bm25"]["hit_at_k"] is None


def test_no_match_unsupported_probe_abstains(tmp_path: Path) -> None:
    _, data, sources = _fixture(tmp_path)
    data["questions"] = [data["questions"][1]]

    def retrieve(_index, query, mode, **_kwargs):
        return {"mode": mode, "query": query, "status": "NO_MATCH", "results": [], "branches": {}}

    out = evaluate_set(tmp_path, data, sources, retrieve, modes=("bm25",))
    probe = out["summary"]["bm25"]["unsupported_probe"][0]
    assert probe["response_mode"] == "abstained"
    assert probe["semantic_abstention_verified"] is True


def test_run_writes_bound_manifest_and_refuses_overwrite(tmp_path: Path, monkeypatch) -> None:
    questions, data, sources = _fixture(tmp_path)
    text = sources["TEST"]
    index = tmp_path / "index"
    index.mkdir()
    hit = _hit(text, 0, len(text))
    (index / "nodes.jsonl").write_text(json.dumps({"node_id": hit["node_id"],
                                                    "metadata": hit["source"]}) + "\n", encoding="utf-8")
    (index / "manifest.json").write_text(json.dumps({"documents": [{"identity": {
        "ticker": "TEST", "accession": "0000000000-00-000001", "text_sha256": _sha(text)}}]}), encoding="utf-8")

    def retrieve(_index, query, mode, **_kwargs):
        return {"mode": mode, "query": query, "status": "OK", "results": [hit], "branches": {}}

    monkeypatch.setattr("finembed.rag.retrieve", retrieve)
    output = tmp_path / "new_run"
    result = run_evaluation(index, questions, output, modes=("bm25",), top_k=1)
    assert result["summary"]["bm25"]["full_span_hits"] == 1
    audit = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert audit["evaluation_sha256"] == hashlib.sha256((output / "evaluation.json").read_bytes()).hexdigest()
    assert audit["source_file_sha256"]["TEST"] == _sha(text)
    with pytest.raises(EvalError, match="exists"):
        run_evaluation(index, questions, output, modes=("bm25",), top_k=1)
