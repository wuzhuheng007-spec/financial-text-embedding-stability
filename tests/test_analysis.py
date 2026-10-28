"""Synthetic numerical fixtures; no financial finding or independent seed claim."""
from pathlib import Path
from dataclasses import replace
import json

import numpy as np
import pytest

from finembed.analysis import topk, upper_triangle_spearman, orthogonal_check, validate_corpus, _save_arm
from finembed.config import load_config
from finembed.cli import ingest
from finembed.model import Encoder
from finembed.run import Run, sha256
from finembed.sec import MissingContact
from finembed.sec import raw_header
from finembed.extract import primary_text, extract_item1, comparison


def test_topk_ties_are_resolved_by_ticker_and_self_is_excluded():
    tickers = ["ZZZ", "AAA", "BBB"]
    matrix = np.array([[1, .5, .5], [.5, 1, .2], [.5, .2, 1]])
    result = topk(matrix, tickers, 1)
    assert result["ZZZ"][0]["ticker"] == "AAA"
    assert result["AAA"][0]["ticker"] == "ZZZ"


def test_spearman_exact_ties_and_invalid_values():
    a = np.array([[1, .2, .2], [.2, 1, .8], [.2, .8, 1.]])
    b = np.array([[1, .1, .1], [.1, 1, .9], [.1, .9, 1.]])
    result = upper_triangle_spearman(a, b)
    assert result["value"] == pytest.approx(1)
    assert result["pair_count"] == 3
    assert upper_triangle_spearman(np.eye(2), np.eye(2))["value"] is None
    a[0, 1] = np.nan
    assert upper_triangle_spearman(a, b)["reason"].startswith("nonfinite")


def test_orthogonal_reflection_preserves_gram_and_cosine():
    vectors = np.array([[1., 2., 3.], [4., 2., 1.], [2., 3., 5.]])
    norms = vectors / np.linalg.norm(vectors, axis=1)[:, None]
    result = orthogonal_check(vectors, norms @ norms.T)
    assert result["passed"]
    assert result["gram_max_abs"] < 1e-10


def test_blocked_m1_index_cannot_enter_analysis(tmp_path):
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    index = tmp_path / "corpus_index.json"
    index.write_text(json.dumps({"as_of": config.as_of.isoformat(), "first_run_symbol_limit": None,
                                 "entries": [{"ticker": c.ticker, "status": "BLOCKED", "reason": "synthetic missing contact"}
                                             for c in config.companies]}), encoding="utf-8")
    valid, audit = validate_corpus(config, index)
    assert valid == [] and audit["valid_count"] == 0
    assert len(audit["entries"]) == len(config.companies)


def test_m1_corpus_rejects_index_and_raw_filer_cik_mismatches(tmp_path):
    """A synthetic, internally consistent filing proves the identity gates."""
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    cik = "0000320193"
    accession = "0000000001-25-000001"
    html = ("<html><body><p>Cover " + "cover " * 400 + "</p>"
            "<p>TABLE OF CONTENTS</p><p>Item 1. Business</p><p>Item 1A. Risk Factors</p>"
            "<h2>Item 1. Business</h2><p>" + "Synthetic business description. " * 120 + "</p>"
            "<h2>Item 1A. Risk Factors</h2><p>" + "Synthetic risk text. " * 100 + "</p></body></html>")
    def submission(filer_cik):
        return (f"<SEC-DOCUMENT>\n<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20251201090000\n"
                f"ACCESSION NUMBER: {accession}\nCONFORMED SUBMISSION TYPE: 10-K\n"
                f"FILED AS OF DATE: 20251201\nFILER:\n\tCOMPANY DATA:\n"
                f"\t\tCENTRAL INDEX KEY: {filer_cik}\n</SEC-HEADER>\n"
                f"<DOCUMENT><TYPE>10-K\n<TEXT>{html}</TEXT></DOCUMENT>").encode()
    raw = submission(cik)
    primary, _ = primary_text(raw)
    item, offsets = extract_item1(primary)
    folder = tmp_path / "filing"
    folder.mkdir()
    metadata = {"ticker": "AAPL", "cik": cik, "accession": accession,
                "archive_url": "https://www.sec.gov/Archives/edgar/data/320193/synthetic",
                "raw_sec_header": raw_header(raw, accession), "raw_sha256": sha256(raw)}
    item_meta = {"primary_text_sha256": sha256(primary.encode()), "item1_sha256": sha256(item.encode()),
                 "start_char": offsets["start_char"], "end_char_exclusive": offsets["end_char_exclusive"]}
    (folder / "original_submission.txt").write_bytes(raw)
    (folder / "primary_text.txt").write_text(primary, encoding="utf-8")
    (folder / "item1_business.txt").write_text(item, encoding="utf-8")
    (folder / "filing_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (folder / "item1_metadata.json").write_text(json.dumps(item_meta), encoding="utf-8")
    (folder / "boundary_comparison.json").write_text(json.dumps(comparison(primary, item, offsets)), encoding="utf-8")
    entries = [{"ticker": "AAPL", "cik": cik, "accession": accession, "status": "INCLUDED",
                "filing_dir": str(folder), "raw_sha256": sha256(raw), "item1_sha256": sha256(item.encode())}]
    entries.extend({"ticker": c.ticker, "status": "BLOCKED", "reason": "synthetic excluded"}
                   for c in config.companies[1:])
    index = tmp_path / "corpus_index.json"
    def save_index():
        index.write_text(json.dumps({"as_of": config.as_of.isoformat(), "first_run_symbol_limit": None,
                                     "entries": entries}), encoding="utf-8")
    save_index()
    valid, audit = validate_corpus(config, index)
    assert len(valid) == 1 and audit["entries"][0]["status"] == "VALID"
    entries[0]["cik"] = "9999999999"
    save_index()
    valid, audit = validate_corpus(config, index)
    assert not valid and "Filing identity" in audit["entries"][0]["reason"]
    entries[0]["cik"] = cik
    wrong_raw = submission("9999999999")
    (folder / "original_submission.txt").write_bytes(wrong_raw)
    metadata["raw_sha256"] = entries[0]["raw_sha256"] = sha256(wrong_raw)
    (folder / "filing_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    save_index()
    valid, audit = validate_corpus(config, index)
    assert not valid and "Raw SEC FILER CIK" in audit["entries"][0]["reason"]


def test_released_m1_limit_records_every_planned_company(monkeypatch, tmp_path):
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    assert config.first_run_symbol_limit is None
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    run = Run(replace(config, artifacts=tmp_path), "SYNTHETIC_BLOCKING_TEST")
    with pytest.raises(MissingContact):
        ingest(run)
    index = json.loads((run.path / "corpus_index.json").read_text())
    assert [x["ticker"] for x in index["entries"]] == [c.ticker for c in config.companies]
    assert all(x["status"] == "BLOCKED" for x in index["entries"])


def test_real_pinned_tokenizer_does_not_truncate_whole_document():
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    if not (config.model_cache / "onnx" / "model.onnx").is_file():
        pytest.skip("Pinned model not downloaded; model-fetch must verify it separately")
    encoder = Encoder(config)
    text = "Synthetic Item 1 Business. " * 200
    chunks128 = encoder.chunks(text, 128)
    chunks192 = encoder.chunks(text, 192)
    assert chunks128[-1].char_end_exclusive >= len(text.rstrip()) - 2
    assert chunks128[-1].token_end_exclusive == chunks192[-1].token_end_exclusive
    assert len(chunks128) > len(chunks192) > 1
    assert max(len(c.token_ids) + encoder.special_count for c in chunks192) <= encoder.identity["max_input_tokens"]
    vectors = encoder.embed_chunks(chunks128[:2])
    assert vectors.shape == (2, 384) and np.isfinite(vectors).all()


def test_synthetic_text_three_arm_output_with_real_pretrained_model(tmp_path):
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    if not (config.model_cache / "onnx" / "model.onnx").is_file():
        pytest.skip("Pinned model not downloaded")
    config = replace(config, artifacts=tmp_path)
    run = Run(config, "SYNTHETIC_TEXT_INTEGRATION_TEST")
    encoder = Encoder(config)
    corpus = []
    for ticker, phrase in (("AAA", "synthetic products and services"),
                           ("BBB", "synthetic cloud infrastructure"),
                           ("CCC", "synthetic digital advertising")):
        text = (phrase + ". ") * 120
        corpus.append({"ticker": ticker, "cik": "SYNTHETIC", "accession": "SYNTHETIC",
                       "item1_text": text, "item1_sha256": sha256(text.encode())})
    base_v, base_c, _ = _save_arm(run, encoder, corpus, 128, "base_128")
    repeat_v, repeat_c, _ = _save_arm(run, encoder, corpus, 128, "repeat_128")
    alt_v, alt_c, _ = _save_arm(run, encoder, corpus, 192, "alternate_192")
    assert np.max(np.abs(base_v - repeat_v)) <= config.repeat_tolerance
    assert np.max(np.abs(base_c - repeat_c)) <= config.repeat_tolerance
    assert base_v.shape == alt_v.shape == (3, 384)
    assert upper_triangle_spearman(base_c, alt_c)["pair_count"] == 3
    assert orthogonal_check(base_v, base_c)["passed"]
    mapping = (run.path / "analysis" / "base_128" / "chunk_map.jsonl").read_text().splitlines()
    assert mapping and len(mapping) > 3
    assert all(json.loads(line)["model_input_tokens_including_specials"] <= 256 for line in mapping)

