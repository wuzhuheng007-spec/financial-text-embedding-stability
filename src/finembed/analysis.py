"""Descriptive multi-company embedding and one-factor chunk-length stability."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import math

from .config import Config
from .extract import primary_text, extract_item1, comparison, ExtractionError
from .model import Encoder, ModelError, aggregate
from .run import Run, sha256, write_json
from .sec import raw_header, raw_filer_cik


class AnalysisError(RuntimeError):
    pass


def validate_corpus(config: Config, index_path: Path) -> tuple[list[dict], dict]:
    if not index_path.is_file():
        raise AnalysisError(f"Real M1 corpus index missing: {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("as_of") != config.as_of.isoformat() or index.get("first_run_symbol_limit") is not None:
        raise AnalysisError("M1 corpus as_of or first_run_symbol_limit differs from analysis config")
    entries = index.get("entries")
    if not isinstance(entries, list) or [x.get("ticker") for x in entries] != [x.ticker for x in config.companies]:
        raise AnalysisError("M1 corpus entries do not match the ordered demonstration list")
    valid, audit = [], []
    for entry in entries:
        ticker = entry["ticker"]
        if entry.get("status") != "INCLUDED":
            audit.append({"ticker": ticker, "status": "BLOCKED" if entry.get("status") == "BLOCKED" else "EXCLUDED",
                          "source_status": entry.get("status"),
                          "reason": entry.get("reason", entry.get("status"))})
            continue
        try:
            folder = Path(entry["filing_dir"]).resolve(strict=True)
            if not folder.is_relative_to(index_path.parent.resolve()):
                raise AnalysisError("Filing directory lies outside its M1 run")
            raw = (folder / "original_submission.txt").read_bytes()
            primary = (folder / "primary_text.txt").read_text(encoding="utf-8")
            item = (folder / "item1_business.txt").read_text(encoding="utf-8")
            filing = json.loads((folder / "filing_metadata.json").read_text(encoding="utf-8"))
            item_meta = json.loads((folder / "item1_metadata.json").read_text(encoding="utf-8"))
            boundary = json.loads((folder / "boundary_comparison.json").read_text(encoding="utf-8"))
            if not filing.get("archive_url", "").startswith("https://www.sec.gov/Archives/edgar/data/"):
                raise AnalysisError("Filing lacks an official SEC archive URL")
            if (filing.get("ticker") != ticker or filing.get("accession") != entry.get("accession")
                    or filing.get("cik") != entry.get("cik")):
                raise AnalysisError("Filing identity does not match corpus index")
            header = raw_header(raw, filing["accession"])
            if header != filing.get("raw_sec_header") or date.fromisoformat(header["acceptance_datetime_eastern"][:10]) > config.as_of:
                raise AnalysisError("Raw SEC header identity/time differs or is after configured as_of")
            if raw_filer_cik(raw) != entry.get("cik"):
                raise AnalysisError("Raw SEC FILER CIK does not match corpus index and filing metadata")
            if sha256(raw) != filing["raw_sha256"] or sha256(raw) != entry["raw_sha256"]:
                raise AnalysisError("Raw SEC submission hash mismatch")
            if sha256(primary.encode("utf-8")) != item_meta["primary_text_sha256"]:
                raise AnalysisError("Primary text hash mismatch")
            if sha256(item.encode("utf-8")) != item_meta["item1_sha256"] or entry["item1_sha256"] != item_meta["item1_sha256"]:
                raise AnalysisError("Item 1 hash mismatch")
            reparsed, _ = primary_text(raw)
            reitem, offsets = extract_item1(reparsed)
            if reparsed != primary or reitem != item:
                raise AnalysisError("M1 extraction does not reproduce from raw SEC bytes")
            if offsets["start_char"] != item_meta["start_char"] or offsets["end_char_exclusive"] != item_meta["end_char_exclusive"]:
                raise AnalysisError("Item 1 character offsets mismatch")
            if comparison(primary, item, offsets) != boundary:
                raise AnalysisError("M1 boundary comparison mismatch")
            files = {name: {"path": str(folder / name), "sha256": sha256((folder / name).read_bytes())}
                     for name in ("original_submission.txt", "primary_text.txt", "item1_business.txt",
                                  "filing_metadata.json", "item1_metadata.json", "boundary_comparison.json")}
            valid.append({"ticker": ticker, "cik": entry["cik"], "accession": entry["accession"],
                          "item1_text": item, "item1_sha256": entry["item1_sha256"], "files": files})
            audit.append({"ticker": ticker, "status": "VALID", "cik": entry["cik"],
                          "accession": entry["accession"], "item1_sha256": entry["item1_sha256"], "files": files})
        except (OSError, KeyError, ValueError, AnalysisError, ExtractionError) as exc:
            audit.append({"ticker": ticker, "status": "BLOCKED" if isinstance(exc, FileNotFoundError) else "INVALID",
                          "reason": f"{type(exc).__name__}: {exc}"})
    summary = {"schema_version": 1, "corpus_index": str(index_path),
               "corpus_index_sha256": sha256(index_path.read_bytes()), "as_of": config.as_of.isoformat(),
               "planned_count": len(entries), "valid_count": len(valid), "entries": audit}
    return valid, summary


def _cosine(vectors):
    import numpy as np
    v = np.asarray(vectors, dtype=np.float64)
    if v.ndim != 2 or not np.isfinite(v).all():
        raise AnalysisError("Nonfinite or malformed document matrix")
    norms = np.linalg.norm(v, axis=1)
    if (norms <= 0).any() or not np.isfinite(norms).all():
        raise AnalysisError("Zero or nonfinite document norm")
    unit = v / norms[:, None]
    result = unit @ unit.T
    if not np.isfinite(result).all():
        raise AnalysisError("Nonfinite cosine matrix")
    return result


def topk(cosine, tickers: list[str], k: int) -> dict:
    import numpy as np
    matrix = np.asarray(cosine)
    if matrix.shape != (len(tickers), len(tickers)) or not np.isfinite(matrix).all():
        raise AnalysisError("Top-k requires a finite square cosine matrix")
    if not 1 <= k < len(tickers):
        raise AnalysisError("Top-k must be below common company count")
    return {ticker: [{"ticker": tickers[j], "cosine": float(matrix[i, j])}
                     for j in sorted((j for j in range(len(tickers)) if j != i),
                                     key=lambda j: (-float(matrix[i, j]), tickers[j]))[:k]]
            for i, ticker in enumerate(tickers)}


def _average_ranks(values):
    import numpy as np
    values = np.asarray(values, dtype=np.float64)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    pos = 0
    while pos < len(values):
        end = pos + 1
        while end < len(values) and values[order[end]] == values[order[pos]]:
            end += 1
        ranks[order[pos:end]] = (pos + 1 + end) / 2
        pos = end
    return ranks


def upper_triangle_spearman(a, b) -> dict:
    import numpy as np
    x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if x.shape != y.shape or x.ndim != 2 or x.shape[0] != x.shape[1]:
        return {"value": None, "reason": "matrix shapes differ or are not square"}
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        return {"value": None, "reason": "nonfinite cosine value; no imputation"}
    upper = np.triu_indices(x.shape[0], k=1)
    xv, yv = x[upper], y[upper]
    if len(xv) < 3:
        return {"value": None, "reason": "fewer than three off-diagonal upper-triangle pairs", "pair_count": len(xv)}
    rx, ry = _average_ranks(xv), _average_ranks(yv)
    dx, dy = rx - rx.mean(), ry - ry.mean()
    denominator = float(np.linalg.norm(dx) * np.linalg.norm(dy))
    if denominator <= 0:
        return {"value": None, "reason": "constant ranks after exact-value average-tie ranking", "pair_count": len(xv)}
    return {"value": float(np.dot(dx, dy) / denominator), "pair_count": len(xv),
            "tie_rule": "exactly equal float64 cosine values receive average ranks; no tolerance grouping"}


def orthogonal_check(vectors, cosine) -> dict:
    import numpy as np
    matrix = np.asarray(vectors, dtype=np.float64)
    dimension = matrix.shape[1]
    direction = np.arange(1, dimension + 1, dtype=np.float64)
    direction /= np.linalg.norm(direction)
    q = np.eye(dimension) - 2 * np.outer(direction, direction)
    transformed = matrix @ q
    orthogonality_error = float(np.max(np.abs(q.T @ q - np.eye(dimension))))
    gram_error = float(np.max(np.abs(matrix @ matrix.T - transformed @ transformed.T)))
    cosine_error = float(np.max(np.abs(cosine - _cosine(transformed))))
    passed = orthogonality_error <= 1e-12 and gram_error <= 1e-10 and cosine_error <= 1e-10
    return {"method": "fixed Householder orthogonal reflection, same vector dimension; no Procrustes",
            "orthogonality_max_abs": orthogonality_error, "gram_max_abs": gram_error,
            "cosine_max_abs": cosine_error, "tolerance_orthogonality": 1e-12,
            "tolerance_gram_cosine": 1e-10, "passed": bool(passed)}


def _save_arm(run: Run, encoder: Encoder, corpus: list[dict], length: int, label: str):
    import numpy as np
    folder = run.path / "analysis" / label
    folder.mkdir(parents=True, exist_ok=True)
    vectors, mapping = [], []
    tickers = [x["ticker"] for x in corpus]
    for row, company in enumerate(corpus):
        chunks = encoder.chunks(company["item1_text"], length)
        chunk_vectors = encoder.embed_chunks(chunks)
        vectors.append(aggregate(chunk_vectors, run.config.normalize_chunks, run.config.normalize_documents))
        for j, chunk in enumerate(chunks):
            mapping.append({"row": row, "ticker": company["ticker"], "chunk_index": j,
                            "item1_sha256": company["item1_sha256"],
                            "token_start": chunk.token_start, "token_end_exclusive": chunk.token_end_exclusive,
                            "char_start": chunk.char_start, "char_end_exclusive": chunk.char_end_exclusive,
                            "source_span_sha256": chunk.source_span_sha256,
                            "body_token_ids": list(chunk.token_ids),
                            "model_input_tokens_including_specials": len(chunk.token_ids) + encoder.special_count})
    matrix = np.stack(vectors)
    cosine = _cosine(matrix)
    np.save(folder / "vectors.npy", matrix, allow_pickle=False)
    np.save(folder / "cosine.npy", cosine, allow_pickle=False)
    write_json(folder / "row_index.json", {"tickers": tickers, "rows": [
        {"row": i, "ticker": c["ticker"], "cik": c["cik"], "accession": c["accession"],
         "item1_sha256": c["item1_sha256"]} for i, c in enumerate(corpus)]})
    write_json(folder / "cosine.json", {"tickers": tickers, "matrix": cosine.tolist()})
    write_json(folder / "topk.json", {"k": run.config.top_k, "tie_rule": "descending cosine; exact ties by ticker ascending",
                                     "neighbors": topk(cosine, tickers, run.config.top_k)})
    with (folder / "chunk_map.jsonl").open("w", encoding="utf-8") as stream:
        for record in mapping:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    write_json(folder / "arm_config.json", {"body_tokens": length, "overlap_tokens": 0,
                                            "special_tokens": encoder.special_count,
                                            "model_max_input_tokens": encoder.identity["max_input_tokens"],
                                            "normalize_chunks": run.config.normalize_chunks,
                                            "aggregation": "equal-weight arithmetic mean of chunk embeddings",
                                            "normalize_documents": run.config.normalize_documents,
                                            "provider": run.config.provider, "model": encoder.identity})
    for path in folder.iterdir():
        run.output(path)
    run.log("embedding_arm_complete", label=label, companies=len(corpus), chunks=len(mapping), body_tokens=length)
    return matrix, cosine, topk(cosine, tickers, run.config.top_k)


def _write_report(run: Run, result: dict, validation: dict) -> None:
    lines = ["# M2–M3 descriptive run", "", f"Status: **{result['status']}**", "",
             f"Config SHA-256: `{run.config.source_sha256}`", "",
             f"Verified real Item 1 files: {validation['valid_count']}/{validation['planned_count']}", ""]
    if result["status"] == "BLOCKED":
        lines.extend([f"Reason: {result['reason']}", "", "No company vectors or stability statistics were produced.", ""])
    elif result["status"] == "FAILED" and "same_config_repeat" not in result:
        lines.extend([f"Reason: {result['reason']}", "", "The comparison was stopped at the failed repeat-inference gate.", ""])
    else:
        repeat = result["same_config_repeat"]
        lines.extend([f"Common companies: {', '.join(result['common_companies'])}", "",
                      f"Same-config repeat max |vector delta|: {repeat['vector_max_abs']:.12g}",
                      f"Same-config repeat max |cosine delta|: {repeat['cosine_max_abs']:.12g}",
                      f"Repeat tolerance: {repeat['tolerance']:.12g}; passed: {repeat['passed']}", "",
                      "This is fixed-weight repeat inference, not independent training seeds.", "",
                      f"Upper-triangle Spearman: {result['upper_triangle_spearman']['value']}",
                      f"Spearman details: {json.dumps(result['upper_triangle_spearman'], ensure_ascii=False, sort_keys=True)}", "",
                      "Top-k overlap/k by company:", ""])
        lines.extend(f"- {ticker}: {fraction:.12g}" for ticker, fraction in result["topk_overlap_fraction_by_company"].items())
        lines.extend(["", "Orthogonal check:", "",
                      f"- Gram max absolute difference: {result['orthogonal_check']['gram_max_abs']:.12g}",
                      f"- Cosine max absolute difference: {result['orthogonal_check']['cosine_max_abs']:.12g}",
                      f"- Passed: {result['orthogonal_check']['passed']}", "",
                      "No Procrustes alignment, pairwise significance test, price-baseline result or return claim was made.", ""])
    lines.extend(["The SEC acceptance date does not establish the first public availability instant. Current ticker mapping is not historical.", "",
                  "See `corpus_validation.json`, `analysis_report.json`, arm configuration and the run manifest for identities, invalid-value rules and hashes.", ""])
    path = run.path / "analysis_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    run.output(path)


def analyze(run: Run, corpus_index: Path) -> tuple[str, str]:
    import numpy as np
    config = run.config
    corpus, validation = validate_corpus(config, corpus_index)
    validation_path = run.path / "corpus_validation.json"
    write_json(validation_path, validation)
    run.output(validation_path)
    run.input(corpus_index)
    if len(corpus) < 3:
        reason = f"Only {len(corpus)} verified real Item 1 documents; at least three common companies required"
        write_json(run.path / "analysis_report.json", {"status": "BLOCKED", "reason": reason,
                                                      "planned_count": len(config.companies),
                                                      "valid_count": len(corpus), "invalid_value_policy": "no imputation"})
        run.output(run.path / "analysis_report.json")
        _write_report(run, {"status": "BLOCKED", "reason": reason}, validation)
        return "BLOCKED", reason
    if config.top_k >= len(corpus):
        raise AnalysisError("Configured top_k is not below verified common-company count")
    for company in corpus:
        for file in company["files"].values():
            run.input(Path(file["path"]))
    encoder = Encoder(config)
    for file in encoder.identity["files"].values():
        run.input(Path(file["path"]))
    base_v, base_c, base_n = _save_arm(run, encoder, corpus, config.body_tokens, "base_128")
    repeat_v, repeat_c, _ = _save_arm(run, encoder, corpus, config.body_tokens, "repeat_128")
    vector_diff = float(np.max(np.abs(base_v - repeat_v)))
    cosine_diff = float(np.max(np.abs(base_c - repeat_c)))
    repeat_pass = bool(vector_diff <= config.repeat_tolerance and cosine_diff <= config.repeat_tolerance)
    if not repeat_pass:
        result = {"status": "FAILED", "reason": "Same-configuration repeat exceeded tolerance",
                  "repeat_vector_max_abs": vector_diff, "repeat_cosine_max_abs": cosine_diff,
                  "repeat_tolerance": config.repeat_tolerance}
        write_json(run.path / "analysis_report.json", result)
        run.output(run.path / "analysis_report.json")
        _write_report(run, result, validation)
        return "FAILED", result["reason"]
    alt_v, alt_c, alt_n = _save_arm(run, encoder, corpus, config.alternate_body_tokens, "alternate_192")
    spearman = upper_triangle_spearman(base_c, alt_c)
    overlaps = {ticker: len({x["ticker"] for x in base_n[ticker]} & {x["ticker"] for x in alt_n[ticker]}) / config.top_k
                for ticker in [x["ticker"] for x in corpus]}
    orthogonal = orthogonal_check(base_v, base_c)
    result_status = "FAILED" if not orthogonal["passed"] else "PARTIAL" if spearman["value"] is None else "DONE"
    result = {"status": result_status,
              "scope": "small-sample descriptive demonstration; no price baseline or return claim",
              "planned_companies": [x.ticker for x in config.companies],
              "common_companies": [x["ticker"] for x in corpus], "common_count": len(corpus),
              "base_body_tokens": config.body_tokens, "alternate_body_tokens": config.alternate_body_tokens,
              "only_changed_factor": "body tokens per nonoverlapping chunk",
              "same_config_repeat": {"vector_max_abs": vector_diff, "cosine_max_abs": cosine_diff,
                                     "tolerance": config.repeat_tolerance, "passed": repeat_pass,
                                     "note": "fixed-weight repeat inference, not independent training seeds"},
              "upper_triangle_spearman": spearman,
              "topk_overlap_fraction_by_company": overlaps, "top_k": config.top_k,
              "topk_tie_rule": "descending cosine; exact ties by ticker ascending",
              "invalid_value_policy": "nonfinite embeddings/cosines fail; undefined Spearman is null with reason; no imputation",
              "orthogonal_check": orthogonal,
              "statistical_inference": "none; company-pair similarities are not independent observations",
              "procrustes": "not performed"}
    write_json(run.path / "analysis_report.json", result)
    run.output(run.path / "analysis_report.json")
    _write_report(run, result, validation)
    if not orthogonal["passed"]:
        return "FAILED", "Orthogonal Gram/cosine numerical check failed"
    if spearman["value"] is None:
        return "PARTIAL", f"Spearman undefined: {spearman['reason']}"
    return "DONE", ""

