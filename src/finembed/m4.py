"""Auditable M4 market-price baseline and an Item 1 TF-IDF control."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import json
import math
import os
import re
import time

import numpy as np
import yaml

from .analysis import validate_corpus
from .run import Run, sha256, write_json


class M4Error(RuntimeError):
    pass


@dataclass(frozen=True)
class M4Config:
    source: Path
    sha256: str
    start: date
    end: date
    adjusted: bool
    price_field: str
    max_pages: int
    interval: float
    attempts: int
    timeout: int
    components: int
    center: bool
    scale: bool
    token_pattern: str


def load_m4_config(path: Path, as_of: date) -> M4Config:
    source = path.resolve(strict=True)
    raw = source.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise M4Error("M4 config must be a YAML mapping")
    market, pca, tfidf = (data[k] for k in ("market", "pca", "tfidf"))
    if any(not isinstance(x, dict) for x in (market, pca, tfidf)):
        raise M4Error("M4 sections must be mappings")
    if market.get("vendor") != "Massive" or market.get("price_field") != "c":
        raise M4Error("M4 demo requires Massive daily closing price c")
    if not all(type(x) is bool for x in (market.get("adjusted"), pca.get("center"), pca.get("scale"),
                                         tfidf.get("lowercase"), tfidf.get("smooth_idf"), tfidf.get("l2_normalize"))):
        raise M4Error("M4 boolean settings must be explicit")
    if not (tfidf["lowercase"] and tfidf["smooth_idf"] and tfidf["l2_normalize"]):
        raise M4Error("TF-IDF demo requires lowercase, smooth IDF and L2 normalization")
    out = M4Config(source, sha256(raw), date.fromisoformat(str(market["window_start"])),
                   date.fromisoformat(str(market["window_end"])), market["adjusted"],
                   market["price_field"], int(market["max_pages_per_request"]),
                   float(market["min_interval_seconds"]), int(market["max_attempts"]),
                   int(market["timeout_seconds"]), int(pca["components"]), pca["center"],
                   pca["scale"], str(tfidf["token_pattern"]))
    if not (out.start < out.end <= as_of and 1 <= out.components <= 10 and 1 <= out.max_pages <= 100):
        raise M4Error("Window, K or pagination limits invalid or beyond as_of")
    if not (out.interval >= 12 and 1 <= out.attempts <= 5 and 1 <= out.timeout <= 120):
        raise M4Error("Market rate/retry/timeout limits invalid")
    if out.token_pattern != "[a-z][a-z0-9]+":
        raise M4Error("Unknown TF-IDF token pattern")
    return out


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "api.massive.com" or parsed.username or parsed.password:
        raise M4Error("Unexpected Massive page host or scheme")
    if any(k.lower() in {"apikey", "token", "key"} for k in parse_qs(parsed.query)):
        raise M4Error("Credential in Massive page URL is forbidden")
    return url


class MassiveClient:
    def __init__(self, key: str, config: M4Config, run: Run, opener=urlopen, sleep=time.sleep):
        if not key or not key.strip():
            raise M4Error("MASSIVE_API_KEY is absent")
        self.key = key.strip()
        self.config = config
        self.run = run
        self.opener = opener
        self.sleep = sleep
        self.last_request = 0.0

    def pages(self, url: str, label: str) -> list[dict]:
        result, seen = [], set()
        for page in range(1, self.config.max_pages + 1):
            url = _safe_url(url)
            if url in seen:
                raise M4Error("Massive pagination cycle")
            seen.add(url)
            payload = self._one(url, label, page)
            result.append(payload)
            next_url = payload.get("next_url")
            if not next_url:
                return result
            if not isinstance(next_url, str):
                raise M4Error("Invalid Massive next_url")
            url = next_url
        raise M4Error("Massive page limit reached before pagination ended")

    def _one(self, url: str, label: str, page: int) -> dict:
        for attempt in range(1, self.config.attempts + 1):
            path = self.run.path / "market" / "raw" / label / f"page_{page:03d}_attempt_{attempt:02d}.json"
            receipt = path.with_suffix(".receipt.json")
            gap = self.config.interval - (time.monotonic() - self.last_request)
            if gap > 0:
                self.sleep(gap)
            self.last_request = time.monotonic()
            request = Request(url, headers={"Authorization": f"Bearer {self.key}",
                                            "Accept": "application/json", "User-Agent": "finembed-research/0.1"})
            try:
                with self.opener(request, timeout=self.config.timeout) as response:
                    status = response.status
                    body = response.read(10_000_001)
            except HTTPError as exc:
                status = exc.code
                body = exc.read(10_000_001)
            except (URLError, TimeoutError) as exc:
                if attempt == self.config.attempts:
                    raise M4Error(f"Massive transport failed: {type(exc).__name__}") from None
                continue
            if len(body) > 10_000_000:
                raise M4Error("Massive response exceeds 10 MB")
            if self.key.encode() in body:
                raise M4Error("Massive response unexpectedly contains credential; refused to persist")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            self.run.output(path)
            meta = {"url": url, "http_status": status, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                    "sha256": sha256(body), "bytes": len(body), "page": page, "attempt": attempt,
                    "provider": "Massive REST", "raw_path": str(path)}
            write_json(receipt, meta)
            self.run.output(receipt)
            if status in (401, 403):
                raise M4Error(f"Massive authorization/entitlement HTTP {status}; raw response retained")
            if status == 429 or 500 <= status < 600:
                if attempt < self.config.attempts:
                    continue
                raise M4Error(f"Massive HTTP {status} after bounded retries; raw response retained")
            if status != 200:
                raise M4Error(f"Massive HTTP {status}; raw response retained")
            try:
                payload = json.loads(body)
            except ValueError:
                raise M4Error("Massive response is not JSON") from None
            if not isinstance(payload, dict) or payload.get("status") not in ("OK", "DELAYED"):
                raise M4Error("Massive response status is not OK")
            return payload
        raise AssertionError("unreachable")


def reference_url(ticker: str, as_of: date) -> str:
    return "https://api.massive.com/v3/reference/tickers?" + urlencode(
        {"ticker": ticker, "date": as_of.isoformat(), "market": "stocks", "limit": 100})


def bars_url(ticker: str, config: M4Config) -> str:
    path = f"/v2/aggs/ticker/{ticker}/range/1/day/{config.start}/{config.end}"
    return "https://api.massive.com" + path + "?" + urlencode(
        {"adjusted": str(config.adjusted).lower(), "sort": "asc", "limit": 50000})


def match_identity(pages: list[dict], ticker: str, sec_cik: str) -> dict:
    rows = [row for page in pages for row in page.get("results", [])
            if isinstance(row, dict) and row.get("ticker") == ticker and row.get("market") == "stocks"]
    if len(rows) != 1 or str(rows[0].get("cik", "")).zfill(10) != sec_cik:
        raise M4Error("Massive ticker/date/CIK mapping is missing, ambiguous or differs from SEC")
    return {k: rows[0].get(k) for k in ("ticker", "cik", "composite_figi", "share_class_figi",
                                        "market", "locale", "type", "active", "last_updated_utc")}


def parse_bars(pages: list[dict], ticker: str, config: M4Config) -> tuple[dict[str, float], list[dict]]:
    closes, retained = {}, []
    for page in pages:
        if page.get("ticker") != ticker or page.get("adjusted") is not config.adjusted:
            raise M4Error("Massive bars ticker or split-adjustment flag differs")
        for row in page.get("results", []):
            if not isinstance(row, dict) or type(row.get("t")) is not int:
                raise M4Error("Malformed Massive bar timestamp")
            day = datetime.fromtimestamp(row["t"] / 1000, ZoneInfo("America/New_York")).date()
            if not config.start <= day <= config.end or day.isoformat() in closes:
                raise M4Error("Massive bar outside configured past window or duplicate date")
            close = row.get(config.price_field)
            if type(close) not in (int, float) or not math.isfinite(close) or close <= 0:
                raise M4Error("Massive bar lacks a positive finite close")
            closes[day.isoformat()] = float(close)
            retained.append({"date_et": day.isoformat(), **row})  # Keep all provider price fields.
    if len(closes) < 3:
        raise M4Error("Fewer than three historical daily bars")
    return closes, sorted(retained, key=lambda x: x["date_et"])


def return_matrix(closes: dict[str, dict[str, float]], tickers: list[str]):
    """Union-date close-to-close returns; a missing current or prior bar stays NaN."""
    dates = sorted({d for ticker in tickers for d in closes[ticker]})
    matrix = np.full((len(dates), len(tickers)), np.nan, dtype=np.float64)
    for j, ticker in enumerate(tickers):
        for i in range(1, len(dates)):
            if dates[i] in closes[ticker] and dates[i - 1] in closes[ticker]:
                matrix[i, j] = closes[ticker][dates[i]] / closes[ticker][dates[i - 1]] - 1.0
    return dates, matrix


def past_pca(matrix: np.ndarray, k: int, center: bool, scale: bool) -> dict:
    x = np.asarray(matrix, dtype=np.float64)
    if x.ndim != 2 or min(x.shape) < 2 or not np.isfinite(x).all():
        raise M4Error("PCA requires finite T×N matrix with T,N >= 2")
    mean = x.mean(axis=0) if center else np.zeros(x.shape[1])
    centered = x - mean
    std = centered.std(axis=0, ddof=0) if scale else np.ones(x.shape[1])
    if (std <= 0).any():
        raise M4Error("PCA scaling encountered a constant security")
    z = centered / std
    rank = int(np.linalg.matrix_rank(z))
    if not 1 <= k <= rank:
        raise M4Error(f"Requested K={k} exceeds effective rank={rank}")
    u, s, vt = np.linalg.svd(z, full_matrices=False)
    return {"loadings": vt[:k].T, "scores": u[:, :k] * s[:k], "mean": mean,
            "scale": std, "singular_values": s, "effective_rank": rank, "k": k,
            "centered": center, "scaled": scale, "input_shape": list(x.shape)}


def fit_tfidf(documents: list[dict], pattern: str):
    tokens = [re.findall(pattern, row["item1_text"].lower()) for row in documents]
    counts = [Counter(x) for x in tokens]
    vocabulary = sorted({term for counter in counts for term in counter})
    if not vocabulary:
        raise M4Error("Empty Item 1 TF-IDF vocabulary")
    n = len(documents)
    df = Counter(term for counter in counts for term in counter)
    idf = np.array([math.log((1 + n) / (1 + df[t])) + 1 for t in vocabulary])
    x = np.array([[counter.get(t, 0) for t in vocabulary] for counter in counts], dtype=np.float64) * idf
    norms = np.linalg.norm(x, axis=1)
    if (norms <= 0).any():
        raise M4Error("Empty TF-IDF document")
    x /= norms[:, None]
    return x, vocabulary, idf, [len(t) for t in tokens]


def execute(run: Run, m4_config_path: Path, corpus_path: Path) -> tuple[str, str]:
    cfg = load_m4_config(m4_config_path, run.config.as_of)
    run.input(cfg.source)
    run.input(corpus_path)
    run.manifest["m4_config_sha256"] = cfg.sha256
    run.save()
    documents, audit = validate_corpus(run.config, corpus_path)
    audit_path = run.path / "m4_corpus_validation.json"
    write_json(audit_path, audit)
    run.output(audit_path)
    if len(documents) != len(run.config.companies):
        raise M4Error("M4 requires the full validated demo corpus")
    for row in documents:
        run.input(Path(row["files"]["item1_business.txt"]["path"]))
    tickers = [d["ticker"] for d in documents]
    tfidf, vocabulary, idf, lengths = fit_tfidf(documents, cfg.token_pattern)
    folder = run.path / "tfidf"
    folder.mkdir()
    np.save(folder / "vectors.npy", tfidf)
    np.save(folder / "cosine.npy", tfidf @ tfidf.T)
    write_json(folder / "vocabulary.json", {"terms": vocabulary, "idf": idf.tolist()})
    write_json(folder / "fit_boundary.json", {"fit_scope": "only the five validated M1 Item 1 Business texts",
        "as_of": run.config.as_of.isoformat(), "corpus_index_sha256": audit["corpus_index_sha256"],
        "ticker_order": tickers, "item1_inputs": [{"ticker": d["ticker"], "cik": d["cik"],
        "accession": d["accession"], "item1_sha256": d["item1_sha256"]} for d in documents],
        "token_pattern": cfg.token_pattern, "lowercase": True, "term_frequency": "raw count",
        "idf": "log((1+n_documents)/(1+document_frequency))+1", "l2_normalize": True,
        "token_counts": lengths, "vocabulary_size": len(vocabulary),
        "interpretation": "descriptive same-corpus text control; no out-of-sample fit or financial incremental-value claim"})
    for path in folder.iterdir():
        run.output(path)
    key = os.environ.get("MASSIVE_API_KEY")
    if not key:
        write_json(run.path / "market_access.json", {"status": "BLOCKED", "key_present": False,
            "actual_account_entitlement": "UNVERIFIED", "reason": "MASSIVE_API_KEY absent in process environment",
            "provider": "Massive", "requested_endpoints": ["/v3/reference/tickers", "/v2/aggs/ticker/.../range/1/day/..."],
            "window_start": cfg.start.isoformat(), "window_end": cfg.end.isoformat()})
        run.output(run.path / "market_access.json")
        write_json(run.path / "common_masks.json", {"ticker_order": tickers, "company_price_available": [False] * len(tickers),
            "date_order": [], "complete_date_mask": [], "reason": "Market authorization unverified; no price observations"})
        run.output(run.path / "common_masks.json")
        write_json(run.path / "missing_exclusions.json", {"entries": [{"ticker": t, "price_status": "BLOCKED",
            "reason": "MASSIVE_API_KEY absent; no source substitution"} for t in tickers], "price_observation_count": 0})
        run.output(run.path / "missing_exclusions.json")
        return "BLOCKED", "TF-IDF completed; Massive account entitlement and real price data unverified without MASSIVE_API_KEY"
    client = MassiveClient(key, cfg, run)
    closes, ledger = {}, []
    authorization_failure = False
    for doc in documents:
        ticker = doc["ticker"]
        try:
            refs = client.pages(reference_url(ticker, run.config.as_of), f"reference_{ticker}")
            identity = match_identity(refs, ticker, doc["cik"])
            write_json(run.path / "market" / f"identity_{ticker}.json", {"sec_cik": doc["cik"],
                "massive": identity, "as_of": run.config.as_of.isoformat(),
                "reference_raw_pages": [str(p) for p in sorted((run.path / "market" / "raw" /
                    f"reference_{ticker}").glob("page_*.json")) if not p.name.endswith(".receipt.json")]})
            run.output(run.path / "market" / f"identity_{ticker}.json")
            pages = client.pages(bars_url(ticker, cfg), f"bars_{ticker}")
            closes[ticker], bars = parse_bars(pages, ticker, cfg)
            write_json(run.path / "market" / f"bars_{ticker}.json", {"ticker": ticker,
                "data_version": "Massive REST retrieval receipt and per-page request_id; historical revision snapshot unavailable",
                "adjusted": cfg.adjusted, "adjustment_scope": "splits only; excludes dividend total return",
                "price_field": cfg.price_field, "response_request_ids": [x.get("request_id") for x in pages],
                "raw_page_receipts": [str(p) for p in sorted((run.path / "market" / "raw" /
                    f"bars_{ticker}").glob("*.receipt.json"))], "bars": bars})
            run.output(run.path / "market" / f"bars_{ticker}.json")
            ledger.append({"ticker": ticker, "status": "INCLUDED", "bar_count": len(bars)})
        except M4Error as exc:
            ledger.append({"ticker": ticker, "status": "EXCLUDED", "reason": str(exc)})
            if "authorization/entitlement HTTP" in str(exc):
                authorization_failure = True
                break
    attempted = {x["ticker"] for x in ledger}
    ledger.extend({"ticker": t, "status": "BLOCKED", "reason": "Authorization failure on prior fixed-pool request"}
                  for t in tickers if t not in attempted)
    write_json(run.path / "market_access.json", {"status": "BLOCKED" if authorization_failure else "OBSERVED",
        "key_present": True,
        "actual_account_entitlement": "Endpoint responses only; dashboard subscription tier unverified",
        "endpoint_results": ledger})
    run.output(run.path / "market_access.json")
    available = [t in closes for t in tickers]
    if not all(available):
        write_json(run.path / "common_masks.json", {"ticker_order": tickers, "company_price_available": available,
            "date_order": [], "complete_date_mask": [], "reason": "One or more fixed-pool securities excluded"})
        run.output(run.path / "common_masks.json")
        write_json(run.path / "missing_exclusions.json", {"entries": ledger, "price_observation_count":
            sum(len(x) for x in closes.values())})
        run.output(run.path / "missing_exclusions.json")
        return ("BLOCKED" if authorization_failure else "PARTIAL"), "Price identity/bars missing for one or more fixed-pool companies; PCA withheld"
    dates, matrix = return_matrix(closes, tickers)
    complete = np.isfinite(matrix).all(axis=1)
    write_json(run.path / "common_masks.json", {"ticker_order": tickers, "company_price_available": available,
        "date_order": dates, "observed_return_mask_TxN": np.isfinite(matrix).tolist(),
        "complete_date_mask": complete.tolist(), "complete_dates": [d for d, ok in zip(dates, complete) if ok],
        "window_start": cfg.start.isoformat(), "window_end": cfg.end.isoformat()})
    run.output(run.path / "common_masks.json")
    write_json(run.path / "missing_exclusions.json", {"entries": ledger,
        "missing_return_cells_by_ticker": {t: int((~np.isfinite(matrix[:, j])).sum()) for j, t in enumerate(tickers)},
        "complete_date_count": int(complete.sum()), "total_union_dates": len(dates),
        "policy": "No missing value is filled; PCA uses only complete past dates"})
    run.output(run.path / "missing_exclusions.json")
    np.save(run.path / "returns_TxN.npy", matrix)
    run.output(run.path / "returns_TxN.npy")
    try:
        pca = past_pca(matrix[complete], cfg.components, cfg.center, cfg.scale)
    except M4Error as exc:
        return "PARTIAL", str(exc)
    np.save(run.path / "pca_stock_loadings_NxK.npy", pca["loadings"])
    np.save(run.path / "pca_date_scores_TxK.npy", pca["scores"])
    write_json(run.path / "pca_metadata.json", {"input_shape_TxN": pca["input_shape"],
        "loadings_shape_NxK": list(pca["loadings"].shape), "scores_shape_TxK": list(pca["scores"].shape),
        "ticker_order": tickers, "date_order": [d for d, ok in zip(dates, complete) if ok],
        "center": cfg.center, "scale": cfg.scale, "mean_by_security": pca["mean"].tolist(),
        "scale_by_security": pca["scale"].tolist(), "requested_and_used_k": pca["k"],
        "effective_rank": pca["effective_rank"], "singular_values": pca["singular_values"].tolist(),
        "input": "past-window split-adjusted close-to-close returns, no dividend total return",
        "window_start": cfg.start.isoformat(), "window_end": cfg.end.isoformat()})
    for name in ("pca_stock_loadings_NxK.npy", "pca_date_scores_TxK.npy", "pca_metadata.json"):
        run.output(run.path / name)
    return "DONE", ""
