"""Dynamic source-bound scoring for the v2.5 training-material regression.

The caller supplies a frozen, private source reference. This module never
chooses production tables or reads reference answers in a parser/packer path.
Mechanical reachability, source-reviewed semantics, and model-visible input
are separate decisions. It does not read return labels or fit a model.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Any


RELATION_FIELDS = (
    "row_metric", "column_header", "period", "currency", "scale",
    "measurement_kind", "basis", "footnote",
)
FOOTNOTE_STATES = {
    "NO_APPLICABLE_FOOTNOTE", "LINKED", "MARKER_UNRESOLVED",
    "CONFLICT", "NOT_REVIEWED",
}
PARSER_STATES = {"EXPLICIT", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE"}
COMPLETE_DECLARATIONS = {"SOURCE_SUPPORTED", "SEMANTICALLY_COMPLETE", "CONFIRMED_FACT"}


class ExpandedEvaluationError(ValueError):
    """A frozen source or parser/pack result violates the evaluation contract."""


def _norm(value: Any) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split()).casefold()


def _unique_index(rows: list[dict], key_fn, label: str) -> dict:
    result = {}
    for row in rows:
        key = key_fn(row)
        if key in result:
            raise ExpandedEvaluationError(f"duplicate {label}: {key}")
        result[key] = row
    return result


def _paths(relation: dict) -> list[str]:
    paths = relation.get("source_dom_paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p for p in paths):
        raise ExpandedEvaluationError("malformed source_dom_paths")
    if len(paths) != len(set(paths)):
        raise ExpandedEvaluationError("duplicate relation source path")
    return paths


def _component(binding: dict, field: str) -> dict:
    if field in {"currency", "scale", "measurement_kind"}:
        unit = binding.get("unit")
        relation = unit.get(field) if isinstance(unit, dict) else None
    elif field == "basis":
        relation = binding.get("basis_relation")
    else:
        relation = binding.get(field)
    if not isinstance(relation, dict) or relation.get("status") not in PARSER_STATES:
        raise ExpandedEvaluationError(f"missing or invalid {field} relation")
    if relation["status"] == "EXPLICIT" and not _paths(relation):
        raise ExpandedEvaluationError(f"explicit {field} without source path")
    _paths(relation)
    return relation


def _flat(binding: dict, field: str) -> Any:
    flat_names = {
        "row_metric": "row_metric_text", "column_header": "column_header_dom_path",
        "period": "period_text", "basis": "basis",
    }
    key = flat_names.get(field, field)
    return binding.get(key, _component(binding, field).get("value"))


def _expected_paths(expected: dict, field: str,
                    table_cells: dict[str, dict] | None = None) -> tuple[set[str], set[str]]:
    """Return required and allowed source paths, supporting multi-level headers.

    `field_source_paths` is a frozen source-review extension. Every emitted
    explicit path must be in its `allowed` set; every `required` path must be
    emitted. The legacy four-table reference is normalized without changing it.
    """
    custom = expected.get("field_source_paths", {}).get(field)
    if custom is not None:
        if not isinstance(custom, dict):
            raise ExpandedEvaluationError("field_source_paths entry must be an object")
        required = set(custom.get("required", []))
        allowed = set(custom.get("allowed", custom.get("required", [])))
        if not required <= allowed:
            raise ExpandedEvaluationError("required source path not allowed")
        if any(not isinstance(p, str) or not p for p in required | allowed):
            raise ExpandedEvaluationError("invalid expected source path")
        return required, allowed
    def present(*keys: str) -> set[str]:
        out = set()
        for key in keys:
            v = expected.get(key)
            if isinstance(v, str) and v:
                out.add(v)
            elif isinstance(v, list):
                out.update(p for p in v if isinstance(p, str) and p)
        return out
    value = present("value_dom_path")
    row = present("row_label_dom_paths", "row_label_dom_path")
    headers = present("column_header_dom_paths", "column_header_dom_path")
    unit = present("unit_dom_paths", "unit_dom_path")
    basis = present("basis_dom_paths", "basis_dom_path")
    if field == "row_metric":
        return row, row
    if field == "column_header":
        return headers, headers
    if field == "period":
        return (headers, headers) if expected.get("period_text") is not None else (set(), set())
    if field == "currency":
        explicit = value if "$" in str(expected.get("value_text", "")) else set()
        markers = present("currency_dom_paths")
        anchor = (table_cells or {}).get(expected.get("value_dom_path"), {})
        for path, cell in (table_cells or {}).items():
            if (cell.get("row_start") == anchor.get("row_start")
                    and cell.get("column_end") == anchor.get("column_start")
                    and _norm(cell.get("text")) == "$"):
                markers.add(path)
        return (explicit | markers, explicit | markers) if expected.get("currency") is not None else (set(), set())
    if field == "scale":
        return (unit, unit) if expected.get("scale") is not None else (set(), set())
    if field == "measurement_kind":
        allowed = row | value | unit | present("measurement_kind_dom_paths")
        anchor = (table_cells or {}).get(expected.get("value_dom_path"), {})
        for path, cell in (table_cells or {}).items():
            if (cell.get("row_start") == anchor.get("row_start")
                    and cell.get("column_start") == anchor.get("column_end")
                    and _norm(cell.get("text")) == "%"):
                allowed.add(path)
        if expected.get("comparison_label") is not None:
            allowed |= headers
        # Parser can cite any subset of these direct supporting source parts.
        return set(), allowed
    if field == "basis":
        return (basis, basis) if expected.get("basis_text") is not None else (set(), set())
    if field == "footnote":
        markers = present("footnote_marker_dom_paths")
        definitions = present("footnote_definition_dom_paths", "footnote_definition_dom_path")
        return set(), markers | definitions | row | headers | basis | value
    raise ExpandedEvaluationError(f"unknown relation field {field}")


def _footnote_expectation(expected: dict) -> str:
    value = expected.get("footnote_expectation")
    if value is None:
        if expected.get("footnote_link_status"):
            value = "MARKER_UNRESOLVED"
        elif expected.get("footnote_status") == "NONE_REQUIRED_IN_SELECTED_TABLE":
            value = "NO_APPLICABLE_FOOTNOTE"
        else:
            value = "NOT_REVIEWED"
    if value not in FOOTNOTE_STATES:
        raise ExpandedEvaluationError(f"invalid footnote expectation {value}")
    return value


def _score_footnote(expected: dict, relation: dict,
                    table_cells: dict[str, dict]) -> str:
    expectation = _footnote_expectation(expected)
    status = relation["status"]
    paths = set(_paths(relation))
    required, allowed = _expected_paths(expected, "footnote", table_cells)
    if expectation == "NOT_REVIEWED":
        return "NOT_REVIEWED"
    if expectation == "NO_APPLICABLE_FOOTNOTE":
        return "CORRECT_NA" if status == "NOT_APPLICABLE" and not paths else "WRONG_NA_OR_LINK"
    if expectation == "LINKED":
        return "CORRECT_LINK" if status == "EXPLICIT" and required <= paths <= allowed else "MISSED_OR_WRONG_LINK"
    if expectation == "MARKER_UNRESOLVED":
        markers = set(expected.get("footnote_marker_dom_paths", []))
        # A legacy reference identifies the marker by its literal text, but
        # may not provide a separate marker path; review it separately.
        if not markers and expected.get("footnote_marker"):
            markers = {p for p in paths & allowed
                       if str(expected["footnote_marker"]) in str(table_cells.get(p, {}).get("text", ""))}
        return "CORRECT_ABSTENTION" if status == "UNKNOWN" and bool(markers) and paths <= allowed else "WRONG_MARKER_OR_APPLICABILITY"
    return "CORRECT_CONFLICT" if status == "CONFLICT" else "MISSED_CONFLICT"


def score_expected_cell(expected: dict, binding: dict | None,
                        *, forbidden_header_paths: set[str] | None = None,
                        table_cells: dict[str, dict] | None = None) -> dict:
    """Score one exact DOM value-cell anchor against a frozen source relation."""
    forbidden_header_paths = forbidden_header_paths or set()
    table_cells = table_cells or {}
    row = {
        "value_dom_path": expected["value_dom_path"], "matched": binding is not None,
        "field_decisions": {}, "wrong_explicit_paths": [], "missing_required_paths": [],
        "forbidden_header_paths": [], "known_fields_complete": False,
        "all_fields_complete": False, "wrong_complete_declaration": False,
    }
    if binding is None:
        row["state"] = "MISSED_CELL"
        return row
    if binding.get("value_cell_dom_path") != expected["value_dom_path"]:
        raise ExpandedEvaluationError("amount-only or wrong-cell match rejected")
    row["anchor_exact"] = _norm(binding.get("value_text")) == _norm(expected.get("value_text"))
    row["geometry_exact"] = (
        binding.get("row_index") == expected.get("row_index_zero_based")
        and [binding.get("column_start"), binding.get("column_end")]
        == expected.get("logical_column_span")
    ) if "row_index_zero_based" in expected else True
    expected_values = {
        "row_metric": expected.get("row_metric"),
        "period": expected.get("period_text"),
        "currency": expected.get("currency"),
        "scale": expected.get("scale"),
        "measurement_kind": expected.get("measurement_kind"),
        "basis": expected.get("basis_text"),
    }
    wrong = not row["anchor_exact"] or not row["geometry_exact"]
    unknown_on_known = False
    source_unresolved = False
    for field in RELATION_FIELDS:
        relation = _component(binding, field)
        status = relation["status"]
        paths = set(_paths(relation))
        required, allowed = _expected_paths(expected, field, table_cells)
        extra = paths - allowed if status == "EXPLICIT" else set()
        missing = required - paths if status == "EXPLICIT" else required
        if extra:
            row["wrong_explicit_paths"].extend(f"{field}:{p}" for p in sorted(extra))
        if missing:
            row["missing_required_paths"].extend(f"{field}:{p}" for p in sorted(missing))
        if field == "column_header":
            forbidden = paths & forbidden_header_paths
            row["forbidden_header_paths"] = sorted(forbidden)
            decision = "CORRECT_EXPLICIT" if status == "EXPLICIT" and not extra and not missing and not forbidden else "WRONG_OR_UNRESOLVED_HEADER"
        elif field == "footnote":
            decision = _score_footnote(expected, relation, table_cells)
            source_unresolved |= decision in {"CORRECT_ABSTENTION", "CORRECT_CONFLICT", "NOT_REVIEWED"}
        else:
            target = expected_values[field]
            observed = _flat(binding, field)
            if field == "period" and expected.get("comparison_label") is not None and target is None:
                expected_na = True
            elif field == "scale" and target is None:
                expected_na = True
            elif field == "currency" and target is None and expected.get("measurement_kind") not in {"amount", "per_share_amount"}:
                expected_na = True
            else:
                expected_na = False
            if target is None:
                if expected_na:
                    decision = "CORRECT_NA" if status == "NOT_APPLICABLE" and observed is None else "WRONG_APPLICABILITY"
                elif status in {"UNKNOWN", "CONFLICT"} and observed is None:
                    decision = "CORRECT_ABSTENTION"
                    source_unresolved = True
                else:
                    decision = "WRONG_UNKNOWN_FIELD"
            elif status == "EXPLICIT" and _norm(observed) == _norm(target) and not extra and not missing:
                decision = "CORRECT_EXPLICIT"
            elif status in {"UNKNOWN", "CONFLICT"}:
                decision = "UNKNOWN_ON_KNOWN"
                unknown_on_known = True
            else:
                decision = "WRONG_EXPLICIT_OR_APPLICABILITY"
        row["field_decisions"][field] = decision
        if decision.startswith("WRONG") or decision.startswith("MISSED"):
            wrong = True
    if expected.get("comparison_label") is not None and _norm(binding.get("comparison_label")) != _norm(expected["comparison_label"]):
        row["field_decisions"]["comparison_label"] = "WRONG_COMPARISON_LABEL"
        wrong = True
    if expected.get("unit_dom_path") is not None and binding.get("unit_dom_path") != expected["unit_dom_path"]:
        row["field_decisions"]["unit_scope"] = "WRONG_UNIT_SCOPE"
        wrong = True
    row["known_fields_complete"] = not wrong and not unknown_on_known
    row["all_fields_complete"] = row["known_fields_complete"] and not source_unresolved
    row["wrong_complete_declaration"] = binding.get("full_status") in COMPLETE_DECLARATIONS and not row["all_fields_complete"]
    row["state"] = "ALL_FIELDS_COMPLETE" if row["all_fields_complete"] else (
        "KNOWN_FIELDS_COMPLETE" if row["known_fields_complete"] else "INCOMPLETE_OR_WRONG")
    return row


def evaluate_frozen_tables(reference: dict, parsed_documents: list[dict]) -> tuple[dict, dict]:
    """Return private row review and redacted aggregate, with dynamic denominators."""
    table_reference = reference.get("v24_four_table_reference", reference)
    expected_tables = table_reference.get("expected_tables")
    if not isinstance(expected_tables, list):
        raise ExpandedEvaluationError("expected_tables must be a list")
    predicted_tables = _unique_index(
        [((doc["document_id"], table["dom_path"]), doc, table)
         for doc in parsed_documents for table in doc.get("tables", [])],
        lambda x: x[0], "parsed table")
    _unique_index(expected_tables,
                  lambda x: (x["document_id"], x["table_dom_path"]), "expected table")
    counts = Counter()
    private_tables = []
    public_tables = []
    for exp_table in expected_tables:
        key = exp_table["document_id"], exp_table["table_dom_path"]
        matched = predicted_tables.get(key)
        if matched is None:
            table = None
            document = None
        else:
            _, document, table = matched
            if document.get("source_sha256") != exp_table.get("raw_body_sha256"):
                raise ExpandedEvaluationError("source hash mismatch for expected table")
        expected_cells = exp_table.get("expected_value_cells", [])
        _unique_index(expected_cells, lambda x: x["value_dom_path"], "expected value cell")
        binding_by_path = _unique_index(table.get("bindings", []) if table else [],
                                        lambda x: x["value_cell_dom_path"], "parsed value cell")
        source_cells = {cell["dom_path"]: cell for row in table.get("rows", [])
                        for cell in row.get("cells", [])} if table else {}
        forbidden = set(exp_table.get("forbidden_column_header_target_dom_paths", []))
        reviews = []
        table_counts = Counter()
        witnesses = {w["value_dom_path"] for w in exp_table.get("frozen_witnesses", [])}
        for exp in expected_cells:
            review = score_expected_cell(exp, binding_by_path.get(exp["value_dom_path"]),
                                         forbidden_header_paths=forbidden, table_cells=source_cells)
            reviews.append(review)
            counts["expected_cells"] += 1
            table_counts["expected_cells"] += 1
            counts["matched_cells"] += int(review["matched"])
            table_counts["matched_cells"] += int(review["matched"])
            counts["missed_cells"] += int(not review["matched"])
            table_counts["missed_cells"] += int(not review["matched"])
            counts["known_fields_complete"] += int(review["known_fields_complete"])
            table_counts["known_fields_complete"] += int(review["known_fields_complete"])
            counts["all_fields_complete"] += int(review["all_fields_complete"])
            table_counts["all_fields_complete"] += int(review["all_fields_complete"])
            counts["wrong_complete_declarations"] += int(review["wrong_complete_declaration"])
            table_counts["wrong_complete_declarations"] += int(review["wrong_complete_declaration"])
            if exp["value_dom_path"] in witnesses:
                counts["frozen_witnesses"] += 1
                table_counts["frozen_witnesses"] += 1
                counts["frozen_witnesses_all_fields_complete"] += int(review["all_fields_complete"])
                table_counts["frozen_witnesses_all_fields_complete"] += int(review["all_fields_complete"])
            counts["forbidden_header_links"] += len(review["forbidden_header_paths"])
            counts["extra_wrong_source_paths"] += len(review["wrong_explicit_paths"])
            for field, decision in review["field_decisions"].items():
                counts[f"{field}:{decision}"] += 1
        counts["expected_tables"] += 1
        counts["missing_tables"] += int(table is None)
        extra_paths = set(binding_by_path) - {x["value_dom_path"] for x in expected_cells}
        # Extra parsed cells are not automatically wrong: the reference may
        # review only selected cells in a larger training table.
        counts["unreviewed_extra_cells"] += len(extra_paths)
        private_tables.append({"document_id": key[0], "table_dom_path": key[1],
                               "cell_reviews": reviews, "unreviewed_extra_paths": sorted(extra_paths)})
        public_tables.append({"symbol": exp_table.get("symbol"),
                              "document_id": key[0],
                              "counts": dict(sorted(table_counts.items()))})
    public = {
        "schema": "financial_text_v25_dynamic_table_review_redacted_v1",
        "source_review_role": table_reference.get("review_role", "CODEX_REFERENCE_DRAFT_NOT_INDEPENDENT_GOLD"),
        "training_material_regression_only": True,
        "denominators": {"tables": len(expected_tables),
                         "expected_cells": sum(len(t.get("expected_value_cells", [])) for t in expected_tables),
                         "parsed_tables_all_documents": len(predicted_tables)},
        "counts": dict(sorted(counts.items())),
        "per_referenced_table": public_tables,
        "new_nonreferenced_tables": len(set(predicted_tables) - {
            (t["document_id"], t["table_dom_path"]) for t in expected_tables}),
        "semantic_scope": "ONLY_FROZEN_REFERENCED_CELLS; OTHER_OUTPUT_NOT_REVIEWED",
    }
    return {"schema": "financial_text_v25_dynamic_table_review_private_v1",
            "tables": private_tables, "public_summary": public}, public


def assess_pack_closure(pack: dict) -> dict:
    """Check exact serialized input and selected group references, not side JSON alone."""
    visible = pack.get("model_input", pack.get("model_visible_text"))
    if not isinstance(visible, str):
        raise ExpandedEvaluationError("pack lacks exact model-visible text")
    budget = pack.get("max_characters", pack.get("budget_characters", 20_000))
    groups = pack.get("group_inventory", [])
    _unique_index(groups, lambda g: g["group_id"], "evidence group")
    if len(visible) > budget:
        raise ExpandedEvaluationError("model-visible Unicode character budget exceeded")
    selected = [g for g in groups if g.get("selected")]
    declared = set(pack.get("selected_group_ids", []))
    if declared != {g["group_id"] for g in selected}:
        raise ExpandedEvaluationError("selected group index differs from inventory")
    spans = pack.get("source_spans", [])
    contexts = pack.get("context_dictionary", {})
    by_id = _unique_index(spans, lambda s: s["id"], "model-visible span")
    dangling = []
    missing_groups = []
    malformed_spans = []
    for span in spans:
        start, end = span.get("start"), span.get("end")
        raw_start, raw_end = span.get("raw_start"), span.get("raw_end")
        if (not all(isinstance(x, int) for x in (start, end, raw_start, raw_end))
                or not 0 <= start <= raw_start <= raw_end == end <= len(visible)):
            malformed_spans.append(span.get("id"))
            continue
        line = visible[start:end]
        expected_prefix = {"CONTEXT": "[CTX ", "VALUE": "[VALUE ",
                           "PARAGRAPH": "[PARAGRAPH "}.get(span.get("kind"))
        if (expected_prefix is None or not line.startswith(expected_prefix)
                or f"id={span['id']} " not in line[:line.find("] ") + 2]
                or hashlib.sha256(visible[raw_start:raw_end].encode("utf-8")).hexdigest()
                != span.get("raw_text_sha256")):
            malformed_spans.append(span.get("id"))
    for g in selected:
        value_span = by_id.get(g["group_id"])
        if value_span is None or value_span.get("kind") != "VALUE":
            missing_groups.append(g["group_id"])
            continue
        value_line = visible[value_span["start"]:value_span["end"]]
        value_header = value_line[:value_line.find("] ") + 2]
        for field, refs in g.get("relation_refs", {}).items():
            if isinstance(refs, str):
                refs = [refs]
            for ref in refs:
                context_span = by_id.get(ref)
                context = contexts.get(ref)
                exact_ref = isinstance(ref, str) and bool(re.search(
                    rf"(?<![A-Za-z0-9]){re.escape(ref)}(?![A-Za-z0-9])", value_header))
                if (not exact_ref or context_span is None
                        or context_span.get("kind") != "CONTEXT"
                        or context_span.get("start", len(visible)) >= value_span["start"]
                        or not isinstance(context, dict)
                        or context.get("document_id") != g.get("document_id")
                        or context.get("dom_path") != context_span.get("dom_path")
                        or context.get("source_sha256") != context_span.get("source_sha256")):
                    dangling.append({"group_id": g["group_id"], "field": field,
                                     "reference": ref})
    return {"budget_characters": budget, "actual_characters": len(visible),
            "selected_groups": len(selected), "missing_group_identifiers": missing_groups,
            "dangling_references": dangling,
            "malformed_spans": malformed_spans,
            "closure_pass": not missing_groups and not dangling and not malformed_spans}


_NUMBER = re.compile(r"(?<![A-Za-z0-9])(?:\(?\s*[+\-−–]?\s*[$€£]?\s*\d[\d,]*(?:\.\d+)?\s*\)?)(?![A-Za-z0-9])")


def _dom(path: str | None) -> str:
    """Normalize frozen slash-index paths and v2.4 CSS paths for comparison."""
    if not path:
        return ""
    if ":nth-of-type(" in path:
        return " > ".join(part.strip().lower() for part in path.split(" > "))
    parts = []
    for part in path.split("/"):
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\[(\d+)\]", part)
        if not match:
            return ""
        parts.append(f"{match.group(1).lower()}:nth-of-type({match.group(2)})")
    return " > ".join(parts)


def _contains(ancestor: str | None, descendant: str | None) -> bool:
    a, d = _dom(ancestor), _dom(descendant)
    return bool(a and d) and (a == d or d.startswith(a + " > "))


def _numbers(value: str | None) -> tuple[str, ...]:
    out = []
    source = value or ""
    for match in _NUMBER.finditer(source):
        token = match.group().strip().replace("−", "-").replace("–", "-")
        previous = source[:match.start()].rstrip()[-1:]
        range_separator = bool(previous) and previous in "0123456789%)]"
        negative = (token.startswith("(") and token.endswith(")")) or (
            token.startswith("-") and not range_separator)
        token = token.strip("() ").replace("$", "").replace("€", "").replace("£", "")
        token = token.replace("+", "").replace("-", "").replace(",", "").strip()
        try:
            number = Decimal(token)
        except InvalidOperation:
            continue
        out.append(str((-number if negative else number).normalize()))
    return tuple(out)


def _candidate_match(candidate: dict, parsed_documents: list[dict]) -> tuple[str, dict | None]:
    """Match by source SHA, DOM value locator, amount and metric context.

    A row-level locator may cover several cells. Multiple surviving anchors
    are ambiguous. A matching amount alone is never enough.
    """
    review = candidate.get("review_v4", candidate)
    evidence = review.get("field_evidence", {})
    value_locators = [e.get("dom_path") for e in evidence.get("value", []) if e.get("dom_path")]
    metric_evidence = [e for e in evidence.get("metric", []) if e.get("dom_path")]
    if not value_locators or not metric_evidence:
        return "REFERENCE_LOCATOR_INSUFFICIENT", None
    if all(re.search(r"div:nth-of-type\(\d+\)$", _dom(p)) for p in value_locators):
        return "PARAGRAPH_SEPARATE", None
    value = str(review.get("final", {}).get("value_text") or "")
    wanted = _numbers(value)
    if not wanted:
        return "REFERENCE_VALUE_UNPARSEABLE", None
    matches = []
    for doc in parsed_documents:
        if doc.get("document_id") != candidate.get("source_document_id"):
            continue
        if doc.get("source_sha256") != candidate.get("source_document_sha256"):
            raise ExpandedEvaluationError("candidate/parser source SHA differs")
        for table in doc.get("tables", []):
            for binding in table.get("bindings", []):
                value_path = binding.get("value_cell_dom_path")
                if not any(_contains(locator, value_path) for locator in value_locators):
                    continue
                if _numbers(str(binding.get("value_text") or "")) != wanted:
                    continue
                row_sources = _paths(_component(binding, "row_metric"))
                metric_text = str(binding.get("row_metric_text") or "")
                metric_ok = any(
                    any(_contains(e.get("dom_path"), p) or _contains(p, e.get("dom_path"))
                        for p in row_sources)
                    and (metric_text.casefold() in str(e.get("rendered_visible_text", "")).casefold()
                         or str(e.get("rendered_visible_text", "")).casefold() in metric_text.casefold())
                    for e in metric_evidence if metric_text and e.get("rendered_visible_text")
                )
                if not metric_ok:
                    continue
                matches.append({"document_id": doc["document_id"], "source_sha256": doc["source_sha256"],
                                "table_dom_path": table["dom_path"], "value_cell_dom_path": value_path,
                                "binding": binding})
    if len(matches) == 1:
        return "UNIQUE_SOURCE_ANCHOR", matches[0]
    if len(matches) > 1:
        return "AMBIGUOUS_SOURCE_ANCHOR", None
    return "NO_SOURCE_ANCHOR", None


def evaluate_candidates(reference: dict, parsed_documents: list[dict],
                        packaged_events: list[dict] | None = None,
                        source_audit: dict[str, dict] | None = None) -> tuple[dict, dict]:
    """Audit 71 frozen candidates without promoting mechanical checks to facts.

    The input denominator comes from `reference.candidates`. A same-team source
    audit can add a later `COMPLETE`/`ERROR` decision tied to the exact parsed
    value cell. Without that audit, semantic status remains NOT_REVIEWED.
    """
    candidates = reference.get("candidates")
    if not isinstance(candidates, list):
        raise ExpandedEvaluationError("frozen candidates list absent")
    _unique_index(candidates, lambda c: c["candidate_id"], "candidate ID")
    source_audit = source_audit or {}
    packs = packaged_events or []
    pack_by_event = _unique_index(packs, lambda p: p["event_group_id"], "pack event")
    pack_checks = {eid: assess_pack_closure(pack) for eid, pack in pack_by_event.items()}
    records = []
    counts = Counter()
    for candidate in candidates:
        cid = candidate["candidate_id"]
        source_status = candidate.get("review_status", candidate.get("status"))
        if source_status not in {"CODEX_REVIEW_SOURCE_SUPPORTED", "CODEX_REVIEW_AMBIGUOUS"}:
            raise ExpandedEvaluationError(f"unexpected source review status for {cid}")
        match_state, anchor = _candidate_match(candidate, parsed_documents)
        pack = pack_by_event.get(candidate.get("event_id"))
        group = None
        paragraph_group = None
        if pack and anchor:
            group_matches = [g for g in pack.get("group_inventory", [])
                             if g.get("document_id") == anchor["document_id"]
                             and g.get("table_dom_path") == anchor["table_dom_path"]
                             and g.get("value_cell_dom_path") == anchor["value_cell_dom_path"]]
            if len(group_matches) > 1:
                raise ExpandedEvaluationError("multiple pack groups for one parsed value cell")
            group = group_matches[0] if group_matches else None
        if pack and match_state == "PARAGRAPH_SEPARATE":
            review = candidate.get("review_v4", candidate)
            value_locators = [e.get("dom_path") for e in review.get("field_evidence", {}).get("value", [])]
            paragraph_matches = [g for g in pack.get("paragraph_group_inventory", [])
                                 if g.get("document_id") == candidate.get("source_document_id")
                                 and any(_contains(loc, g.get("dom_path"))
                                         or _contains(g.get("dom_path"), loc)
                                         for loc in value_locators)]
            if len(paragraph_matches) == 1:
                paragraph_group = paragraph_matches[0]
        parsed_status = "NOT_LOCATED"
        if match_state == "PARAGRAPH_SEPARATE":
            paragraph_inventoried = any(
                doc.get("document_id") == candidate.get("source_document_id")
                and doc.get("source_sha256") == candidate.get("source_document_sha256")
                and any(_contains(e.get("dom_path"), p.get("dom_path"))
                        or _contains(p.get("dom_path"), e.get("dom_path"))
                        for e in candidate.get("review_v4", candidate).get("field_evidence", {}).get("value", [])
                        for p in doc.get("paragraph_inventory", []))
                for doc in parsed_documents)
            parsed_status = ("PARAGRAPH_INVENTORIED_UNSTRUCTURED" if paragraph_inventoried
                             else "PARAGRAPH_SOURCE_LOCATOR_NOT_INVENTORIED")
        if anchor:
            binding = anchor["binding"]
            relations = [_component(binding, f) for f in RELATION_FIELDS]
            if any(r["status"] == "CONFLICT" for r in relations):
                parsed_status = "CONFLICT"
            elif any(r["status"] == "UNKNOWN" for r in relations):
                parsed_status = "RELATION_UNKNOWN"
            elif binding.get("full_status") == "MECHANICALLY_COMPLETE":
                parsed_status = "MECHANICALLY_COMPLETE_PENDING_SOURCE_REVIEW"
            else:
                parsed_status = "PARTIAL_OR_INAPPLICABLE"
        audited = source_audit.get(cid)
        semantic_status = "NOT_REVIEWED"
        if audited is not None:
            if anchor is None:
                raise ExpandedEvaluationError("source audit claims a missing source anchor")
            audited_anchor = (audited.get("document_id"), audited.get("table_dom_path"),
                              audited.get("value_cell_dom_path"))
            actual_anchor = (anchor["document_id"], anchor["table_dom_path"],
                             anchor["value_cell_dom_path"])
            if audited_anchor != actual_anchor:
                raise ExpandedEvaluationError("source audit anchor differs from parser anchor")
            semantic_status = audited.get("decision", "NOT_REVIEWED")
            if semantic_status not in {"COMPLETE", "ERROR", "UNKNOWN", "CONFLICT", "NOT_REVIEWED"}:
                raise ExpandedEvaluationError("invalid source audit decision")
            if semantic_status == "COMPLETE":
                if source_status != "CODEX_REVIEW_SOURCE_SUPPORTED":
                    raise ExpandedEvaluationError("ambiguous frozen source cannot be promoted by parser audit")
                if parsed_status != "MECHANICALLY_COMPLETE_PENDING_SOURCE_REVIEW":
                    raise ExpandedEvaluationError("semantic COMPLETE contradicts parsed relations")
                if (audited.get("source_sha256") != anchor["source_sha256"]
                        or audited.get("review_role") not in {"CODEX_REVIEW", "INDEPENDENT_HUMAN_REVIEW"}):
                    raise ExpandedEvaluationError("semantic COMPLETE lacks source SHA or review role")
                field_review = audited.get("field_review")
                if (not isinstance(field_review, dict)
                        or {"value", *RELATION_FIELDS} - set(field_review)
                        or any(field_review[f] not in {"CORRECT", "CORRECT_NOT_APPLICABLE"}
                               for f in ("value", *RELATION_FIELDS))):
                    raise ExpandedEvaluationError("semantic COMPLETE lacks all field source reviews")
        pack_status = "NOT_PACKED"
        if group:
            if group.get("selected") and pack_checks[candidate["event_id"]]["closure_pass"]:
                pack_status = "VISIBLE_CLOSED_PENDING_SEMANTIC_REVIEW"
            elif group.get("selected"):
                pack_status = "VISIBLE_DANGLING_REFERENCE"
            else:
                pack_status = "OMITTED_" + str(group.get("omission_reason") or "UNKNOWN")
        elif paragraph_group:
            pack_status = ("PARAGRAPH_PACKED_DIAGNOSTIC" if paragraph_group.get("status") == "PACKED_DIAGNOSTIC_PARAGRAPH"
                           else "PARAGRAPH_OMITTED_" + str(paragraph_group.get("omission_reason") or "UNKNOWN"))
        elif match_state == "PARAGRAPH_SEPARATE":
            pack_status = "PARAGRAPH_NOT_PACKED_OR_UNMATCHED"
        if semantic_status == "COMPLETE" and pack_status == "VISIBLE_CLOSED_PENDING_SEMANTIC_REVIEW":
            pack_status = "SOURCE_REVIEWED_VISIBLE_COMPLETE"
        row = {"candidate_id": cid, "event_id": candidate.get("event_id"),
               "source_review": source_status, "match_state": match_state,
               "parsed_status": parsed_status, "source_binding_status": semantic_status,
               "pack_status": pack_status,
               "old_fragment_complete": bool(candidate.get("old_fragment_complete")),
               "anchor": None if anchor is None else {k: anchor[k] for k in
                   ("document_id", "table_dom_path", "value_cell_dom_path")}}
        records.append(row)
        counts["candidates"] += 1
        counts["source_supported"] += int(source_status == "CODEX_REVIEW_SOURCE_SUPPORTED")
        counts["source_ambiguous"] += int(source_status == "CODEX_REVIEW_AMBIGUOUS")
        counts[f"match:{match_state}"] += 1
        counts[f"parsed:{parsed_status}"] += 1
        counts[f"semantic:{semantic_status}"] += 1
        counts[f"pack:{pack_status}"] += 1
        counts["old_fragment_complete"] += int(bool(candidate.get("old_fragment_complete")))
    public = {"schema": "financial_text_v25_candidate_review_redacted_v1",
              "source_review_role": "CODEX_REVIEW_NOT_INDEPENDENT_GOLD",
              "training_material_regression_only": True,
              "denominators": {"candidates": len(candidates),
                               "source_supported": counts["source_supported"],
                               "source_ambiguous": counts["source_ambiguous"]},
              "counts": dict(sorted(counts.items())),
              "candidate_statuses": [{k: row[k] for k in
                                     ("candidate_id", "source_review", "match_state", "parsed_status",
                                      "source_binding_status", "pack_status", "old_fragment_complete")}
                                     for row in records],
              "pack_closure": pack_checks,
              "semantic_scope": "COMPLETE_REQUIRES_POST_PARSE_EXACT_ANCHOR_SOURCE_AUDIT"}
    return {"schema": "financial_text_v25_candidate_review_private_v1",
            "rows": records, "public_summary": public}, public
