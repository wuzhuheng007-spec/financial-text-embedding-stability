"""Synthetic-only S3 runner checks; no fixture is research evidence."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from finembed import boundary_shift_runner as runner
from finembed.boundary_shift_preflight import SHIFTS, boundary_shift_chunks
from finembed.model import aggregate
from finembed.run import sha256
from finembed.sensitivity_preflight import chunk_map_sha256


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "configs" / "boundary_shift_probe_round5.json"


class SyntheticTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return SimpleNamespace(ids=[ord(char) for char in text],
                               offsets=[(i, i + 1) for i in range(len(text))])


class FakeEncoder:
    instances = []
    fail_delta = None
    invalid_delta = None

    def __init__(self, config):
        self.config = config
        self.tokenizer = SyntheticTokenizer()
        self.special_count = 2
        self.identity = {"max_input_tokens": 256}
        self.forward_batch_call_attempts = 0
        self.embed_calls = 0
        FakeEncoder.instances.append(self)

    def embed_chunks(self, chunks):
        self.embed_calls += 1
        self.forward_batch_call_attempts += (
            len(chunks) + self.config.batch_size - 1) // self.config.batch_size
        delta = len(chunks[0].token_ids) - 128
        if self.fail_delta == delta and chunks[0].token_ids[0] == ord("A"):
            raise RuntimeError("synthetic encoder failure")
        vectors = np.array([[sum(chunk.token_ids),
                             sum(value * value for value in chunk.token_ids),
                             len(chunk.token_ids)] for chunk in chunks], dtype=np.float64)
        if self.invalid_delta == delta and chunks[0].token_ids[0] == ord("A"):
            vectors[0, 0] = np.nan
        return vectors


@pytest.fixture
def synthetic_plan(tmp_path):
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    docs, rows = [], []
    total_chunks = 0
    for index, ticker in enumerate(candidate["input"]["company_order"]):
        # AMZN's reference tail is 120, so delta=-10 produces a 130-token tail.
        tail = 120 if ticker == "AMZN" else 28
        text = chr(65 + index) * (128 * (9 + index) + tail)
        item_hash = sha256(text.encode("utf-8"))
        accession = f"{index + 1:010d}-25-{index + 1:06d}"
        docs.append({"ticker": ticker, "cik": f"{index + 1:010d}",
                     "accession": accession, "item1_sha256": item_hash,
                     "item1_text": text})
        encoding = SyntheticTokenizer().encode(text)
        shifts = {}
        for delta in SHIFTS:
            chunks = boundary_shift_chunks(encoding.ids, encoding.offsets, text,
                                           128, delta, 2, 256)
            total_chunks += len(chunks)
            body_lengths = [len(chunk.token_ids) for chunk in chunks]
            shifts[str(delta)] = {"body_tokens": len(encoding.ids),
                "chunk_count": len(chunks), "chunk_body_token_lengths": body_lengths,
                "chunk_map_sha256": chunk_map_sha256(chunks),
                "coverage_check": "PASS",
                "max_model_input_tokens": max(length + 2 for length in body_lengths)}
        rows.append({"ticker": ticker, "cik": f"{index + 1:010d}",
                     "accession": accession, "form": "10-K", "section": "ITEM_1_BUSINESS",
                     "item1_sha256": item_hash, "shifts": shifts})
    corpus_path = tmp_path / "synthetic_corpus_index.json"
    corpus_path.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    receipt_path = tmp_path / "synthetic_receipt.json"
    receipt_path.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    decision_path = tmp_path / "synthetic_decision.json"
    decision_path.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    return {"root": ROOT, "candidate": candidate, "candidate_path": CANDIDATE,
            "corpus_path": corpus_path, "receipt_path": receipt_path,
            "receipt_sha256": runner.file_sha256(receipt_path),
            "receipt": {"documents": rows, "planned_counts": {"chunk_vectors": total_chunks},
                        "special_tokens_per_chunk": 2,
                        "model": {"max_input_tokens": 256}},
            "decision_path": decision_path, "decision_sha256": runner.file_sha256(decision_path),
            "decision": {"status": "SYNTHETIC_TEST_ONLY"},
            "metric_contract_sha256": "0" * 64, "input_manifest_sha256": "1" * 64,
            "input_versions": [{key: row[key] for key in (
                "ticker", "cik", "accession", "form", "section", "item1_sha256")}
                for row in rows], "model_identity": {"synthetic": True},
            "runtime_provenance": {"synthetic": True},
            "bound_s3_modules": {"synthetic": True},
            "docs": docs, "config": SimpleNamespace(batch_size=8)}


def test_complete_fake_grid_and_saved_recalculation(synthetic_plan, tmp_path, monkeypatch):
    monkeypatch.setattr(runner.Encoder, "__init__", lambda *_:
                        pytest.fail("Real Encoder constructed in synthetic test"))
    output = tmp_path / "synthetic_complete"
    FakeEncoder.instances.clear()
    manifest = runner._execute_prepared(synthetic_plan, output,
                                        encoder_factory=FakeEncoder,
                                        evidence_class="SYNTHETIC_TEST_ONLY")
    assert manifest["status"] == "COMPLETE"
    assert manifest["axis"] == "boundary_shift"
    assert manifest["boundary_shifts"] == list(SHIFTS)
    assert manifest["logical_jobs_completed"] == 25
    assert manifest["chunk_vectors_completed"] == synthetic_plan["receipt"]["planned_counts"]["chunk_vectors"]
    assert manifest["company_vectors_completed"] == 50
    assert manifest["actual_batch_call_attempts"] == 50
    assert manifest["real_encoder_sessions_created"] == 0
    assert FakeEncoder.instances[-1].embed_calls == 25
    assert all(set(job["files"]) == {"chunks", "equal_chunks", "body_token_weighted"}
               for job in manifest["jobs"])
    amzn = next(job for job in manifest["jobs"]
                if job["ticker"] == "AMZN" and job["boundary_shift"] == -10)
    assert amzn["chunk_body_token_lengths"][0] == 118
    assert amzn["chunk_body_token_lengths"][-1] == 130
    chunks = np.load(output / amzn["files"]["chunks"]["path"], allow_pickle=False)
    weighted = np.load(output / amzn["files"]["body_token_weighted"]["path"], allow_pickle=False)
    equal = np.load(output / amzn["files"]["equal_chunks"]["path"], allow_pickle=False)
    assert np.array_equal(weighted, aggregate(chunks, True, True,
                                               weights=amzn["chunk_body_token_lengths"]))
    assert not np.array_equal(weighted, equal)
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["axis"] == "boundary_shift"
    assert metrics["inference_provenance"] == "SYNTHETIC_TEST_ONLY"
    assert runner.verify_saved_output(output)["metrics_recomputed"] is True
    with pytest.raises(FileExistsError):
        runner._execute_prepared(synthetic_plan, output, encoder_factory=FakeEncoder,
                                 evidence_class="SYNTHETIC_TEST_ONLY")


@pytest.mark.parametrize("failure", ["raise", "nan"])
def test_failure_retains_partial_without_complete_metrics(synthetic_plan, tmp_path, failure):
    class FailingEncoder(FakeEncoder):
        fail_delta = -10 if failure == "raise" else None
        invalid_delta = -10 if failure == "nan" else None

    output = tmp_path / f"synthetic_{failure}"
    manifest = runner._execute_prepared(synthetic_plan, output,
                                        encoder_factory=FailingEncoder,
                                        evidence_class="SYNTHETIC_TEST_ONLY")
    assert manifest["status"] == "PARTIAL_FAILURE"
    assert len(manifest["failures"]) == 1
    assert manifest["logical_jobs_completed"] == 24
    assert manifest["metric_file"] is None
    assert not (output / "metrics.json").exists()
    assert runner.verify_saved_output(output)["run_status"] == "PARTIAL_FAILURE"


def test_gate_rejects_before_encoder_and_output_creation(monkeypatch, tmp_path):
    def refused(*_args, **_kwargs):
        raise runner.ProbeGateError("S3 researcher decision gate: WRONG_SCOPE")

    monkeypatch.setattr(runner, "prepare_run", refused)
    monkeypatch.setattr(runner.Encoder, "__init__", lambda *_:
                        pytest.fail("Real Encoder constructed before S3 gate"))
    output = tmp_path / "no_output"
    with pytest.raises(runner.ProbeGateError, match="WRONG_SCOPE"):
        runner.run_candidate(CANDIDATE, tmp_path / "corpus",
                             tmp_path / "receipt", tmp_path / "r4_decision", output)
    assert not output.exists()
    with pytest.raises(ValueError, match="disagree"):
        runner._execute_prepared({}, tmp_path / "wrong_evidence",
                                 encoder_factory=FakeEncoder,
                                 evidence_class="REAL_MODEL")


def test_saved_vector_length_identity_and_metrics_tampering_rejected(synthetic_plan, tmp_path):
    output = tmp_path / "synthetic_tamper"
    manifest = runner._execute_prepared(synthetic_plan, output,
                                        encoder_factory=FakeEncoder,
                                        evidence_class="SYNTHETIC_TEST_ONLY")
    assert manifest["status"] == "COMPLETE"
    chunk_file = output / manifest["jobs"][0]["files"]["chunks"]["path"]
    original_chunk = chunk_file.read_bytes()
    chunk_file.write_bytes(original_chunk + b"tampered")
    with pytest.raises(ValueError, match="hash/path"):
        runner.verify_saved_output(output)
    chunk_file.write_bytes(original_chunk)
    metric_file = output / "metrics.json"
    original_metric = metric_file.read_bytes()
    metric_file.write_bytes(original_metric + b" ")
    with pytest.raises(ValueError, match="metric file hash"):
        runner.verify_saved_output(output)
    metric_file.write_bytes(original_metric)
    original_manifest = deepcopy(manifest)
    changed = deepcopy(original_manifest)
    changed["jobs"][0]["chunk_body_token_lengths"][-1] += 1
    runner._save_manifest(output, changed)
    with pytest.raises(ValueError, match="company vector differs"):
        runner.verify_saved_output(output)
    changed = deepcopy(original_manifest)
    changed["jobs"][0]["boundary_shift"] = 123
    runner._save_manifest(output, changed)
    with pytest.raises(ValueError, match="unexpected cells"):
        runner.verify_saved_output(output)
    changed = deepcopy(original_manifest)
    changed["reference_boundary_shift"] = -10
    runner._save_manifest(output, changed)
    with pytest.raises(ValueError, match="exact S3 manifest"):
        runner.verify_saved_output(output)


def test_real_source_module_binding_rejects_shadow_or_changed_code(tmp_path):
    receipt = {"bindings": []}
    with pytest.raises(runner.ProbeGateError, match="unbound or shadowed"):
        runner._require_bound_modules(ROOT, receipt)
    source = Path(runner.__file__).resolve()
    wrong_root = tmp_path
    (wrong_root / "src" / "finembed").mkdir(parents=True)
    receipt = {"bindings": [{"path": str(source), "sha256": runner.file_sha256(source)}]}
    with pytest.raises(runner.ProbeGateError, match="unbound or shadowed"):
        runner._require_bound_modules(wrong_root, receipt)


def test_integrated_decision_gate_rejects_before_encoder_and_directory(
        synthetic_plan, tmp_path, monkeypatch):
    root = tmp_path
    config_dir = root / "configs"
    contract_dir = root / "docs"
    artifact_dir = root / "artifacts"
    config_dir.mkdir()
    contract_dir.mkdir()
    artifact_dir.mkdir()
    contract = contract_dir / "S3_BOUNDARY_SHIFT_METRICS.md"
    contract.write_text("# Synthetic S3 contract\n", encoding="utf-8")
    config_file = config_dir / "smoke.yaml"
    config_file.write_text("synthetic: true\n", encoding="utf-8")
    corpus = artifact_dir / "corpus_index.json"
    corpus.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    historical = artifact_dir / "chunk_map.jsonl"
    historical.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    candidate = deepcopy(synthetic_plan["candidate"])
    candidate["input"]["versions"] = synthetic_plan["input_versions"]
    candidate["input"]["corpus_index_sha256"] = runner.file_sha256(corpus)
    candidate["model_config"]["sha256"] = runner.file_sha256(config_file)
    candidate_path = config_dir / "boundary_shift_probe_round5.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    config = SimpleNamespace(source_sha256=runner.file_sha256(config_file),
                             provider="CPUExecutionProvider", batch_size=8,
                             normalize_chunks=True, normalize_documents=True,
                             overlap_tokens=0, body_tokens=128)
    model = {"model_id": "sentence-transformers/all-MiniLM-L6-v2",
             "revision": "1" * 40, "tokenizer_revision": "1" * 40,
             "max_input_tokens": 256,
             "files": {name: {"sha256": sha256(name.encode("utf-8"))}
                       for name in ("onnx/model.onnx", "tokenizer.json",
                                    "config.json", "sentence_bert_config.json")}}
    receipt = {"kind": "BOUNDARY_SHIFT_TOKENIZATION_PREFLIGHT",
               "status": "PASS_TOKENIZATION_ONLY",
               "candidate_sha256": runner.file_sha256(candidate_path),
               "boundary_shifts": list(SHIFTS),
               "historical_128_alignment": {
                   "status": "PASS",
                   "historical_map_sha256": runner.file_sha256(historical)},
               "runtime_provenance": {"synthetic": True},
               "bindings": [
                   {"path": str(historical.resolve()), "sha256": runner.file_sha256(historical)},
                   {"path": str(contract.resolve()), "sha256": runner.file_sha256(contract)}],
               "s3_metric_contract_sha256": runner.file_sha256(contract),
               "metric_contract_sha256": candidate["metric_family_reference"]["sha256"],
               "reference_config_sha256": config.source_sha256,
               "model": model, "documents": synthetic_plan["receipt"]["documents"],
               "planned_counts": synthetic_plan["receipt"]["planned_counts"]}
    receipt_path = artifact_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    decision_path = artifact_dir / "decision.json"
    decision_path.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    monkeypatch.setattr(runner, "capture_runtime_provenance",
                        lambda *_args, **_kwargs: {"required_provider": "CPUExecutionProvider"})
    monkeypatch.setattr(runner, "verify_runtime_provenance",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_require_bound_modules",
                        lambda *_args, **_kwargs: {"synthetic": True})
    monkeypatch.setattr(runner, "verify_receipt", lambda *_args: None)
    monkeypatch.setattr(runner, "build_preflight",
                        lambda *_args: deepcopy(receipt))
    monkeypatch.setattr(runner, "load_config", lambda *_args: config)
    monkeypatch.setattr(runner, "inspect_model_files", lambda *_args: model)
    monkeypatch.setattr(runner, "validate_corpus",
                        lambda *_args: (synthetic_plan["docs"], {"valid_count": 5}))
    monkeypatch.setattr(runner.Encoder, "__init__", lambda *_:
                        pytest.fail("Real Encoder constructed before S3 decision gate"))
    output = artifact_dir / "must_not_exist"
    for state in ("MISSING", "WRONG_SCOPE", "VERSION_MISMATCH"):
        monkeypatch.setattr(runner, "validate_boundary_decision",
                            lambda *_args, _state=state, **_kwargs:
                            {"status": _state, "reason": "synthetic test refusal"})
        with pytest.raises(runner.ProbeGateError, match=state):
            runner.run_candidate(candidate_path, corpus, receipt_path,
                                 decision_path, output)
        assert not output.exists()
