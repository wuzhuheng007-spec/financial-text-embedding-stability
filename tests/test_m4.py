"""Synthetic boundary checks; these are never represented as market observations."""
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import json

import numpy as np
import pytest

from finembed.m4 import (M4Error, MassiveClient, bars_url, fit_tfidf, load_m4_config,
                         match_identity, parse_bars, past_pca, return_matrix)


ROOT = Path(__file__).resolve().parents[1]
CFG = load_m4_config(ROOT / "configs" / "m4_demo.yaml", date(2025, 12, 31))


def test_tfidf_fits_only_passed_documents():
    docs = [{"item1_text": "alpha alpha beta"}, {"item1_text": "beta gamma"}]
    x, vocabulary, idf, counts = fit_tfidf(docs, CFG.token_pattern)
    assert vocabulary == ["alpha", "beta", "gamma"]
    assert counts == [3, 2]
    assert x.shape == (2, 3)
    assert np.allclose(np.linalg.norm(x, axis=1), 1)
    assert idf[0] > idf[1]


def test_missing_returns_stay_missing_and_pca_orientation():
    closes = {"A": {"2025-01-01": 10, "2025-01-02": 11, "2025-01-03": 12, "2025-01-04": 13},
              "B": {"2025-01-01": 20, "2025-01-03": 21, "2025-01-04": 22}}
    days, returns = return_matrix(closes, ["A", "B"])
    assert days == ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"]
    assert np.isnan(returns[0]).all()
    assert np.isnan(returns[1, 1]) and np.isnan(returns[2, 1])
    assert returns[3, 1] == pytest.approx(22 / 21 - 1)
    x = np.array([[1., 2., 4.], [2., 4., 3.], [4., 1., 2.], [3., 3., 1.]])
    pca = past_pca(x, 2, True, False)
    assert pca["input_shape"] == [4, 3]
    assert pca["loadings"].shape == (3, 2)
    assert pca["scores"].shape == (4, 2)
    full = past_pca(x, pca["effective_rank"], True, False)
    assert np.allclose(full["scores"] @ full["loadings"].T, x - x.mean(axis=0), atol=1e-10)
    with pytest.raises(M4Error, match="rank"):
        past_pca(np.array([[1., 1.], [2., 2.], [3., 3.]]), 2, True, False)


def test_identity_and_bar_validation():
    row = {"ticker": "AAPL", "cik": "0000320193", "market": "stocks", "share_class_figi": "X"}
    assert match_identity([{"results": [row]}], "AAPL", "0000320193")["cik"] == "0000320193"
    with pytest.raises(M4Error, match="mapping"):
        match_identity([{"results": [row]}], "AAPL", "0000000001")
    def bar(day, close):
        return {"t": int(datetime(2025, 1, day, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000),
                "o": close - 1, "h": close + 1, "l": close - 2, "c": close, "v": 100, "vw": close}
    pages = [{"ticker": "AAPL", "adjusted": True, "results": [bar(1, 10), bar(2, 11), bar(3, 12)]}]
    prices, retained = parse_bars(pages, "AAPL", CFG)
    assert prices["2025-01-02"] == 11
    assert retained[1]["v"] == 100
    with pytest.raises(M4Error, match="flag"):
        parse_bars([{"ticker": "AAPL", "adjusted": False, "results": pages[0]["results"]}], "AAPL", CFG)


def test_client_preserves_synthetic_raw_pages_and_safe_pagination(tmp_path):
    class Response:
        status = 200
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, n): return self.body[:n]
    calls = []
    def opener(req, timeout):
        calls.append(req.full_url)
        assert req.get_header("Authorization") == "Bearer synthetic-test-key"
        payload = {"status": "OK", "results": [], "next_url": "https://api.massive.com/page2" if len(calls) == 1 else None}
        return Response(json.dumps(payload).encode())
    outputs = []
    run = SimpleNamespace(path=tmp_path, output=lambda p: outputs.append(p))
    client = MassiveClient("synthetic-test-key", CFG, run, opener=opener, sleep=lambda _: None)
    assert len(client.pages("https://api.massive.com/page1", "synthetic")) == 2
    raw = list((tmp_path / "market" / "raw" / "synthetic").glob("*.json"))
    assert len(raw) == 4  # Two raw pages plus two receipts.
    assert len(outputs) == 4
    with pytest.raises(M4Error, match="Credential"):
        client.pages("https://api.massive.com/page1?apiKey=secret", "synthetic")
