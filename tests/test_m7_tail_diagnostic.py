"""Synthetic software tests only; no company-result claims."""
import numpy as np
import pytest

from finembed.model import aggregate
from scripts.m7_tail_diagnostic import (weights_for_chunks, normalized_weights,
                                         peers_and_margin, cosine, main)


def test_full_non_tail_chunks_make_tail_ratio_algebraically_equal_to_body_weights():
    counts = [128, 128, 41]
    w = weights_for_chunks(counts, 128)
    assert w["equal_chunks"] == [1, 1, 1]
    assert w["tail_only_ratio"] == [1, 1, 41 / 128]
    assert normalized_weights(w["body_token_weighted"]) == pytest.approx(
        normalized_weights(w["tail_only_ratio"]))
    chunks = np.array([[1., 2.], [2., 1.], [3., 4.]])
    body = aggregate(chunks, True, True, weights=w["body_token_weighted"])
    tail = aggregate(chunks, True, True, weights=w["tail_only_ratio"])
    equal = aggregate(chunks, True, True)
    assert np.allclose(body, tail, atol=1e-14)
    assert not np.allclose(body, equal)


def test_incomplete_non_tail_chunk_rejects_tail_ratio_formula():
    for counts in ([127, 128, 41], [0, 128], [129, 20], []):
        with pytest.raises(ValueError):
            weights_for_chunks(counts, 128)


def test_single_chunk_drop_is_explicitly_undefined():
    w = weights_for_chunks([41], 128)
    assert w["drop_last_chunk_equal"] is None
    assert normalized_weights(w["drop_last_chunk_equal"]) is None


def test_top2_exact_tie_uses_ticker_and_records_zero_margin():
    tickers = ["A", "B", "C", "D", "E"]
    matrix = np.eye(5)
    matrix[0, 1:] = [.8, .7, .7, .3]
    matrix[1:, 0] = matrix[0, 1:]
    result = peers_and_margin(matrix, tickers)
    assert result["A"]["top2"] == ["B", "C"]
    assert result["A"]["rank3_ticker"] == "D"
    assert result["A"]["exact_cutoff_tie"] is True
    assert result["A"]["rank2_minus_rank3"] == 0


def test_cosine_recomputes_saved_unit_vector_geometry():
    v = np.array([[2., 0.], [1., 1.], [0., 3.]])
    m = cosine(v)
    assert np.allclose(np.diag(m), 1)
    assert m[0, 2] == 0
    assert np.allclose(m, m.T)


def test_missing_frozen_input_is_blocked_before_inference(tmp_path):
    output = tmp_path / "blocked"
    assert main(["--config", str(tmp_path / "missing.yaml"), "--corpus", str(tmp_path / "none.json"),
                 "--prior-dense", str(tmp_path), "--output", str(output)]) == 2
    import json
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "BLOCKED"
    assert "missing.yaml" in receipt["reason"]
