"""Render a verified, complete small-chunk run as descriptive Markdown only.

This script reads saved arrays and JSON. It does not construct an encoder or
perform model inference. The optional M6 comparison reads frozen local files.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finembed.sensitivity_preflight import file_sha256  # noqa: E402
from finembed.small_chunk_runner import verify_saved_output  # noqa: E402


EXPECTED_LENGTHS = [118, 123, 128, 133, 138]
POOLING = ["equal_chunks", "body_token_weighted"]
COMPANIES = ["AAPL", "MSFT", "AMZN", "GOOGL", "META"]


def _number(value: float | None) -> str:
    if value is None:
        return "undefined"
    return format(float(value), ".9g")


def _require_complete(manifest: dict, metrics: dict, receipt: dict) -> None:
    if (manifest.get("status") != "COMPLETE" or
            manifest.get("evidence_class") != "REAL_MODEL" or
            manifest.get("decision_validation", {}).get("status") != "ACCEPTED" or
            receipt.get("status") != "PASS_TOKENIZATION_ONLY"):
        raise ValueError("A complete, decision-bound real-model run and passing preflight are required")
    if (metrics.get("kind") != "SMALL_CHUNK_DESCRIPTIVE_METRICS" or
            metrics.get("status") != "COMPLETE_NUMERICAL_ONLY" or
            metrics.get("inference_provenance") != "REAL_MODEL" or
            metrics.get("company_order") != COMPANIES or
            metrics.get("candidate_body_tokens") != EXPECTED_LENGTHS or
            metrics.get("reference_body_tokens") != 128 or
            metrics.get("pooling_methods") != POOLING or
            metrics.get("denominators") != {"companies": 5, "upper_triangle_pairs": 10,
                                            "tracked_reference_rank2_rank3_peers": 10}):
        raise ValueError("Metric identity or denominator differs from the declared candidate")
    identity_fields = ("ticker", "cik", "accession", "form", "section", "item1_sha256")
    if ([row["ticker"] for row in manifest["input_versions"]] != COMPANIES or
            [row["ticker"] for row in receipt["documents"]] != COMPANIES or
            any(tuple(a[field] for field in identity_fields) !=
                tuple(b[field] for field in identity_fields)
                for a, b in zip(manifest["input_versions"], receipt["documents"]))):
        raise ValueError("Saved input identities differ across run and preflight")
    if (set(metrics.get("arms", {})) != {str(x) for x in EXPECTED_LENGTHS} or
            any(set(metrics["arms"][str(x)]) != set(POOLING) for x in EXPECTED_LENGTHS) or
            set(metrics.get("within_pooling_length_comparisons", {})) !=
            {str(x) for x in EXPECTED_LENGTHS if x != 128} or
            set(metrics.get("same_length_pooling_comparisons", {})) !=
            {str(x) for x in EXPECTED_LENGTHS} or
            set(metrics.get("signed_length_by_pooling_interactions", {})) !=
            {str(x) for x in EXPECTED_LENGTHS if x != 128}):
        raise ValueError("Metric grid is incomplete")
    if (manifest.get("logical_job_count_planned") != 25 or
            manifest.get("logical_jobs_completed") != 25 or
            manifest.get("company_vector_count_planned") != 50 or
            manifest.get("company_vectors_completed") != 50 or
            manifest.get("chunk_vector_count_planned") != receipt["planned_counts"]["chunk_vectors"] or
            manifest.get("chunk_vectors_completed") != receipt["planned_counts"]["chunk_vectors"]):
        raise ValueError("Saved and preflight counts differ")


def compare_m6_reference(run: Path, manifest: dict, metrics: dict,
                         historical_dense: Path) -> dict:
    """Read back the M6 128 arms; report observed differences without a pass cutoff."""
    historical_dense = historical_dense.resolve(strict=True)
    old_receipt_path = historical_dense / "receipt.json"
    old = json.loads(old_receipt_path.read_text(encoding="utf-8"))
    if old.get("status") != "DONE" or old.get("dense_status") != "DONE":
        raise ValueError("Historical M6 dense receipt is not complete")
    current_model = manifest["model_identity"]
    old_files = old["model_identity"]["files"]
    if (old["config_sha256"] != current_model["reference_config_sha256"] or
            old["model_identity"]["revision"] != current_model["revision"] or
            old["model_identity"]["tokenizer_revision"] != current_model["tokenizer_revision"] or
            {name: row["sha256"] for name, row in old_files.items()} !=
            current_model["model_file_sha256"] or
            old["row_text_hashes"] !=
            [row["item1_sha256"] for row in manifest["input_versions"]]):
        raise ValueError("Historical M6 input, configuration or model identity differs")
    result = {"receipt_sha256": file_sha256(old_receipt_path), "pooling": {}}
    ids = metrics["company_order"]
    for pool in POOLING:
        name = f"dense_vectors_128_{pool}.npy"
        old_path = historical_dense / name
        if file_sha256(old_path) != old["outputs"][name]["sha256"]:
            raise ValueError(f"Historical M6 vector hash changed: {name}")
        old_vectors = np.load(old_path, allow_pickle=False)
        jobs = {job["ticker"]: job for job in manifest["jobs"]
                if job["body_tokens_per_chunk"] == 128}
        if set(jobs) != set(ids):
            raise ValueError("Saved 128-token run is missing a company")
        current_vectors = np.vstack([np.load(run / jobs[ticker]["files"][pool]["path"],
                                               allow_pickle=False) for ticker in ids])
        if old_vectors.shape != current_vectors.shape or not np.isfinite(old_vectors).all():
            raise ValueError("Historical 128-token vector shape or finiteness differs")
        # The M6 CSV header carries both ticker and accession. Verify row order
        # before comparing its saved cosine matrix to the new result.
        csv_name = f"dense_cosine_128_{pool}.csv"
        csv_path = historical_dense / csv_name
        if file_sha256(csv_path) != old["outputs"][csv_name]["sha256"]:
            raise ValueError(f"Historical M6 cosine CSV hash changed: {csv_name}")
        with csv_path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        labels = [f"{row['ticker']}|{row['accession']}" for row in manifest["input_versions"]]
        if rows[0][1:] != labels or [row[0] for row in rows[1:]] != labels:
            raise ValueError("Historical M6 CSV company/accession order differs")
        old_cosine = np.asarray([[float(value) for value in row[1:]]
                                 for row in rows[1:]], dtype=np.float64)
        current_cosine = np.asarray(metrics["arms"]["128"][pool]["cosine_matrix"],
                                    dtype=np.float64)
        if old_cosine.shape != current_cosine.shape or not np.isfinite(old_cosine).all():
            raise ValueError("Historical M6 cosine matrix is invalid")
        result["pooling"][pool] = {
            "historical_vector_sha256": old["outputs"][name]["sha256"],
            "historical_cosine_sha256": old["outputs"][csv_name]["sha256"],
            "vector_max_abs": float(np.max(np.abs(old_vectors - current_vectors))),
            "cosine_max_abs": float(np.max(np.abs(old_cosine - current_cosine)))}
    return result


def render_markdown(manifest: dict, metrics: dict, receipt: dict, *,
                    run_name: str, manifest_sha256: str, metrics_sha256: str,
                    receipt_sha256: str, historical: dict | None = None) -> str:
    """Render checked machine fields; preserve all detailed values in metrics.json."""
    _require_complete(manifest, metrics, receipt)
    lines = ["# Small-chunk probe descriptive results", "",
             "**Scope.** This report describes one complete, saved real-model engineering probe "
             "after `verify-output` recomputed its arrays and metrics. It does not establish "
             "general stability, semantic improvement, a causal token-count effect, statistical "
             "significance, prediction, trading risk or economic value. The source decision's "
             "authorship still requires independent human review.", "",
             f"Run directory: `{run_name}`. Verification status: `PASS_SAVED_OUTPUT_RECOMPUTED`. "
             f"Run status: `{manifest['status']}`; evidence class: `{manifest['evidence_class']}`.", "",
             "| Bound object | SHA-256 or identity |", "| --- | --- |",
             f"| Manifest | `{manifest_sha256}` |",
             f"| Metrics | `{metrics_sha256}` |",
             f"| Tokenization preflight receipt | `{receipt_sha256}` |",
             f"| Candidate | `{manifest['candidate_sha256']}` |",
             f"| Decision record | `{manifest['decision_sha256']}` |",
             f"| Input manifest | `{manifest['input_manifest_sha256']}` |",
             f"| Metric contract | `{manifest['metric_contract_sha256']}` |",
             f"| Encoder revision | `{manifest['model_identity']['revision']}` |", "",
             "**Exact input order.** " + "; ".join(
                 f"{row['ticker']} `{row['accession']}` Item 1 `{row['item1_sha256']}`"
                 for row in manifest["input_versions"]) + ".", "",
             "**Execution counts.** Planned/completed logical document-length jobs: "
             f"{manifest['logical_job_count_planned']}/{manifest['logical_jobs_completed']}; "
             "planned/completed chunk vectors: "
             f"{manifest['chunk_vector_count_planned']}/{manifest['chunk_vectors_completed']}; "
             "planned/completed company vectors: "
             f"{manifest['company_vector_count_planned']}/{manifest['company_vectors_completed']}; "
             f"actual batch call attempts: {manifest['actual_batch_call_attempts']}. "
             "The two pooling arms reuse each document-length job's chunk vectors.", "",
             "## Complete arm inventory and length contrasts", "",
             "Each row is one executed arm. Length contrasts use the 128-token arm with the "
             "same pooling rule. The reference rows have no length-change statistic.", "",
             "| Body tokens | Pooling | Jobs | Chunks | Company vectors | Mean rank-2/3 gap | "
             "Mean vector distance | Mean signed pair change | Mean / max abs pair change | "
             "Pair-rank Spearman | Mean Top-2 overlap | Mean abs tracked-rank shift | "
             "Mean rank-2/3 gap change | Exact cutoff ties |",
             "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | "
             "---: | ---: | ---: | ---: |"]
    for length in EXPECTED_LENGTHS:
        jobs = [job for job in manifest["jobs"] if job["body_tokens_per_chunk"] == length]
        if len(jobs) != 5 or any(job["status"] != "COMPLETE" for job in jobs):
            raise ValueError(f"Incomplete arm jobs for length {length}")
        chunks = sum(job["chunk_count"] for job in jobs)
        for pool in POOLING:
            arm = metrics["arms"][str(length)][pool]
            tie_count = sum(row["exact_cutoff_tie"] for row in arm["rank2_rank3"].values())
            gap = sum(row["rank2_minus_rank3"] for row in arm["rank2_rank3"].values()) / 5
            if length == 128:
                cells = ["—"] * 7
            else:
                comp = metrics["within_pooling_length_comparisons"][str(length)][pool]
                gap_mean = sum(row["candidate_minus_reference"] for row in
                               comp["rank2_rank3_margin"].values()) / 5
                signed_mean = sum(row["signed_cosine_change"] for row in
                                  comp["similarity_change"]["upper_triangle_pairs"]) / 10
                cells = [_number(comp["company_vector_cosine_distance"]["mean"]),
                         _number(signed_mean),
                         _number(comp["similarity_change"]["mean_absolute"]) + " / " +
                         _number(comp["similarity_change"]["maximum_absolute"]),
                         _number(comp["upper_triangle_spearman"]["value"]),
                         _number(comp["top2_overlap"]["mean"]),
                         _number(comp["reference_rank2_rank3_peer_movement"]["mean_absolute_rank_shift"]),
                         _number(gap_mean)]
            lines.append(f"| {length} | `{pool}` | 5 | {chunks} | 5 | {_number(gap)} | " +
                         " | ".join(cells) + f" | {tie_count} |")
    lines.extend(["", "Vector distance is `1 - cosine(reference firm vector, candidate firm vector)`. "
                  "Signed pair change is candidate cosine minus reference cosine; absolute pair "
                  "changes and Spearman use the ten dependent upper-triangle pairs. Top-2 "
                  "overlap is intersection size divided by two, averaged across five firms. "
                  "Tracked-rank shift follows the reference rank-2 and rank-3 peers, with "
                  "ten firm-peer observations. The gap is rank-2 cosine minus rank-3 cosine. "
                  "Exact cosine ties use ascending company ID; Spearman uses average ranks "
                  "for exact ties. A constant upper triangle yields an undefined, unfilled "
                  "Spearman value, with its reason in `metrics.json`.", "",
                  "## Top-2 set changes against same-pooling 128", "",
                  "The table includes every firm and nonreference arm whose Top-2 peer **set** "
                  "differs from its same-pooling 128-token reference. Peer order and the "
                  "rank-2/rank-3 boundary are shown separately; a zero gap is an exact tie.", "",
                  "| Body tokens | Pooling | Firm | Reference Top-2 | Candidate Top-2 | "
                  "Reference rank 2 / rank 3 (gap) | Candidate rank 2 / rank 3 (gap) |",
                  "| ---: | --- | --- | --- | --- | --- | --- |"])
    changes_by_company = {company: 0 for company in COMPANIES}
    for length in EXPECTED_LENGTHS:
        if length == 128:
            continue
        for pool in POOLING:
            reference = metrics["arms"]["128"][pool]
            candidate = metrics["arms"][str(length)][pool]
            for company in COMPANIES:
                ref_top2 = reference["top2"][company]
                cand_top2 = candidate["top2"][company]
                if set(ref_top2) == set(cand_top2):
                    continue
                changes_by_company[company] += 1
                before = reference["rank2_rank3"][company]
                after = candidate["rank2_rank3"][company]
                lines.append(f"| {length} | `{pool}` | {company} | "
                             f"{', '.join(ref_top2)} | {', '.join(cand_top2)} | "
                             f"{before['rank2']} / {before['rank3']} "
                             f"({_number(before['rank2_minus_rank3'])}) | "
                             f"{after['rank2']} / {after['rank3']} "
                             f"({_number(after['rank2_minus_rank3'])}) |")
    total_changes = sum(changes_by_company.values())
    if total_changes == 0:
        lines.append("| — | — | No Top-2 set changes | — | — | — | — |")
    counts = ", ".join(f"{company} {count}" for company, count in changes_by_company.items())
    lines.extend(["", f"Observed set-change rows by firm: {counts}; total {total_changes}. "
                  "Each row is one firm/arm contrast, not an independent research sample.", "",
                  ])
    if total_changes:
        largest = max(changes_by_company.values())
        leaders = ", ".join(company for company, count in changes_by_company.items()
                            if count == largest)
        lines.extend([f"The largest observed count is {largest} for {leaders} out of "
                      f"{total_changes} set-change rows. This within-grid concentration "
                      "does not establish firm-specific susceptibility.", ""])
    lines.extend(["## Tail-token preflight matrix", "",
                  "Each cell gives the final chunk's body-token count and its share of the "
                  "complete Item 1 body-token count. Values are from the tokenizer-only "
                  "preflight receipt, not model output.", "",
                  "| Firm | Total body tokens | 118 tail | 123 tail | 128 tail | "
                  "133 tail | 138 tail |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for row in receipt["documents"]:
        body = row["body_tokens"]
        if type(body) is not int or body <= 0:
            raise ValueError("Preflight has an invalid body-token count")
        cells = []
        for length in EXPECTED_LENGTHS:
            value = row["lengths"][str(length)]
            tail = value["tail_tokens"]
            if (type(tail) is not int or not 0 < tail <= length or
                    value["body_tokens"] != body):
                raise ValueError("Preflight tail-token matrix is inconsistent")
            cells.append(f"{tail} ({_number(100 * tail / body)}%)")
        lines.append(f"| {row['ticker']} | {body} | " + " | ".join(cells) + " |")
    lines.extend(["", "For the final chunk, equal-chunk pooling assigns weight `1 / chunk_count`; "
                  "body-token-weighted pooling assigns `tail_tokens / total_body_tokens`. "
                  "The change counts and tail shares describe this fixed input grid. "
                  "Changing chunk length also changes internal boundaries and sometimes "
                  "chunk count, so these rows do not isolate a tail-token or pooling cause.", "",
                  "## Same-length pooling contrasts", "",
                  "Weighted pooling is compared with equal-chunk pooling at each fixed length.", "",
                  "| Body tokens | Mean vector distance | Mean signed pair change | Mean / max abs pair change | "
                  "Pair-rank Spearman | Mean Top-2 overlap | Mean abs tracked-rank shift | "
                  "Mean rank-2/3 gap change |",
                  "| ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |"])
    for length in EXPECTED_LENGTHS:
        comp = metrics["same_length_pooling_comparisons"][str(length)]
        gap_mean = sum(row["candidate_minus_reference"] for row in
                       comp["rank2_rank3_margin"].values()) / 5
        signed_mean = sum(row["signed_cosine_change"] for row in
                          comp["similarity_change"]["upper_triangle_pairs"]) / 10
        lines.append(f"| {length} | {_number(comp['company_vector_cosine_distance']['mean'])} | "
                     f"{_number(signed_mean)} | "
                     f"{_number(comp['similarity_change']['mean_absolute'])} / "
                     f"{_number(comp['similarity_change']['maximum_absolute'])} | "
                     f"{_number(comp['upper_triangle_spearman']['value'])} | "
                     f"{_number(comp['top2_overlap']['mean'])} | "
                     f"{_number(comp['reference_rank2_rank3_peer_movement']['mean_absolute_rank_shift'])} | "
                     f"{_number(gap_mean)} |")
    lines.extend(["", "## Signed length-by-pooling interaction", "",
                  "For each nonreference length and pair, interaction equals the weighted-pooling "
                  "signed cosine change from 128 minus the equal-pooling signed cosine change. "
                  "This is a descriptive difference of contrasts, not a causal estimate.", "",
                  "| Body tokens | Mean signed interaction | Minimum / maximum pair interaction | "
                  "Dependent pair count |",
                  "| ---: | ---: | --- | ---: |"])
    for length in EXPECTED_LENGTHS:
        if length == 128:
            continue
        interaction = metrics["signed_length_by_pooling_interactions"][str(length)]
        values = [row["weighted_minus_equal_interaction"] for row in
                  interaction["upper_triangle_pairs"]]
        if len(values) != 10 or interaction["pair_denominator"] != 10:
            raise ValueError("Interaction pair inventory is incomplete")
        lines.append(f"| {length} | {_number(interaction['mean_signed'])} | "
                     f"{_number(min(values))} / {_number(max(values))} | 10 |")
    if historical is not None:
        lines.extend(["", "## Historical M6 128-token compatibility readback", "",
                      f"M6 dense receipt SHA-256: `{historical['receipt_sha256']}`. "
                      "The comparison requires the same ordered accession/text hashes, pinned "
                      "model files and historical config hash. Differences below are observed "
                      "readback values, not a scientific acceptance threshold or a reproduction "
                      "of another member's small-token observation.", "",
                      "| 128-token pooling | Max abs company-vector difference | "
                      "Max abs cosine-matrix difference | Historical vector SHA-256 |",
                      "| --- | ---: | ---: | --- |"])
        for pool in POOLING:
            row = historical["pooling"][pool]
            lines.append(f"| `{pool}` | {_number(row['vector_max_abs'])} | "
                         f"{_number(row['cosine_max_abs'])} | "
                         f"`{row['historical_vector_sha256']}` |")
    lines.extend(["", "## Reading the complete machine result", "",
                  "`metrics.json` retains each arm's complete cosine matrix, ordered peer list, "
                  "Top-2 set and rank-2/3 gap; every firm's vector distance, Top-2 overlap, "
                  "margin change and tracked rank movement; every pair's signed and absolute "
                  "cosine change; each undefined Spearman reason; and every signed interaction. "
                  "The tables above summarize every declared arm and metric family without "
                  "discarding the underlying company and pair records.", "",
                  "No p-value, population claim, semantic improvement, market forecast, trading "
                  "loss or net economic benefit follows from this five-company, one-model probe. "
                  "M5 remains unfrozen.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path,
                        help="Existing ignored run directory with COMPLETE verified outputs")
    parser.add_argument("--output", required=True, type=Path,
                        help="New Markdown report path; existing files are never overwritten")
    parser.add_argument("--historical-m6-dense", type=Path,
                        help="Optional frozen M6 dense directory for 128-token comparison")
    args = parser.parse_args(argv)
    try:
        run = args.run.resolve(strict=True)
        verification = verify_saved_output(run)
        if verification != {"status": "PASS_SAVED_OUTPUT_RECOMPUTED", "run_status": "COMPLETE",
                             "evidence_class": "REAL_MODEL", "jobs_checked": 25,
                             "metrics_recomputed": True}:
            raise ValueError("Saved result is not a verified complete real-model grid")
        manifest_path = run / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_sha = file_sha256(manifest_path)
        if manifest_sha != (run / "manifest.sha256").read_text(encoding="ascii").strip():
            raise ValueError("Manifest changed after saved-output verification")
        metric_path = run / manifest["metric_file"]["path"]
        metrics = json.loads(metric_path.read_text(encoding="utf-8"))
        metrics_sha = file_sha256(metric_path)
        if metrics_sha != manifest["metric_file"]["sha256"]:
            raise ValueError("Metrics changed after saved-output verification")
        receipt_path = Path(manifest["receipt_path"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if file_sha256(receipt_path) != manifest["receipt_sha256"]:
            raise ValueError("Preflight receipt changed after saved-output verification")
        historical = (compare_m6_reference(run, manifest, metrics, args.historical_m6_dense)
                      if args.historical_m6_dense is not None else None)
        report = render_markdown(manifest, metrics, receipt, run_name=run.name,
                                 manifest_sha256=manifest_sha,
                                 metrics_sha256=metrics_sha,
                                 receipt_sha256=file_sha256(receipt_path),
                                 historical=historical)
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(report)
        print(json.dumps({"status": "DESCRIPTIVE_REPORT_WRITTEN", "path": str(output),
                          "sha256": file_sha256(output), "encoder_inference": "NOT_RUN_BY_REPORTER"}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, IndexError) as exc:
        print(f"REPORT_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
