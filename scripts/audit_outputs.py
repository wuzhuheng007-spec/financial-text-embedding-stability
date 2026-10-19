"""Read-only audit of real M1 and M2/M3 artifacts; writes a separate audit receipt."""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import hashlib
import json

import numpy as np

from finembed.analysis import topk, upper_triangle_spearman, orthogonal_check
from finembed.run import sha256, write_json


def check_manifest(folder: Path) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for role in ("inputs", "outputs"):
        for entry in manifest[role]:
            if "path" in entry:
                path = Path(entry["path"])
            else:
                url = entry["source_url"]
                key = hashlib.sha256(url.encode("utf-8")).hexdigest()
                path = folder.parents[2] / ".cache" / "sec" / f"{key}.bin"
            assert path.is_file(), (role, path)
            assert sha256(path.read_bytes()) == entry["sha256"], (role, path)
            if "bytes" in entry:
                assert path.stat().st_size == entry["bytes"], (role, path)
    return {"status": manifest["status"], "input_count": len(manifest["inputs"]),
            "output_count": len(manifest["outputs"]),
            "manifest_sha256": sha256((folder / "manifest.json").read_bytes())}


def check_arm(folder: Path, corpus_folder: Path) -> dict:
    row = json.loads((folder / "row_index.json").read_text(encoding="utf-8"))
    config = json.loads((folder / "arm_config.json").read_text(encoding="utf-8"))
    cos_json = json.loads((folder / "cosine.json").read_text(encoding="utf-8"))
    near = json.loads((folder / "topk.json").read_text(encoding="utf-8"))
    vectors = np.load(folder / "vectors.npy", allow_pickle=False)
    cosine = np.load(folder / "cosine.npy", allow_pickle=False)
    tickers = row["tickers"]
    assert tickers == cos_json["tickers"] == [r["ticker"] for r in row["rows"]]
    assert vectors.shape == (len(tickers), 384) and cosine.shape == (len(tickers), len(tickers))
    assert np.isfinite(vectors).all() and np.isfinite(cosine).all()
    assert np.allclose(cosine, np.asarray(cos_json["matrix"]), atol=0, rtol=0)
    assert np.allclose(cosine, cosine.T, atol=1e-12)
    assert np.allclose(np.diag(cosine), 1, atol=1e-12)
    unit = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    assert np.allclose(cosine, unit @ unit.T, atol=1e-12)
    assert near["neighbors"] == topk(cosine, tickers, near["k"])
    mapping = defaultdict(list)
    with (folder / "chunk_map.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            chunk = json.loads(line)
            mapping[chunk["ticker"]].append(chunk)
    counts = {}
    for ticker in tickers:
        item = (corpus_folder / "filings" / ticker / "item1_business.txt").read_text(encoding="utf-8")
        chunks = mapping[ticker]
        assert chunks and chunks[0]["token_start"] == 0
        assert all(a["token_end_exclusive"] == b["token_start"] for a, b in zip(chunks, chunks[1:]))
        assert all(c["model_input_tokens_including_specials"] == len(c["body_token_ids"]) + 2 for c in chunks)
        assert all(c["model_input_tokens_including_specials"] <= config["model_max_input_tokens"] for c in chunks)
        assert all(sha256(item[c["char_start"]:c["char_end_exclusive"]].encode("utf-8")) == c["source_span_sha256"] for c in chunks)
        assert chunks[-1]["char_end_exclusive"] >= len(item.rstrip()) - 2
        counts[ticker] = {"chunks": len(chunks), "body_tokens": chunks[-1]["token_end_exclusive"],
                          "last_source_char": chunks[-1]["char_end_exclusive"]}
    return {"config": config, "vectors": vectors, "cosine": cosine,
            "neighbors": near["neighbors"], "counts": counts, "tickers": tickers}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m1", required=True, type=Path)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    m1, analysis = args.m1.resolve(strict=True), args.analysis.resolve(strict=True)
    m1_manifest, analysis_manifest = check_manifest(m1), check_manifest(analysis)
    assert m1_manifest["status"] == analysis_manifest["status"] == "DONE"
    index = json.loads((m1 / "corpus_index.json").read_text(encoding="utf-8"))
    validation = json.loads((analysis / "corpus_validation.json").read_text(encoding="utf-8"))
    report = json.loads((analysis / "analysis_report.json").read_text(encoding="utf-8"))
    assert index["included_count"] == validation["valid_count"] == report["common_count"] == 5
    assert all(x["status"] == "INCLUDED" for x in index["entries"])
    assert all(x["status"] == "VALID" for x in validation["entries"])
    arms = {label: check_arm(analysis / "analysis" / label, m1) for label in ("base_128", "repeat_128", "alternate_192")}
    base, repeat, alternate = (arms[x] for x in ("base_128", "repeat_128", "alternate_192"))
    assert base["tickers"] == repeat["tickers"] == alternate["tickers"] == report["common_companies"]
    assert all(base["counts"][x]["body_tokens"] == alternate["counts"][x]["body_tokens"] for x in base["tickers"])
    base_config = dict(base["config"])
    alternate_config = dict(alternate["config"])
    assert base_config["body_tokens"] == repeat["config"]["body_tokens"] == 128
    assert alternate_config["body_tokens"] == 192
    alternate_config["body_tokens"] = 128
    assert base_config == repeat["config"] == alternate_config
    assert np.max(np.abs(base["vectors"] - repeat["vectors"])) == report["same_config_repeat"]["vector_max_abs"]
    assert np.max(np.abs(base["cosine"] - repeat["cosine"])) == report["same_config_repeat"]["cosine_max_abs"]
    assert upper_triangle_spearman(base["cosine"], alternate["cosine"])["value"] == report["upper_triangle_spearman"]["value"]
    for ticker in base["tickers"]:
        overlap = len({x["ticker"] for x in base["neighbors"][ticker]} &
                      {x["ticker"] for x in alternate["neighbors"][ticker]}) / report["top_k"]
        assert overlap == report["topk_overlap_fraction_by_company"][ticker]
    assert orthogonal_check(base["vectors"], base["cosine"])["passed"]
    receipt = {"status": "AUDIT_OK", "m1_manifest": m1_manifest, "analysis_manifest": analysis_manifest,
               "common_companies": base["tickers"],
               "arms": {name: {"body_tokens": arm["config"]["body_tokens"], "counts": arm["counts"]}
                        for name, arm in arms.items()},
               "report_sha256": sha256((analysis / "analysis_report.json").read_bytes())}
    write_json(args.output.resolve(), receipt)
    print(json.dumps({"status": "AUDIT_OK", "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
