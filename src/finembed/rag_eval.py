"""Source-bound development retrieval evaluation, never a finance benchmark.

The question labels are agent-authored software-development probes. A retrieved
chunk counts as a source hit only when its verified saved-text character span
fully contains the exact, hashed anchor. Partial overlap is not a hit.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable


MODES = ("bm25", "dense", "hybrid")
SCHEMA_VERSION = 1


class EvalError(RuntimeError):
    """The development set, source binding, or retrieval contract is invalid."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_development_set(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Recheck all exact saved source bytes and every annotated anchor."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "SOFTWARE_DEVELOPMENT_LABELS_NOT_INDEPENDENT_HUMAN_EVALUATION":
        raise EvalError("Question labels must retain their development-only status")
    sources: dict[str, str] = {}
    if not isinstance(data.get("source_files"), dict) or not isinstance(data.get("questions"), list):
        raise EvalError("Development set lacks source bindings or questions")
    for ticker, binding in data["source_files"].items():
        file = Path(binding["path"])
        if not file.is_file() or _file_sha(file) != binding["sha256"]:
            raise EvalError(f"Saved source file missing or changed: {ticker}")
        sources[ticker] = file.read_text(encoding="utf-8")
    ids: set[str] = set()
    for question in data["questions"]:
        qid = question.get("id")
        if not isinstance(qid, str) or qid in ids or not question.get("question"):
            raise EvalError("Question IDs must be unique and text must be nonempty")
        ids.add(qid)
        filters = question.get("filters")
        if not isinstance(filters, dict) or set(filters) - {"ticker", "accession", "section", "version"}:
            raise EvalError(f"Invalid filters: {qid}")
        expected = question.get("expected_evidence")
        if expected is None:
            if question.get("requires_abstention") is not True:
                raise EvalError(f"Missing evidence label without explicit unsupported probe: {qid}")
            continue
        ticker = expected.get("ticker")
        if ticker not in sources or expected.get("input_text_sha256") != data["source_files"][ticker]["sha256"]:
            raise EvalError(f"Evidence source identity differs from bound source file: {qid}")
        start, end = expected.get("char_start"), expected.get("char_end_exclusive")
        text = sources[ticker]
        if not (type(start) is int and type(end) is int and 0 <= start < end <= len(text)):
            raise EvalError(f"Invalid source character span: {qid}")
        anchor = text[start:end]
        if anchor != expected.get("anchor_text") or _sha(anchor.encode("utf-8")) != expected.get("anchor_sha256"):
            raise EvalError(f"Anchor text or hash differs from exact saved source span: {qid}")
    if not ids:
        raise EvalError("Empty development set")
    return data, sources


def _validated_hits(response: dict, filters: dict, sources: dict[str, str],
                    node_sources: dict[str, dict] | None = None) -> list[dict]:
    results = response.get("results")
    if not isinstance(results, list):
        raise EvalError("Retrieval response has no results list")
    seen: set[str] = set()
    for rank, hit in enumerate(results, 1):
        source = hit.get("source")
        node_id = hit.get("node_id")
        if (not isinstance(source, dict) or not isinstance(node_id, str) or node_id in seen
                or type(hit.get("rank")) is not int or hit["rank"] != rank
                or not math.isfinite(float(hit.get("score")))):
            raise EvalError("Retrieved ranks, IDs, scores, or source records are invalid")
        seen.add(node_id)
        for key, value in filters.items():
            source_key = "input_version" if key == "version" else key
            if value is not None and str(source.get(source_key)) != str(value):
                raise EvalError(f"Returned node violates {key} filter")
        ticker = source.get("ticker")
        start, end = source.get("chunk_char_start"), source.get("chunk_char_end_exclusive")
        if ticker not in sources or not (type(start) is int and type(end) is int and
                                         0 <= start < end <= len(sources[ticker])):
            raise EvalError("Returned node has no valid saved-text character span")
        if source.get("text_sha256") != _sha(sources[ticker].encode("utf-8")):
            raise EvalError("Returned node is bound to another saved text version")
        text = hit.get("text")
        if text != sources[ticker][start:end] or source.get("chunk_text_sha256") != _sha(text.encode("utf-8")):
            raise EvalError("Returned text differs from exact saved source span")
        if node_sources is not None:
            if node_id not in node_sources or node_sources[node_id] != source:
                raise EvalError("Returned node metadata differs from saved index node")
    branches = response.get("branches", {})
    if not isinstance(branches, dict):
        raise EvalError("Retrieval branches must be a mapping")
    for branch_hits in branches.values():
        if not isinstance(branch_hits, list):
            raise EvalError("Retrieval branch hits must be a list")
        branch_seen: set[str] = set()
        for branch_rank, hit in enumerate(branch_hits, 1):
            node_id = hit.get("node_id")
            if (not isinstance(node_id, str) or node_id in branch_seen or
                    type(hit.get("rank")) is not int or hit["rank"] != branch_rank or
                    not math.isfinite(float(hit.get("score")))):
                raise EvalError("Retrieval branch rank, ID, or score is invalid")
            branch_seen.add(node_id)
            if node_sources is not None:
                source = node_sources.get(node_id)
                if source is None:
                    raise EvalError("Branch references a missing index node")
                for key, value in filters.items():
                    source_key = "input_version" if key == "version" else key
                    if value is not None and str(source.get(source_key)) != str(value):
                        raise EvalError(f"Branch node violates {key} filter")
    return results


def _source_hit(hit: dict, expected: dict) -> tuple[bool, bool]:
    source = hit["source"]
    same_file = (source.get("ticker") == expected["ticker"] and
                 source.get("accession") == expected["accession"] and
                 source.get("text_sha256") == expected["input_text_sha256"])
    if not same_file:
        return False, False
    start, end = source["chunk_char_start"], source["chunk_char_end_exclusive"]
    a, b = expected["char_start"], expected["char_end_exclusive"]
    return start <= a and b <= end, start < b and a < end


def evaluate_set(index_dir: Path, data: dict, sources: dict[str, str],
                 retrieve_fn: Callable[..., dict], *, modes: tuple[str, ...] = MODES,
                 top_k: int = 5, node_sources: dict[str, dict] | None = None) -> dict:
    """Evaluate identical source-linked probes across modes, preserving failures."""
    if type(top_k) is not int or top_k < 1 or not modes or set(modes) - set(MODES):
        raise EvalError("Invalid top_k or retrieval modes")
    from .generation import answer_from_evidence

    cases: list[dict] = []
    for mode in modes:
        for question in data["questions"]:
            qid = question["id"]
            filters = question["filters"]
            row: dict[str, Any] = {"question_id": qid, "mode": mode,
                                   "filters": filters, "expected_evidence": question.get("expected_evidence"),
                                   "status": None, "evaluable": False, "hit_at_k": None,
                                   "first_full_span_rank": None, "partial_overlap_ranks": [],
                                   "required_limitation_exposed": None,
                                   "retrieval": None}
            try:
                response = retrieve_fn(index_dir, question["question"], mode,
                                       top_k=top_k, **filters)
                if not isinstance(response, dict) or response.get("mode") != mode or response.get("query") != question["question"]:
                    raise EvalError("Retrieval mode or query changed")
                status = response.get("status")
                if status not in {"OK", "NO_MATCH", "UNAVAILABLE", "NO_QUERY_TOKENS"}:
                    raise EvalError("Unexpected retrieval status")
                results = _validated_hits(response, filters, sources, node_sources)
                if status != "OK" and results:
                    raise EvalError("Non-OK response contains ranked results")
                row["status"] = status
                row["retrieval"] = response
                row["evaluable"] = status in {"OK", "NO_MATCH"}
                expected = question.get("expected_evidence")
                if expected is not None and row["evaluable"]:
                    for hit in results:
                        full, overlap = _source_hit(hit, expected)
                        if full and row["first_full_span_rank"] is None:
                            row["first_full_span_rank"] = hit["rank"]
                            required = question.get("required_limitation")
                            if required:
                                limits = hit["source"].get("limitations") or []
                                row["required_limitation_exposed"] = any(required in str(x) for x in limits)
                        elif overlap and not full:
                            row["partial_overlap_ranks"].append(hit["rank"])
                    row["hit_at_k"] = row["first_full_span_rank"] is not None
                elif expected is None:
                    answer = answer_from_evidence(question["question"], response, backend=None)
                    row["no_generated_answer"] = answer.get("answer") is None
                    row["response_mode"] = answer.get("mode")
                    row["response_reason"] = answer.get("reason")
                    row["semantic_abstention_verified"] = answer.get("mode") == "abstained"
            except Exception as exc:  # A failed query remains a failed query, never a fallback.
                row["status"] = "ERROR"
                row["error_type"] = type(exc).__name__
                row["error"] = str(exc)
            cases.append(row)
    summary: dict[str, dict] = {}
    for mode in modes:
        evidence = [x for x in cases if x["mode"] == mode and x["expected_evidence"] is not None]
        evaluable = [x for x in evidence if x["evaluable"]]
        hits = sum(x["hit_at_k"] is True for x in evaluable)
        required = [x for x in evidence if any(q["id"] == x["question_id"] and
                    q.get("required_limitation") for q in data["questions"])]
        summary[mode] = {"source_linked_questions": len(evidence),
                         "evaluable_denominator": len(evaluable), "full_span_hits": hits,
                         "hit_at_k": hits / len(evaluable) if evaluable else None,
                         "undefined_reason": None if evaluable else "NO_EVALUABLE_EVIDENCE_QUERIES",
                         "required_limitation_checks": [
                             {"question_id": x["question_id"],
                              "exposed_on_first_full_span_hit": x["required_limitation_exposed"]}
                             for x in required],
                         "status_counts": dict(Counter(x["status"] for x in cases if x["mode"] == mode)),
                         "unsupported_probe": [
                             {key: x.get(key) for key in ("status", "no_generated_answer", "response_mode",
                                                          "response_reason", "semantic_abstention_verified")}
                             for x in cases if x["mode"] == mode and x["expected_evidence"] is None]}
    return {"schema_version": SCHEMA_VERSION,
            "scope": "AGENT_AUTHORED_SOURCE_LINKED_SOFTWARE_DEVELOPMENT_SMOKE",
            "not_independent_human_evaluation": True,
            "not_financial_experiment": True,
            "hit_rule": "same ticker/accession/text SHA-256; retrieved saved-text chunk fully contains hashed anchor",
            "modes": list(modes), "top_k": top_k, "summary": summary, "cases": cases}


def run_evaluation(index_dir: Path, questions_path: Path, output: Path,
                   *, modes: tuple[str, ...] = MODES, top_k: int = 5) -> dict:
    from .rag import retrieve
    output = output.resolve()
    if output.exists():
        raise EvalError("Output directory exists; use a new unique run directory")
    data, sources = load_development_set(questions_path)
    index_manifest = index_dir.resolve(strict=True) / "manifest.json"
    manifest = json.loads(index_manifest.read_text(encoding="utf-8"))
    indexed = {(doc["identity"]["ticker"], doc["identity"]["accession"],
                doc["identity"]["text_sha256"]) for doc in manifest["documents"]}
    for question in data["questions"]:
        expected = question.get("expected_evidence")
        if expected is not None and (expected["ticker"], expected["accession"],
                                     expected["input_text_sha256"]) not in indexed:
            raise EvalError(f"Question source is absent from exact index: {question['id']}")
    node_sources = {node["node_id"]: node["metadata"] for node in
                    (json.loads(line) for line in (index_dir / "nodes.jsonl").read_text(encoding="utf-8").splitlines())}
    result = evaluate_set(index_dir, data, sources, retrieve, modes=modes,
                          top_k=top_k, node_sources=node_sources)
    output.mkdir(parents=True, exist_ok=False)
    result_path = output / "evaluation.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bindings = {"development_questions.json": _file_sha(questions_path),
                "index_manifest.json": _file_sha(index_manifest),
                "index_nodes.jsonl": _file_sha(index_dir / "nodes.jsonl"),
                "rag_eval.py": _file_sha(Path(__file__)),
                "rag.py": _file_sha(Path(__file__).with_name("rag.py")),
                "generation.py": _file_sha(Path(__file__).with_name("generation.py"))}
    audit = {"created_utc": datetime.now(timezone.utc).isoformat(),
             "command": ["python", "-m", "finembed.rag_eval", "--index", str(index_dir),
                         "--questions", str(questions_path), "--output", str(output),
                         "--modes", *modes, "--top-k", str(top_k)],
             "source_file_sha256": {ticker: binding["sha256"] for ticker, binding in data["source_files"].items()},
             "input_and_code_sha256": bindings,
             "evaluation_sha256": _file_sha(result_path),
             "label_provenance": data["status"], "financial_experiment": "NOT_RUN"}
    (output / "manifest.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "evaluation_sha256": audit["evaluation_sha256"],
            "summary": result["summary"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        result = run_evaluation(args.index, args.questions, args.output,
                                modes=tuple(args.modes), top_k=args.top_k)
    except (EvalError, ValueError, FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "reason": str(exc)}),
              file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
