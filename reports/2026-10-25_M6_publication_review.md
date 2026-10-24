# M6 public-branch publication review — 2026-10-25

This is a same-agent source and packaging review, not an independent scientific audit. The user explicitly authorized submission of checked M6 code and research documents to the existing **public** repository on 2026-10-25. The original ZIP records the earlier upload block and is preserved unchanged as `M6_bm25_length_controls_2026-10-24.zip`, SHA-256 `3ca29526925e7243695069ccd5bed445e235fb1d6d959a17759370a862080d2a`.

## Plan and actual

| Plan | Actual |
|---|---|
| Inspect the received archive | 59 files under one safe root; all 58 archive checksums verified; sensitive-pattern scan found no credentials, private Overleaf URL or local user path. |
| Preserve the published base | Imported only M6 source, test and document changes against base commit `6a1e3877045c486ea9467e42d3a36274a401300b`. Existing `results/`, `figures/`, `configs/` and the historical `MANIFEST_SHA256.json` remain Git-blob-identical to that commit. Fourteen text files have CRLF bytes in the original Windows share export and LF bytes in Git and the M6 ZIP; normalized content is identical. |
| Check engineering behavior | The original synthetic section fixture produced five failures on Windows: `write_text()` changed line endings after its SHA-256 was calculated. The fixture now writes the exact UTF-8 bytes it hashes. With this correction, `python -m pytest -q -r s` returned **40 passed, 2 skipped** using Python 3.11.9, NumPy 2.4.6 and pytest 9.1.1. The skips require absent pinned model files. `python -m compileall -q src scripts tests` and `python -m finembed.m6 --help` also passed. The review set `PYTHONPATH` to this copy's `src` so tests could not import the older editable installation. |
| Publish for review | The target is the existing `codex/m6-bm25-length-controls-20261024` branch. Remote commit and PR status must be checked directly on GitHub; this report does not treat a local commit as an upload. |

`M6_CHECKSUMS.json` identifies the revised public-branch **Git blob bytes** and source archive. The root `MANIFEST_SHA256.json` remains the **original Windows local export** manifest; it must not be applied to changed M6 source files or assumed to hash LF-normalized Git blobs. No original SEC text, raw vendor responses, model weights, browser material, environment secrets or team Overleaf sources were added. No license has been granted.

## Scope of source and citation checks

The BM25 implementation is a specified lexical variant, with directed retrieval scores and separately named term-weight cosine. [Elastic's similarity settings](https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity) support the stated `k1` saturation and `b` document-length roles and defaults; they do not validate performance on SEC filings. The [SEC Form 10-Q](https://www.sec.gov/files/form10-q.pdf) lists Part I Item 1 as financial statements, Part I Item 2 as MD&A, and Part II Item 1A as risk factors. The [pinned MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md) identifies a short-text encoder and its input-length context. These three primary pages were opened during this review.

No real M6 corpus analysis or fixed-model dense inference was run for this publication. The tests use labeled synthetic sections and a synthetic encoder for software checks only. M5 research parameters remain undecided; no claim about representation improvement, financial incremental value or exclusion of look-ahead bias follows from this branch.
