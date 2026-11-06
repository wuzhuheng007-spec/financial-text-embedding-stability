"""Synthetic-only runner checks; fixtures are never research evidence."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from finembed import small_chunk_runner as runner
from finembed import small_chunk_decision as decision
from finembed.model import ModelError, chunk_text
from finembed.run import sha256
from finembed.sensitivity_preflight import chunk_map_sha256


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "configs" / "small_chunk_probe_round2.json"


class SyntheticTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return SimpleNamespace(ids=[ord(char) for char in text],
                               offsets=[(i, i + 1) for i in range(len(text))])


class FakeEncoder:
    instances = []
    fail_length = None
    invalid = None

    def __init__(self, config):
        self.config = config
        self.tokenizer = SyntheticTokenizer()
        self.special_count = 2
        self.identity = {"max_input_tokens": 256}
        self.forward_batch_call_attempts = 0
        self.embed_calls = 0
        self.active_length = None
        FakeEncoder.instances.append(self)

    def chunks(self, text, body_tokens):
        self.active_length = body_tokens
        self.active_text = text
        return chunk_text(self.tokenizer, text, body_tokens, 2, 256)

    def embed_chunks(self, chunks):
        self.embed_calls += 1
        # The fixture uses batch size one, so jobs and batch calls differ.
        # This is accounting behavior only; no ONNX session is involved.
        self.forward_batch_call_attempts += (
            len(chunks) + self.config.batch_size - 1) // self.config.batch_size
        if self.fail_length == self.active_length and self.active_text.startswith("A"):
            raise RuntimeError("synthetic encoder failure")
        vectors = np.array([[sum(chunk.token_ids),
                             sum(value * value for value in chunk.token_ids),
                             len(chunk.token_ids)] for chunk in chunks], dtype=np.float64)
        if self.invalid == "zero" and self.active_length == 118 and self.active_text.startswith("A"):
            vectors[0] = 0
        if self.invalid == "nan" and self.active_length == 118 and self.active_text.startswith("A"):
            vectors[0, 0] = np.nan
        return vectors


@pytest.fixture
def synthetic_plan(tmp_path):
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    lengths = candidate["chunking"]["candidate_body_tokens"]
    docs = []
    rows = []
    total_chunks = 0
    for index, ticker in enumerate(candidate["input"]["company_order"]):
        text = chr(65 + index) * (118 + 5 * index) + "Z" * (21 + index)
        item_hash = sha256(text.encode("utf-8"))
        accession = f"{index + 1:010d}-25-{index + 1:06d}"
        docs.append({"ticker": ticker, "cik": f"{index + 1:010d}",
                     "accession": accession, "item1_sha256": item_hash,
                     "item1_text": text})
        length_stats = {}
        for length in lengths:
            chunks = chunk_text(SyntheticTokenizer(), text, length, 2, 256)
            total_chunks += len(chunks)
            length_stats[str(length)] = {"body_tokens": len(text),
                "chunk_count": len(chunks), "tail_tokens": len(chunks[-1].token_ids),
                "chunk_map_sha256": chunk_map_sha256(chunks),
                "coverage_check": "PASS",
                "max_model_input_tokens": max(len(chunk.token_ids) + 2 for chunk in chunks)}
        rows.append({"ticker": ticker, "cik": f"{index + 1:010d}",
                     "accession": accession, "form": "10-K", "section": "ITEM_1_BUSINESS",
                     "item1_sha256": item_hash, "lengths": length_stats})
    corpus_path = tmp_path / "synthetic_corpus_index.json"
    corpus_path.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    receipt_path = tmp_path / "synthetic_receipt.json"
    receipt_path.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    decision_path = tmp_path / "synthetic_decision.json"
    decision_path.write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    return {"root": tmp_path, "candidate": candidate, "candidate_path": CANDIDATE,
            "corpus_path": corpus_path, "receipt_path": receipt_path,
            "receipt_sha256": runner.file_sha256(receipt_path),
            "receipt": {"documents": rows, "planned_counts": {"chunk_vectors": total_chunks}},
            "decision_path": decision_path, "decision_sha256": runner.file_sha256(decision_path),
            "decision": {"status": "SYNTHETIC_TEST_ONLY", "source_authenticity": "NOT_APPLICABLE"},
            "metric_contract_sha256": "0" * 64, "input_manifest_sha256": "1" * 64,
            "input_versions": [{key: row[key] for key in (
                "ticker", "cik", "accession", "form", "section", "item1_sha256")}
                for row in rows], "model_identity": {"synthetic": True},
            "runtime_provenance": {"synthetic": True}, "docs": docs,
            "config": SimpleNamespace(batch_size=1)}


def test_complete_fake_run_reuses_chunk_vectors_and_recomputes(synthetic_plan, tmp_path):
    FakeEncoder.instances.clear()
    output = tmp_path / "fake_complete"
    manifest = runner._execute_prepared(synthetic_plan, output, encoder_factory=FakeEncoder,
                                        evidence_class="SYNTHETIC_TEST_ONLY")
    assert manifest["status"] == "COMPLETE"
    assert manifest["evidence_class"] == "SYNTHETIC_TEST_ONLY"
    assert manifest["logical_job_count_planned"] == 25
    assert manifest["logical_jobs_completed"] == 25
    assert manifest["chunk_vectors_completed"] == synthetic_plan["receipt"]["planned_counts"]["chunk_vectors"]
    assert manifest["company_vectors_completed"] == 50
    assert manifest["actual_batch_call_attempts"] == manifest["chunk_vectors_completed"] > 25
    assert FakeEncoder.instances[-1].embed_calls == 25
    assert all(set(job["files"]) == {"chunks", "equal_chunks", "body_token_weighted"}
               for job in manifest["jobs"])
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["inference_provenance"] == "SYNTHETIC_TEST_ONLY"
    assert metrics["denominators"]["upper_triangle_pairs"] == 10
    assert runner.verify_saved_output(output)["metrics_recomputed"] is True
    with pytest.raises(FileExistsError):
        runner._execute_prepared(synthetic_plan, output, encoder_factory=FakeEncoder,
                                 evidence_class="SYNTHETIC_TEST_ONLY")


@pytest.mark.parametrize("failure", ["raise", "zero", "nan"])
def test_failed_company_never_reports_full_grid(synthetic_plan, tmp_path, failure):
    class FailingEncoder(FakeEncoder):
        fail_length = 118 if failure == "raise" else None
        invalid = failure if failure != "raise" else None

    output = tmp_path / f"fake_{failure}"
    manifest = runner._execute_prepared(synthetic_plan, output, encoder_factory=FailingEncoder,
                                        evidence_class="SYNTHETIC_TEST_ONLY")
    assert manifest["status"] == "PARTIAL_FAILURE"
    assert len(manifest["failures"]) == 1
    assert manifest["logical_jobs_completed"] == 24
    assert manifest["metric_file"] is None
    assert not (output / "metrics.json").exists()
    assert runner.verify_saved_output(output)["run_status"] == "PARTIAL_FAILURE"


def test_missing_company_and_evidence_class_fail_before_encoder(synthetic_plan, tmp_path):
    plan = deepcopy(synthetic_plan)
    plan["docs"] = plan["docs"][:-1]
    with pytest.raises(runner.ProbeGateError, match="five-company"):
        runner._execute_prepared(plan, tmp_path / "missing", encoder_factory=FakeEncoder,
                                 evidence_class="SYNTHETIC_TEST_ONLY")
    assert not (tmp_path / "missing").exists()
    with pytest.raises(ValueError, match="disagree"):
        runner._execute_prepared(synthetic_plan, tmp_path / "wrong_label",
                                 encoder_factory=FakeEncoder, evidence_class="REAL_MODEL")


def test_saved_vector_metric_and_manifest_tamper_are_rejected(synthetic_plan, tmp_path):
    output = tmp_path / "tamper"
    manifest = runner._execute_prepared(synthetic_plan, output, encoder_factory=FakeEncoder,
                                        evidence_class="SYNTHETIC_TEST_ONLY")
    assert manifest["status"] == "COMPLETE"
    vector_file = output / manifest["jobs"][0]["files"]["chunks"]["path"]
    original = vector_file.read_bytes()
    vector_file.write_bytes(original + b"tampered")
    with pytest.raises(ValueError, match="hash/path"):
        runner.verify_saved_output(output)
    vector_file.write_bytes(original)
    metrics = output / "metrics.json"
    original_metrics = metrics.read_bytes()
    metrics.write_bytes(original_metrics + b" ")
    with pytest.raises(ValueError, match="metric file hash"):
        runner.verify_saved_output(output)
    metrics.write_bytes(original_metrics)
    manifest_file = output / "manifest.json"
    manifest_file.write_bytes(manifest_file.read_bytes() + b" ")
    with pytest.raises(ValueError, match="manifest hash"):
        runner.verify_saved_output(output)


def test_saved_metadata_and_metric_path_checked_even_if_sidecar_is_updated(
        synthetic_plan, tmp_path, monkeypatch):
    output = tmp_path / "metadata"
    runner._execute_prepared(synthetic_plan, output, encoder_factory=FakeEncoder,
                             evidence_class="SYNTHETIC_TEST_ONLY")
    manifest_path = output / "manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))

    def replace_manifest(value):
        runner._save_manifest(output, value)

    changed = deepcopy(original)
    changed["company_vector_count_planned"] = 49
    replace_manifest(changed)
    with pytest.raises(ValueError, match="incomplete job grid"):
        runner.verify_saved_output(output)

    outside = tmp_path / "outside_metrics.json"
    outside.write_bytes((output / "metrics.json").read_bytes())
    changed = deepcopy(original)
    changed["metric_file"] = {"path": "../outside_metrics.json",
                              "sha256": runner.file_sha256(outside)}
    replace_manifest(changed)
    with pytest.raises(ValueError, match="metric file hash"):
        runner.verify_saved_output(output)

    changed = deepcopy(original)
    changed["evidence_class"] = "REAL_MODEL"
    changed["candidate_sha256"] = "0" * 64
    replace_manifest(changed)
    monkeypatch.setattr(runner, "prepare_run", lambda *_: synthetic_plan)
    with pytest.raises(ValueError, match="candidate_sha256"):
        runner.verify_saved_output(output)


def test_saved_shape_chunk_accounting_and_batch_tamper_are_rejected(
        synthetic_plan, tmp_path):
    output = tmp_path / "structure"
    runner._execute_prepared(synthetic_plan, output, encoder_factory=FakeEncoder,
                             evidence_class="SYNTHETIC_TEST_ONLY")
    original = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    changes = (
        ("shape", lambda m: m["jobs"][0]["files"]["chunks"].__setitem__("shape", [999, 3]),
         "shape, dtype"),
        ("chunk_length", lambda m: m["jobs"][0]["chunk_body_token_lengths"].pop(),
         "chunk rows or body-token lengths"),
        ("batch", lambda m: m.__setitem__("actual_batch_call_attempts",
                                         m["actual_batch_call_attempts"] + 1),
         "incomplete job grid"),
        ("session", lambda m: m.__setitem__("real_encoder_sessions_created", 1),
         "incomplete job grid"),
        ("cell_path", lambda m: m["jobs"][0]["files"]["chunks"].__setitem__(
            "path", "vectors/123/AAPL_chunks.npy"), "declared job cell"),
    )
    for _label, alter, reason in changes:
        changed = deepcopy(original)
        alter(changed)
        runner._save_manifest(output, changed)
        with pytest.raises(ValueError, match=reason):
            runner.verify_saved_output(output)


def test_public_gate_stops_before_model_construction(monkeypatch, tmp_path):
    def refused(*_args, **_kwargs):
        raise runner.ProbeGateError("Researcher decision gate: MISSING")

    monkeypatch.setattr(runner, "prepare_run", refused)
    monkeypatch.setattr(runner.Encoder, "__init__", lambda *_: pytest.fail("Encoder constructed"))
    output = tmp_path / "no_model"
    with pytest.raises(runner.ProbeGateError, match="MISSING"):
        runner.run_candidate(CANDIDATE, tmp_path / "corpus", tmp_path / "receipt", None, output)
    assert not output.exists()


@pytest.fixture
def gate_case(tmp_path, monkeypatch, synthetic_plan):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
    (tmp_path / "configs").mkdir()
    (tmp_path / "docs").mkdir()
    contract = tmp_path / "docs" / "SMALL_CHUNK_METRICS.md"
    contract.write_text("# Synthetic metric definition\n", encoding="utf-8")
    smoke = tmp_path / "configs" / "smoke.yaml"
    smoke.write_text("synthetic: true\n", encoding="utf-8")
    corpus = synthetic_plan["corpus_path"]
    candidate = deepcopy(synthetic_plan["candidate"])
    candidate["input"]["corpus_index_sha256"] = runner.file_sha256(corpus)
    candidate["model_config"]["sha256"] = runner.file_sha256(smoke)
    candidate_path = tmp_path / "configs" / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    config = SimpleNamespace(source_sha256=runner.file_sha256(smoke),
                             provider="CPUExecutionProvider", normalize_chunks=True,
                             normalize_documents=True, overlap_tokens=0,
                             body_tokens=128)
    model = {"model_id": "sentence-transformers/all-MiniLM-L6-v2",
             "revision": "1" * 40, "tokenizer_revision": "1" * 40,
             "max_input_tokens": 256,
             "files": {name: {"sha256": sha256(name.encode("utf-8"))}
                       for name in decision.MODEL_FILE_NAMES}}
    receipt = {"status": "PASS_TOKENIZATION_ONLY",
               "candidate_config_sha256": runner.file_sha256(candidate_path),
               "candidate_body_tokens": candidate["chunking"]["candidate_body_tokens"],
               "historical_128_alignment": {"status": "PASS"},
               "reference_config_sha256": config.source_sha256,
               "runtime_provenance": {"synthetic": True},
               "bindings": [{"path": str(contract.resolve()),
                             "sha256": runner.file_sha256(contract)}],
               "model": model, "documents": synthetic_plan["receipt"]["documents"],
               "planned_counts": synthetic_plan["receipt"]["planned_counts"]}
    receipt_path = tmp_path / "artifacts" / "synthetic_preflight.json"
    receipt_path.parent.mkdir()
    historical = tmp_path / "artifacts" / "chunk_map.jsonl"
    historical.write_text('{"synthetic_map":true}\n', encoding="utf-8")
    receipt["historical_128_alignment"]["historical_map_sha256"] = runner.file_sha256(historical)
    receipt["bindings"].append({"path": str(historical.resolve()),
                                "sha256": runner.file_sha256(historical)})
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    fresh_receipt = deepcopy(receipt)
    source = tmp_path / "artifacts" / "synthetic_decision_source.txt"
    source.write_text("Synthetic decision source fixture only.\n", encoding="utf-8")
    identity = runner._model_identity(receipt, config)
    versions = synthetic_plan["input_versions"]
    record = {"schema_version": 1, "kind": "small_chunk_decision_record",
              "decision_scope": decision.EXPECTED_SCOPE, "decision": "ACCEPT",
              "source_reference": {"path": "artifacts/synthetic_decision_source.txt",
                                   "sha256": runner.file_sha256(source)},
              "decided_at_utc": "2026-10-31T12:34:56Z",
              "candidate_sha256": runner.file_sha256(candidate_path),
              "input_manifest_sha256": decision.derive_input_manifest_sha256(
                  candidate["input"]["corpus_index_sha256"], versions),
              "input_versions": versions, "model_identity": identity,
              "metric_contract_sha256": runner.file_sha256(contract),
              "company_failure_policy": decision.COMPANY_FAILURE_POLICY}
    record_path = tmp_path / "artifacts" / "synthetic_decision.json"
    monkeypatch.setattr(runner, "capture_runtime_provenance",
                        lambda *_: {"required_provider": "CPUExecutionProvider"})
    monkeypatch.setattr(runner, "verify_runtime_provenance", lambda *_ , **__: None)
    monkeypatch.setattr(runner, "verify_receipt", lambda *_: None)
    monkeypatch.setattr(runner, "build_preflight", lambda *_: deepcopy(fresh_receipt))
    monkeypatch.setattr(runner, "load_config", lambda *_: config)
    monkeypatch.setattr(runner, "inspect_model_files", lambda *_: model)
    monkeypatch.setattr(runner, "validate_corpus", lambda *_:
                        (synthetic_plan["docs"], {"valid_count": 5}))
    return {"candidate": candidate_path, "corpus": corpus, "receipt": receipt_path,
            "decision": record_path, "record": record, "source": source,
            "contract": contract, "model": model}


def test_decision_states_are_checked_before_encoder_load(gate_case, monkeypatch, tmp_path):
    case = gate_case
    monkeypatch.setattr(runner.Encoder, "__init__", lambda *_:
                        pytest.fail("Encoder constructed during gate check"))
    args = (case["candidate"], case["corpus"], case["receipt"])
    output = tmp_path / "should_not_exist"
    with pytest.raises(runner.ProbeGateError, match="MISSING"):
        runner.run_candidate(*args, None, output)
    assert not output.exists()
    for value, expected in (("REJECT", "REJECTED"), ("NOT_PROVIDED", "MISSING")):
        record = deepcopy(case["record"])
        record["decision"] = value
        case["decision"].write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(runner.ProbeGateError, match=expected):
            runner.run_candidate(*args, case["decision"], output)
    for field, value, expected in (("decision_scope", "formal_finance", "WRONG_SCOPE"),
                                   ("candidate_sha256", "0" * 64, "VERSION_MISMATCH")):
        record = deepcopy(case["record"])
        record[field] = value
        case["decision"].write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(runner.ProbeGateError, match=expected):
            runner.run_candidate(*args, case["decision"], output)
    assert not output.exists()
    case["decision"].write_text(json.dumps(case["record"]), encoding="utf-8")
    assert runner.prepare_run(*args, case["decision"])["decision"]["status"] == "ACCEPTED"


def test_changed_input_contract_candidate_or_model_rejected_before_encoder(gate_case):
    case = gate_case
    case["decision"].write_text(json.dumps(case["record"]), encoding="utf-8")
    args = (case["candidate"], case["corpus"], case["receipt"], case["decision"])
    assert runner.prepare_run(*args)["decision"]["status"] == "ACCEPTED"
    case["corpus"].write_text('{"synthetic_fixture":false}\n', encoding="utf-8")
    with pytest.raises(runner.ProbeGateError, match="corpus index differs"):
        runner.prepare_run(*args)
    case["corpus"].write_text('{"synthetic_fixture":true}\n', encoding="utf-8")
    case["contract"].write_text("# Changed synthetic contract\n", encoding="utf-8")
    with pytest.raises(runner.ProbeGateError, match="Metric contract"):
        runner.prepare_run(*args)
    case["contract"].write_text("# Synthetic metric definition\n", encoding="utf-8")
    original = case["candidate"].read_bytes()
    case["candidate"].write_bytes(original + b" ")
    with pytest.raises(runner.ProbeGateError, match="another candidate version"):
        runner.prepare_run(*args)
    case["candidate"].write_bytes(original)
    case["model"]["files"]["tokenizer.json"]["sha256"] = "0" * 64
    with pytest.raises(runner.ProbeGateError, match="model/config identity"):
        runner.prepare_run(*args)


def test_edited_preflight_conclusions_cannot_reach_encoder(gate_case, monkeypatch, tmp_path):
    case = gate_case
    case["decision"].write_text(json.dumps(case["record"]), encoding="utf-8")
    original = json.loads(case["receipt"].read_text(encoding="utf-8"))
    monkeypatch.setattr(runner.Encoder, "__init__", lambda *_:
                        pytest.fail("Encoder constructed with edited preflight"))
    output = tmp_path / "no_real_model"
    for alter in ("planned_count", "map_digest", "invented_alignment"):
        edited = deepcopy(original)
        if alter == "planned_count":
            edited["planned_counts"]["chunk_vectors"] += 1
        elif alter == "map_digest":
            edited["documents"][0]["lengths"]["118"]["chunk_map_sha256"] = "0" * 64
        else:
            edited["bindings"] = [bound for bound in edited["bindings"]
                                  if Path(bound["path"]).name != "chunk_map.jsonl"]
        case["receipt"].write_text(json.dumps(edited), encoding="utf-8")
        with pytest.raises(runner.ProbeGateError, match=
                           "fresh tokenization|unique bound source file"):
            runner.run_candidate(case["candidate"], case["corpus"],
                                 case["receipt"], case["decision"], output)
        assert not output.exists()
