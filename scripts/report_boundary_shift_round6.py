"""Render a verified S3 CPU engineering run from saved machine output only.

This script rechecks the saved run before writing Markdown. It does not create
an Encoder, load model weights, or make model forward calls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from finembed.boundary_shift_runner import verify_saved_output
from finembed.sensitivity_preflight import file_sha256


SHIFTS = (-10, -5, 0, 5, 10)
POOLING = ("equal_chunks", "body_token_weighted")
COMPANIES = ("AAPL", "MSFT", "AMZN", "GOOGL", "META")


def _number(value: float | None) -> str:
    return "undefined" if value is None else format(float(value), ".9g")


def _signed_pair_mean(comparison: dict) -> float:
    pairs = comparison["similarity_change"]["upper_triangle_pairs"]
    if len(pairs) != 10:
        raise ValueError("A comparison lacks ten dependent company pairs")
    return sum(row["signed_cosine_change"] for row in pairs) / 10


def _gap_change_mean(comparison: dict) -> float:
    margins = comparison["rank2_rank3_margin"]
    if set(margins) != set(COMPANIES):
        raise ValueError("A comparison lacks five company rank margins")
    return sum(row["candidate_minus_reference"] for row in margins.values()) / 5


def _comparison_cells(comparison: dict) -> list[str]:
    similarity = comparison["similarity_change"]
    return [
        _number(comparison["company_vector_cosine_distance"]["mean"]),
        _number(_signed_pair_mean(comparison)),
        _number(similarity["mean_absolute"]),
        _number(similarity["maximum_absolute"]),
        _number(comparison["upper_triangle_spearman"]["value"]),
        _number(comparison["top2_overlap"]["mean"]),
        _number(comparison["reference_rank2_rank3_peer_movement"]
                ["mean_absolute_rank_shift"]),
        _number(_gap_change_mean(comparison)),
    ]


def _require_complete(manifest: dict, metrics: dict, receipt: dict) -> None:
    """Check presentation-level completeness after saved-output verification."""
    if (manifest.get("kind") != "BOUNDARY_SHIFT_PROBE_RUN" or
            manifest.get("status") != "COMPLETE" or
            manifest.get("evidence_class") != "REAL_MODEL" or
            manifest.get("decision_validation", {}).get("status") != "ACCEPTED" or
            manifest.get("axis") != "boundary_shift" or
            manifest.get("reference_body_tokens") != 128 or
            manifest.get("reference_boundary_shift") != 0 or
            manifest.get("boundary_shifts") != list(SHIFTS) or
            manifest.get("pooling") != list(POOLING) or
            manifest.get("failures") != [] or
            receipt.get("status") != "PASS_TOKENIZATION_ONLY"):
        raise ValueError("A complete, decision-bound real S3 grid is required")
    if (metrics.get("kind") != "BOUNDARY_SHIFT_DESCRIPTIVE_METRICS" or
            metrics.get("status") != "COMPLETE_NUMERICAL_ONLY" or
            metrics.get("inference_provenance") != "REAL_MODEL" or
            metrics.get("axis") != "boundary_shift" or
            metrics.get("base_step_body_tokens") != 128 or
            metrics.get("reference_boundary_shift") != 0 or
            metrics.get("candidate_boundary_shifts") != list(SHIFTS) or
            metrics.get("company_order") != list(COMPANIES) or
            metrics.get("pooling_methods") != list(POOLING) or
            metrics.get("denominators") != {"companies": 5,
                "upper_triangle_pairs": 10,
                "tracked_reference_rank2_rank3_peers": 10}):
        raise ValueError("S3 metric identity or denominators differ from the grid")
    if (set(metrics.get("arms", {})) != {str(x) for x in SHIFTS} or
            any(set(metrics["arms"][str(x)]) != set(POOLING) for x in SHIFTS) or
            set(metrics.get("within_pooling_shift_comparisons", {})) !=
            {str(x) for x in SHIFTS if x != 0} or
            set(metrics.get("same_shift_pooling_comparisons", {})) !=
            {str(x) for x in SHIFTS} or
            set(metrics.get("signed_shift_by_pooling_interactions", {})) !=
            {str(x) for x in SHIFTS if x != 0}):
        raise ValueError("S3 metric arm or comparison grid is incomplete")
    if ([row["ticker"] for row in manifest.get("input_versions", [])] != list(COMPANIES) or
            [row["ticker"] for row in receipt.get("documents", [])] != list(COMPANIES) or
            manifest.get("logical_jobs_completed") != 25 or
            manifest.get("chunk_vectors_completed") !=
            receipt.get("planned_counts", {}).get("chunk_vectors") or
            manifest.get("company_vectors_completed") != 50):
        raise ValueError("Saved input or execution counts differ from the S3 preflight")
    expected = {(company, delta) for company in COMPANIES for delta in SHIFTS}
    observed = {(row["ticker"], row["boundary_shift"])
                for row in manifest.get("jobs", []) if row["status"] == "COMPLETE"}
    if len(manifest.get("jobs", [])) != 25 or observed != expected:
        raise ValueError("The saved S3 job inventory is incomplete")


def _render_markdown(manifest: dict, metrics: dict, receipt: dict, *,
                     run_name: str, manifest_sha256: str, metrics_sha256: str,
                     receipt_sha256: str) -> str:
    """Format checked machine fields; the CLI performs full vector readback."""
    _require_complete(manifest, metrics, receipt)
    lines = [
        "# S3 boundary-shift engineering probe: descriptive results", "",
        "**Scope.** One complete fixed-five-company, fixed-MiniLM/ONNX CPU grid. "
        "The experimental axis is an internal boundary shift at a constant "
        "128-token base step, not a change in nominal chunk length. This is "
        "exploratory after round 4. It does not establish a pure semantic-boundary "
        "cause, general model stability, semantic quality, financial prediction, "
        "trading loss or economic value.", "",
        f"Saved run: `{run_name}`. Saved-output verification: "
        "`PASS_SAVED_OUTPUT_RECOMPUTED`; run status: `COMPLETE`; "
        "evidence class: `REAL_MODEL`.", "",
        "| Bound object | SHA-256 or identity |", "| --- | --- |",
        f"| Manifest | `{manifest_sha256}` |",
        f"| Metrics | `{metrics_sha256}` |",
        f"| S3 tokenizer-only receipt | `{receipt_sha256}` |",
        f"| Candidate | `{manifest['candidate_sha256']}` |",
        f"| S3 metric contract | `{manifest['metric_contract_sha256']}` |",
        f"| Private decision record hash only | `{manifest['decision_sha256']}` |",
        f"| Input manifest | `{manifest['input_manifest_sha256']}` |",
        f"| Execution code commit | `{manifest['git_at_run']['head']}` |",
        f"| Model revision | `{manifest['model_identity']['revision']}` |",
        f"| Provider / batch size | `{manifest['model_identity']['provider']}` / "
        f"`{manifest['batch_size']}` |", "",
        "**Ordered source versions.** " + "; ".join(
            f"{row['ticker']} `{row['accession']}` Item 1 `{row['item1_sha256']}`"
            for row in manifest["input_versions"]) + ".", "",
        "**Actual execution.** "
        f"{manifest['logical_jobs_completed']}/{manifest['logical_job_count_planned']} "
        "document-shift jobs; "
        f"{manifest['chunk_vectors_completed']}/{manifest['chunk_vector_count_planned']} "
        "chunk vectors; "
        f"{manifest['company_vectors_completed']}/{manifest['company_vector_count_planned']} "
        "company vectors; "
        f"{manifest['real_encoder_sessions_created']} real Encoder/ONNX session; "
        f"{manifest['actual_batch_call_attempts']} actual batch-call attempts. "
        "The two pooling arms reuse each document-shift job's chunk vectors. "
        f"Measured process wall time from output-directory creation: "
        f"{_number(manifest['process_wall_seconds_from_output_creation'])} s; "
        "peak memory: UNKNOWN (not measured).", "",
        "## All ten shift-by-pooling arms", "",
        "Delta zero is the reference for within-pooling shift comparisons; "
        "its self-comparison is identically zero and is not presented as a "
        "separate effect arm. Each row has five firms. All chunks were encoded "
        "once per company and delta.", "",
        "| Boundary shift (body tokens) | Pooling | Jobs | Chunk vectors | "
        "Company vectors | Mean rank-2/3 score gap | Exact cutoff ties |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for delta in SHIFTS:
        jobs = [job for job in manifest["jobs"] if job["boundary_shift"] == delta]
        chunks = sum(job["chunk_count"] for job in jobs)
        for pool in POOLING:
            arm = metrics["arms"][str(delta)][pool]
            margins = arm["rank2_rank3"]
            if set(margins) != set(COMPANIES):
                raise ValueError("S3 arm lacks complete rank margins")
            mean_gap = sum(row["rank2_minus_rank3"] for row in margins.values()) / 5
            ties = sum(bool(row["exact_cutoff_tie"]) for row in margins.values())
            lines.append(f"| {delta:+d} | `{pool}` | 5 | {chunks} | 5 | "
                         f"{_number(mean_gap)} | {ties} |")
    lines.extend(["", "## Nonzero shifts against delta zero within the same pooling", "",
                  "Each signed pair change is candidate cosine minus its same-pooling "
                  "delta-zero cosine. Absolute pair changes and Spearman use ten "
                  "dependent company pairs; Top-2 overlap averages five firms.", "",
                  "| Delta | Pooling | Mean vector cosine distance | Mean signed "
                  "pair change | Mean absolute pair change | Max absolute pair "
                  "change | Pair-rank Spearman | Mean Top-2 overlap | Mean "
                  "absolute tracked-rank shift | Mean rank-2/3 gap change |",
                  "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
    undefined = []
    for delta in SHIFTS:
        if delta == 0:
            continue
        for pool in POOLING:
            comp = metrics["within_pooling_shift_comparisons"][str(delta)][pool]
            if comp["upper_triangle_spearman"]["value"] is None:
                undefined.append((f"delta {delta:+d} / {pool}",
                                  comp["upper_triangle_spearman"].get("reason")))
            lines.append(f"| {delta:+d} | `{pool}` | " +
                         " | ".join(_comparison_cells(comp)) + " |")
    lines.extend(["", "## Pooling comparison at every fixed shift", "",
                  "`body_token_weighted` minus `equal_chunks` uses the same "
                  "stored chunk vectors within each company and shift.", "",
                  "| Delta | Mean vector cosine distance | Mean signed pair change | "
                  "Mean absolute pair change | Max absolute pair change | "
                  "Pair-rank Spearman | Mean Top-2 overlap | Mean absolute "
                  "tracked-rank shift | Mean rank-2/3 gap change |",
                  "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for delta in SHIFTS:
        comp = metrics["same_shift_pooling_comparisons"][str(delta)]
        if comp["upper_triangle_spearman"]["value"] is None:
            undefined.append((f"pooling at delta {delta:+d}",
                              comp["upper_triangle_spearman"].get("reason")))
        lines.append(f"| {delta:+d} | " + " | ".join(_comparison_cells(comp)) + " |")
    lines.extend(["", "## Signed shift-by-pooling interaction", "",
                  "For every nonzero delta and dependent pair, "
                  "`I=[S(delta,weighted)-S(0,weighted)]-"
                  "[S(delta,equal)-S(0,equal)]`. Neither absolute changes nor "
                  "causal effects enter this signed difference of contrasts.", "",
                  "| Delta | Mean signed interaction | Minimum / maximum pair "
                  "interaction | Dependent pair count |",
                  "| ---: | ---: | --- | ---: |"])
    pair_rows = []
    for delta in SHIFTS:
        if delta == 0:
            continue
        interaction = metrics["signed_shift_by_pooling_interactions"][str(delta)]
        pairs = interaction["upper_triangle_pairs"]
        if len(pairs) != 10 or interaction["pair_denominator"] != 10:
            raise ValueError("S3 interaction lacks ten pairwise values")
        values = [row["weighted_minus_equal_interaction"] for row in pairs]
        lines.append(f"| {delta:+d} | {_number(interaction['mean_signed'])} | "
                     f"{_number(min(values))} / {_number(max(values))} | 10 |")
        for row in pairs:
            pair_rows.append(f"| {delta:+d} | {' / '.join(row['companies'])} | "
                             f"{_number(row['equal_shift_effect'])} | "
                             f"{_number(row['weighted_shift_effect'])} | "
                             f"{_number(row['weighted_minus_equal_interaction'])} |")
    lines.extend(["", "All declared signed company-pair interactions:", "",
                  "| Delta | Company pair | Equal signed shift effect | Weighted "
                  "signed shift effect | Weighted minus equal interaction |",
                  "| ---: | --- | ---: | ---: | ---: |", *pair_rows, "",
                  "## Every Top-2 peer-set change", "",
                  "The reference is delta zero under the same pooling. Peer "
                  "order and rank-2/3 cutoff gaps are shown separately. The "
                  "table includes every firm and nonzero-shift arm whose "
                  "Top-2 *set* changed.", "",
                  "| Delta | Pooling | Firm | Reference Top-2 | Shifted Top-2 | "
                  "Reference rank 2 / rank 3 (gap) | Shifted rank 2 / rank 3 (gap) |",
                  "| ---: | --- | --- | --- | --- | --- | --- |"])
    changes_by_firm = {company: 0 for company in COMPANIES}
    for delta in SHIFTS:
        if delta == 0:
            continue
        for pool in POOLING:
            ref = metrics["arms"]["0"][pool]
            shifted = metrics["arms"][str(delta)][pool]
            for company in COMPANIES:
                before_top2 = ref["top2"][company]
                after_top2 = shifted["top2"][company]
                if set(before_top2) == set(after_top2):
                    continue
                changes_by_firm[company] += 1
                before = ref["rank2_rank3"][company]
                after = shifted["rank2_rank3"][company]
                lines.append(f"| {delta:+d} | `{pool}` | {company} | "
                             f"{', '.join(before_top2)} | {', '.join(after_top2)} | "
                             f"{before['rank2']} / {before['rank3']} "
                             f"({_number(before['rank2_minus_rank3'])}) | "
                             f"{after['rank2']} / {after['rank3']} "
                             f"({_number(after['rank2_minus_rank3'])}) |")
    if not sum(changes_by_firm.values()):
        lines.append("| — | — | None | — | — | — | — |")
    lines.extend(["", "Top-2 set-change rows by firm: " + ", ".join(
        f"{company} {count}" for company, count in changes_by_firm.items()) +
        f"; total {sum(changes_by_firm.values())}. These are dependent "
        "firm/arm contrasts, not independent samples.", "",
        "## Every same-delta pooling Top-2 peer-set change", "",
        "At each fixed boundary shift, `equal_chunks` is the reference and "
        "`body_token_weighted` is the candidate. This table lists every firm "
        "whose Top-2 peer set changes under that pooling contrast, including "
        "delta zero.", "",
        "| Delta | Firm | Equal Top-2 | Weighted Top-2 | "
        "Equal rank 2 / rank 3 (gap) | Weighted rank 2 / rank 3 (gap) |",
        "| ---: | --- | --- | --- | --- | --- |"])
    pooling_changes_by_firm = {company: 0 for company in COMPANIES}
    for delta in SHIFTS:
        equal = metrics["arms"][str(delta)]["equal_chunks"]
        weighted = metrics["arms"][str(delta)]["body_token_weighted"]
        for company in COMPANIES:
            before_top2 = equal["top2"][company]
            after_top2 = weighted["top2"][company]
            if set(before_top2) == set(after_top2):
                continue
            pooling_changes_by_firm[company] += 1
            before = equal["rank2_rank3"][company]
            after = weighted["rank2_rank3"][company]
            lines.append(f"| {delta:+d} | {company} | "
                         f"{', '.join(before_top2)} | {', '.join(after_top2)} | "
                         f"{before['rank2']} / {before['rank3']} "
                         f"({_number(before['rank2_minus_rank3'])}) | "
                         f"{after['rank2']} / {after['rank3']} "
                         f"({_number(after['rank2_minus_rank3'])}) |")
    if not sum(pooling_changes_by_firm.values()):
        lines.append("| — | None | — | — | — | — |")
    lines.extend(["", "Same-delta pooling set-change rows by firm: " + ", ".join(
        f"{company} {count}" for company, count in pooling_changes_by_firm.items()) +
        f"; total {sum(pooling_changes_by_firm.values())}. The pooling "
        "contrast reuses identical stored chunk vectors within each job.", "",
        "## First and tail body lengths", "",
        "Each cell is `first/tail` body token IDs from the tokenizer-only "
        "receipt, not an inferred model effect. Complete interior chunks "
        "remain at the 128-token base step.", "",
        "| Firm | Total body IDs | Chunks per delta | -10 | -5 | 0 | +5 | +10 |",
        "| --- | ---: | ---: | --- | --- | --- | --- | --- |"])
    for row in receipt["documents"]:
        arms = [row["shifts"][str(delta)] for delta in SHIFTS]
        lengths = [f"{arm['first_tokens']}/{arm['tail_tokens']}" for arm in arms]
        if any(arm["chunk_count"] != arms[0]["chunk_count"] for arm in arms):
            raise ValueError("Preflight S3 chunk count changed within one firm")
        lines.append(f"| {row['ticker']} | {row['body_tokens']} | "
                     f"{arms[0]['chunk_count']} | " + " | ".join(lengths) + " |")
    lines.extend(["", "Equal pooling uses `1/n` for every chunk. Body-token "
                  "weighted pooling uses the *actual* shifted chunk length "
                  "divided by the full body-token count, so first and tail "
                  "weights also vary with delta. AMZN's negative-shift long "
                  "tail remains intact if within the pinned model budget.", "",
                  "## Failures, exclusions and undefined measures", "",
                  "Failures: **0**. Excluded firms/arms: **0**. "
                  "A partial grid would have no full-grid metrics file.", ""])
    if undefined:
        lines.extend([f"Undefined Spearman comparisons: **{len(undefined)}**.", ""])
        for label, reason in undefined:
            lines.append(f"- {label}: {reason or 'reason missing'}")
        lines.append("")
    else:
        lines.extend(["Undefined Spearman comparisons: **0**.", ""])
    lines.extend(["## Interpretation boundary", "",
                  "The metric file retains every company vector distance, "
                  "signed and absolute pair change, all five-firm cosine "
                  "matrices, ordered neighbors, rank-2/3 identities and gaps, "
                  "tracked reference-neighbor movement, exact ties and "
                  "undefined reasons. The ten company pairs share firms. "
                  "No p-value, danger cutoff or selected default arm follows.", "",
                  "S3 keeps the full text and chunk count, but moves chunk "
                  "content, first/tail lengths and weighted-pooling weights. "
                  "The result cannot isolate a pure semantic-boundary cause. "
                  "No training, generation, MoE, new retrieval evaluation or "
                  "formal financial experiment was run by this probe.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path,
                        help="Existing ignored S3 output directory")
    parser.add_argument("--output", required=True, type=Path,
                        help="New English Markdown report; never overwrite")
    args = parser.parse_args(argv)
    try:
        run = args.run.resolve(strict=True)
        result = verify_saved_output(run)
        if result != {"status": "PASS_SAVED_OUTPUT_RECOMPUTED",
                      "run_status": "COMPLETE", "evidence_class": "REAL_MODEL",
                      "jobs_checked": 25, "metrics_recomputed": True}:
            raise ValueError("Saved S3 output is not a verified complete real grid")
        manifest_path = run / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_sha = file_sha256(manifest_path)
        if manifest_sha != (run / "manifest.sha256").read_text(encoding="ascii").strip():
            raise ValueError("Manifest changed after saved-output verification")
        metrics_path = run / manifest["metric_file"]["path"]
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics_sha = file_sha256(metrics_path)
        if metrics_sha != manifest["metric_file"]["sha256"]:
            raise ValueError("Metrics changed after saved-output verification")
        receipt_path = Path(manifest["receipt_path"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt_sha = file_sha256(receipt_path)
        if receipt_sha != manifest["receipt_sha256"]:
            raise ValueError("Preflight receipt changed after saved-output verification")
        report = _render_markdown(manifest, metrics, receipt, run_name=run.name,
                                  manifest_sha256=manifest_sha,
                                  metrics_sha256=metrics_sha,
                                  receipt_sha256=receipt_sha)
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(report)
        print(json.dumps({"status": "DESCRIPTIVE_REPORT_WRITTEN",
                          "path": str(output), "sha256": file_sha256(output),
                          "encoder_inference": "NOT_RUN_BY_REPORTER"}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, IndexError) as exc:
        print(f"REPORT_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
