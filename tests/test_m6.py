"""Synthetic software fixtures only. No SEC download or financial finding."""
from pathlib import Path
import json
import math

import numpy as np
import pytest

from finembed.extract import primary_text, ExtractionError
from finembed.lexical import BM25, tokenize
from finembed.m6 import load_sections, main
from finembed.model import aggregate, ModelError
from finembed.run import sha256


def test_bm25_matches_hand_calculated_single_term():
    model = BM25([["cloud", "cloud"], ["retail"]], k1=1.2, b=.75)
    expected = math.log(2) * (2 * 2.2) / (2 + 1.2 * (.25 + .75 * 2 / 1.5))
    assert model.scores(["cloud"]) == pytest.approx([expected, 0])
    assert model.scores(["cloud", "cloud"]) == model.scores(["cloud"])
    assert model.scores(["unseen"]) == [0, 0]


def test_length_control_b0_and_b1_and_tf_saturation():
    docs = [["x", "y"], ["x", "y", "z", "z", "z"]]
    no_length = BM25(docs, b=0).scores(["x"])
    corrected = BM25(docs, b=1).scores(["x"])
    assert no_length[0] == no_length[1]
    assert corrected[0] > corrected[1]
    scores = BM25([["x"], ["x"] * 2, ["x"] * 3], b=0).scores(["x"])
    assert 0 < scores[2]-scores[1] < scores[1]-scores[0]


@pytest.mark.parametrize("kwargs", [{"b":-1}, {"b":1.1}, {"b":float('nan')}, {"k1":0}, {"k1":float('inf')}])
def test_invalid_bm25_parameters(kwargs):
    with pytest.raises(ValueError):
        BM25([["x"]], **kwargs)


def test_empty_inputs_rejected_and_tokenizer_frozen():
    assert tokenize("Cloud-Services $123. AAPL") == ["cloud", "services", "123", "aapl"]
    for docs in [[], [[]], [[""]]]:
        with pytest.raises(ValueError):
            BM25(docs)
    with pytest.raises(ValueError):
        BM25([["x"]]).scores([])


def test_bm25_score_direction_and_termweight_geometry():
    model = BM25([["a"], ["a", "b", "b"], ["c"]])
    retrieval = np.array([model.scores(q) for q in [["a"], ["a", "b", "b"], ["c"]]])
    cosine = np.array(model.termweight_cosine())
    assert not np.allclose(retrieval, retrieval.T)
    assert np.allclose(cosine, cosine.T)
    assert np.allclose(np.diag(cosine), 1)
    assert cosine[0, 2] == 0
    assert np.linalg.eigvalsh(cosine).min() >= -1e-12


def test_weighted_pooling_tail_and_backward_compatibility():
    chunks = np.array([[1., 0], [0, 1.]])
    assert np.allclose(aggregate(chunks, False, False), [.5, .5])
    assert np.allclose(aggregate(chunks, False, False, weights=[4, 1]), [.8, .2])
    assert np.allclose(aggregate(chunks, True, True), aggregate(chunks, True, True, weights=[1, 1]))
    assert np.allclose(aggregate(chunks, False, True, weights=[4, 1]), np.array([4., 1.])/np.sqrt(17))
    # A scalar document rescaling cannot change its cosine direction.
    x = aggregate(chunks, False, False, weights=[4, 1])
    assert np.allclose(x/np.linalg.norm(x), (x/5)/np.linalg.norm(x/5))


@pytest.mark.parametrize("weights", [[0,1], [-1,1], [1], [1,float('nan')], [1,float('inf')]])
def test_invalid_chunk_weights(weights):
    with pytest.raises(ModelError):
        aggregate([[1, 0], [0, 1]], True, True, weights=weights)


def section_fixture(tmp_path, form="10-Q", count=4):
    entries = []
    for i in range(count):
        accession = f"000000000{i+1}-25-000001"
        paragraph = (f"SYNTHETIC SOFTWARE TEST company {i} cloud retail risk. " * 70)
        raw = (f"<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20250102120000\nACCESSION NUMBER: {accession}\n"
               f"CONFORMED SUBMISSION TYPE: {form}\nFILED AS OF DATE: 20250102\n</SEC-HEADER>\n"
               f"<DOCUMENT>\n<TYPE>{form}\n<TEXT><html><p>{paragraph}</p></html></TEXT>\n</DOCUMENT>").encode()
        text, _ = primary_text(raw, form)
        raw_path, text_path = tmp_path/f"{i}.raw", tmp_path/f"{i}.txt"
        raw_path.write_bytes(raw); text_path.write_bytes(text.encode("utf-8"))
        entries.append({"ticker": f"TEST{i}", "cik": f"{i+1:010d}", "accession": accession, "form": form,
                        "section": "PART_I_ITEM_2_MDA" if form == "10-Q" else "ITEM_1_BUSINESS",
                        "raw_path": raw_path.name, "raw_sha256": sha256(raw), "text_path": text_path.name,
                        "text_sha256": sha256(text.encode()), "start_char":0, "end_char_exclusive":len(text),
                        "boundary_reviewed":True})
    path = tmp_path/"sections.json"
    path.write_text(json.dumps({"entries":entries}), encoding="utf-8")
    return path, entries


def test_q_form_adapter_and_wrong_section_and_hash_failure(tmp_path):
    path, entries = section_fixture(tmp_path)
    docs, audit = load_sections(path)
    assert len(docs) == 4 and audit["historical_public_availability"] == "NOT_VERIFIED"
    with pytest.raises(ExtractionError):
        primary_text((tmp_path/"0.raw").read_bytes())
    entries[0]["section"] = "ITEM_1_BUSINESS"
    path.write_text(json.dumps({"entries":entries}))
    with pytest.raises(ValueError, match="NOT Business"):
        load_sections(path)
    entries[0]["section"] = "PART_I_ITEM_2_MDA"
    path.write_text(json.dumps({"entries":entries}))
    (tmp_path/"0.txt").write_text("tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        load_sections(path)


def test_unreviewed_duplicate_and_mixed_strata_rejected(tmp_path):
    path, entries = section_fixture(tmp_path)
    entries[0]["boundary_reviewed"] = False
    path.write_text(json.dumps({"entries":entries}))
    with pytest.raises(ValueError, match="attestation"):
        load_sections(path)
    entries[0]["boundary_reviewed"] = True
    path.write_text(json.dumps({"entries":[entries[0], entries[0]]}))
    with pytest.raises(ValueError, match="Duplicate"):
        load_sections(path)
    entries[0]["section"] = "PART_II_ITEM_1A_RISK_FACTORS"
    path.write_text(json.dumps({"entries":entries}))
    with pytest.raises(ValueError, match="stratum"):
        load_sections(path)


def test_m6_synthetic_cli_receipts_and_no_overwrite(tmp_path):
    path, entries = section_fixture(tmp_path)
    output = tmp_path/"run"
    assert main(["--section-index",str(path),"--output",str(output)]) == 0
    receipt = json.loads((output/"receipt.json").read_text())
    assert receipt["status"] == "DONE" and receipt["dense_status"] == "NOT_REQUESTED"
    assert receipt["financial_experiment"] == "NOT_RUN"
    assert receipt["row_text_hashes"] == [r["text_sha256"] for r in entries]
    assert all(sha256((output/name).read_bytes()) == row["sha256"] for name,row in receipt["outputs"].items())
    with pytest.raises(FileExistsError):
        main(["--section-index",str(path),"--output",str(output)])


def test_missing_real_corpus_records_blocker_without_results(tmp_path):
    output=tmp_path/"blocked"
    assert main(["--section-index",str(tmp_path/"absent.json"),"--output",str(output)]) == 2
    receipt=json.loads((output/"receipt.json").read_text())
    assert receipt["status"] == "BLOCKED"
    assert list(receipt["outputs"]) == []


def test_dense_pipeline_wiring_with_explicit_synthetic_encoder(tmp_path, monkeypatch):
    from types import SimpleNamespace
    class SyntheticEncoder:
        identity = {"model_id":"SYNTHETIC_SOFTWARE_TEST_NOT_A_RESEARCH_MODEL"}
        def __init__(self, config): pass
        def chunks(self, text, length):
            return [SimpleNamespace(token_ids=(1,2),token_end_exclusive=2),
                    SimpleNamespace(token_ids=(3,),token_end_exclusive=3)]
        def embed_chunks(self, chunks): return np.array([[1.,0.],[0.,1.]])
    monkeypatch.setattr("finembed.m6.Encoder", SyntheticEncoder)
    path, _ = section_fixture(tmp_path)
    output=tmp_path/"synthetic-dense"
    assert main(["--section-index",str(path),"--dense","--output",str(output)]) == 0
    receipt=json.loads((output/"receipt.json").read_text())
    assert receipt["dense_status"] == "DONE"
    assert receipt["model_identity"]["model_id"].startswith("SYNTHETIC")
    equal=np.load(output/"dense_vectors_128_equal_chunks.npy")
    weighted=np.load(output/"dense_vectors_128_body_token_weighted.npy")
    assert np.allclose(equal[0],np.array([1.,1.])/np.sqrt(2))
    assert np.allclose(weighted[0],np.array([2.,1.])/np.sqrt(5))


def test_partial_dense_failure_cannot_report_full_success(tmp_path, monkeypatch):
    def unavailable(config): raise ModelError("SYNTHETIC missing model test")
    monkeypatch.setattr("finembed.m6.Encoder", unavailable)
    path,_=section_fixture(tmp_path)
    output=tmp_path/"partial"
    assert main(["--section-index",str(path),"--dense","--output",str(output)]) == 2
    receipt=json.loads((output/"receipt.json").read_text())
    assert receipt["status"] == receipt["dense_status"] == "BLOCKED"
    assert (output/"lexical_metrics.json").exists()
    assert not (output/"dense_metrics.json").exists()
