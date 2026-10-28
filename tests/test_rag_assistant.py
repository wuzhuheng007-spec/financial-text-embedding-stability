"""Software-fixture checks for the local retrieval/answer CLI boundary."""
from __future__ import annotations

import json

from finembed import rag_assistant


def test_retrieval_only_cli_keeps_source_reference(monkeypatch, capsys):
    monkeypatch.setattr(rag_assistant, "retrieve", lambda *args, **kwargs: {
        "status": "OK", "index_manifest_sha256": "a" * 64,
        "results": [{"node_id": "fixture-node-1", "rank": 1, "score": 2.0,
                     "text": "A fixture passage about revenue.",
                     "source": {"ticker": "TEST", "accession": "fixture-accession",
                                "chunk_text_sha256": "b" * 64}}],
    })
    monkeypatch.setattr(rag_assistant, "load_generation_backend",
                        lambda path: (None, {"mode": "none", "inference": "NOT_RUN"}))
    code = rag_assistant.main(["--index", "fixture-index", "--query", "revenue",
                               "--mode", "bm25", "--ticker", "TEST"])
    response = json.loads(capsys.readouterr().out)
    assert code == 0
    assert response["response"]["mode"] == "retrieval_only"
    assert response["response"]["answer"] is None
    assert response["response"]["passages"][0]["node_id"] == "fixture-node-1"
    assert response["response"]["passages"][0]["source"]["ticker"] == "TEST"


def test_cli_fails_closed_on_missing_generation_configuration(monkeypatch, capsys):
    monkeypatch.setattr(rag_assistant, "retrieve", lambda *args, **kwargs: {
        "status": "NO_MATCH", "index_manifest_sha256": "a" * 64, "results": []})
    code = rag_assistant.main(["--index", "fixture-index", "--query", "revenue",
                               "--mode", "bm25", "--backend-config", "missing.json"])
    error = json.loads(capsys.readouterr().err)
    assert code == 1
    assert error["status"] == "FAILED"
    assert error["error_type"] == "GenerationError"
