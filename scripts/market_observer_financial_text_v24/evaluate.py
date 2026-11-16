"""Source-semantic small regression for the frozen v2.4 table kernel.

This evaluator reads private training-period reference rows. It does not feed
them to the production parser, build model inputs, fit models, or score returns.
The four selected tables are a known training regression set, not an independent
test or a human gold standard. All per-cell comparisons stay in ignored paths.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = ROOT / "artifacts/market_observer_financial_text_v24_20261116"
PUBLIC_DIR = ROOT / "results/market_observer_financial_text_v24_20261116"
SELECTION = PRIVATE_DIR / "selection_manifest.private.json"
REFERENCE = PRIVATE_DIR / "expected_relations.private.json"
DEFAULT_FREEZE = PUBLIC_DIR / "freeze_frozen_v1_redacted.json"
DEFINITION_VERSION = "v24_small_source_semantic_audit_v1"
TRAIN_START = date(2025, 4, 1)
TRAIN_END = date(2026, 3, 31)
VALID_STATES = {"EXPLICIT", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE"}
UNIT_FIELDS = ("currency", "scale", "measurement_kind")
RELATION_FIELDS = ("row_metric", "column_header", "period", "currency", "scale",
                   "measurement_kind", "basis", "footnote")
HEADER_ROLES = {"BASIS_HEADER", "UNIT_AND_COLUMN_HEADER", "UNIT_AND_BASIS_HEADER",
                "PERIOD_HEADER", "COLUMN_HEADER", "HEADER", "REPEATED_HEADER"}


class EvaluationContractError(ValueError):
    """Frozen input, scorer output, or source-provenance contract failed."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(value: Any) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split()).casefold()


def _paths(component: dict | None) -> list[str]:
    if not isinstance(component, dict):
        return []
    paths = component.get("source_dom_paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p for p in paths):
        raise EvaluationContractError("relation source_dom_paths malformed")
    if len(paths) != len(set(paths)):
        raise EvaluationContractError("duplicate relation source path")
    return paths


def _status(component: dict | None) -> str:
    if not isinstance(component, dict):
        raise EvaluationContractError("missing relation component")
    status = component.get("status")
    if status not in VALID_STATES:
        raise EvaluationContractError(f"invalid relation state: {status}")
    if status == "EXPLICIT" and not _paths(component):
        raise EvaluationContractError("EXPLICIT relation lacks source path")
    return status


def _component(binding: dict, field: str) -> dict:
    if field in UNIT_FIELDS:
        unit = binding.get("unit")
        component = unit.get(field) if isinstance(unit, dict) else None
    elif field == "basis":
        component = binding.get("basis_relation")
    else:
        component = binding.get(field)
    if not isinstance(component, dict):
        raise EvaluationContractError(f"binding lacks {field} relation")
    _status(component)
    return component


def _value(binding: dict, field: str) -> Any:
    aliases = {"row_metric": "row_metric_text", "column_header": "column_header_dom_path",
               "period": "period_text", "basis": "basis"}
    flat = aliases.get(field, field)
    if flat in binding:
        return binding[flat]
    return _component(binding, field).get("value", _component(binding, field).get("text"))


def _field_result(*, field: str, expected_value: Any, expected_path: str | None,
                  binding: dict, ambiguity: bool = False, null_is_na: bool = False) -> dict:
    component = _component(binding, field)
    status = _status(component)
    observed_value = _value(binding, field)
    observed_paths = _paths(component)
    path_correct = expected_path is None or expected_path in observed_paths
    value_correct = normalize(observed_value) == normalize(expected_value)
    if expected_value is None:
        if observed_value is not None or status == "EXPLICIT":
            decision = "WRONG_EXPLICIT"
        elif status == "CONFLICT":
            decision = "CONFLICT_ABSTENTION" if ambiguity else "UNEXPECTED_CONFLICT"
        elif status == "UNKNOWN":
            decision = "CORRECT_ABSTENTION" if ambiguity else (
                "WRONG_UNKNOWN_WHEN_NA" if null_is_na else "CORRECT_NULL")
        elif status == "NOT_APPLICABLE":
            decision = "WRONG_NA_ON_UNKNOWN" if ambiguity else (
                "CORRECT_NOT_APPLICABLE" if null_is_na else "CORRECT_NULL")
        else:
            decision = "WRONG_EXPLICIT"
    elif status == "UNKNOWN":
        decision = "UNKNOWN_ON_KNOWN"
    elif status == "CONFLICT":
        decision = "CONFLICT_ON_KNOWN"
    elif status == "NOT_APPLICABLE":
        decision = "MISSED_KNOWN"
    elif value_correct and path_correct:
        decision = "CORRECT_EXPLICIT"
    else:
        decision = "WRONG_EXPLICIT"
    return {"field": field, "decision": decision, "expected_value": expected_value,
            "predicted_value": observed_value, "expected_source_path": expected_path,
            "predicted_source_paths": observed_paths, "predicted_status": status,
            "reason": component.get("reason"), "scope_id": component.get("scope_id"),
            "value_correct": value_correct, "path_correct": path_correct}


def _explicit_edge_audit(expected: dict, binding: dict,
                         table_cells: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Review every emitted EXPLICIT path against source-frozen semantic anchors.

    A token/cell merely being in the same table is never sufficient. Paths not
    justified by the frozen reference or direct adjacent marker geometry are
    retained as UNVERIFIED for source review, and fail the small-scope gate.
    """
    value_path = expected["value_dom_path"]
    value_cell = table_cells.get(value_path, {})
    adjacent_currency = set()
    adjacent_percent = set()
    for path, cell in table_cells.items():
        if cell.get("row_start") != value_cell.get("row_start"):
            continue
        roles = set(cell.get("roles", []))
        if (cell.get("column_end") == value_cell.get("column_start")
                and "CURRENCY_MARKER" in roles and normalize(cell.get("text")) == "$"):
            adjacent_currency.add(path)
        if (cell.get("column_start") == value_cell.get("column_end")
                and "PERCENT_MARKER" in roles and normalize(cell.get("text")) == "%"):
            adjacent_percent.add(path)
    unit_path = expected["unit_dom_path"]
    header_path = expected["column_header_dom_path"]
    allowed = {
        "row_metric": {expected["row_label_dom_path"]},
        "column_header": {header_path},
        "period": {header_path} if expected.get("period_text") is not None else set(),
        "currency": ({value_path} if "$" in expected["value_text"] else set()) | adjacent_currency,
        "scale": {unit_path} if expected.get("scale") is not None else set(),
        "measurement_kind": {expected["row_label_dom_path"], value_path, unit_path}
        | adjacent_percent
        | ({header_path} if expected.get("comparison_label") else set()),
        "basis": {expected["basis_dom_path"]},
        "footnote": set(),
    }
    edges = []
    issues = []
    for field in RELATION_FIELDS:
        component = _component(binding, field)
        if _status(component) != "EXPLICIT":
            continue
        for path in _paths(component):
            reviewed = path in allowed[field]
            edges.append({"field": field, "source_dom_path": path,
                          "decision": "FROZEN_SOURCE_SUPPORTED" if reviewed else "UNVERIFIED"})
            if not reviewed:
                issues.append(f"{field}:{path}")
    if expected.get("comparison_label") is not None:
        kind = _component(binding, "measurement_kind")
        if _status(kind) == "EXPLICIT" and header_path not in _paths(kind):
            issues.append("measurement_kind:comparison_header_not_cited")
    return edges, issues


def score_cell(expected: dict, binding: dict | None, *, forbidden_header_paths: set[str],
               table_cells: dict[str, dict] | None = None) -> dict:
    """Compare one pre-frozen source cell; never match by amount alone."""
    ledger = {"value_dom_path": expected["value_dom_path"], "expected_value_text": expected["value_text"],
              "predicted_value_text": None if binding is None else binding.get("value_text"),
              "fields": {}, "forbidden_header_links": [], "explicit_edge_review": [],
              "unverified_explicit_paths": [], "unverified_explicit_edges": [],
              "known_fields_complete": False, "fully_bound": False,
              "missed": binding is None, "wrong_complete_declaration": False}
    if binding is None:
        return ledger
    if binding.get("value_cell_dom_path") != expected["value_dom_path"]:
        raise EvaluationContractError("scorer matched a different source cell")
    table_cells = table_cells or {}
    ledger["anchor_exact"] = normalize(binding.get("value_text")) == normalize(expected["value_text"])
    ledger["geometry_exact"] = (
        binding.get("row_index") == expected["row_index_zero_based"]
        and [binding.get("column_start"), binding.get("column_end")] == expected["logical_column_span"]
    )
    if not ledger["anchor_exact"] or not ledger["geometry_exact"]:
        ledger["anchor_decision"] = "WRONG_ANCHOR_OR_GRID"
    else:
        ledger["anchor_decision"] = "CORRECT_EXACT_ANCHOR"

    fields = ledger["fields"]
    fields["row_metric"] = _field_result(
        field="row_metric", expected_value=expected["row_metric"],
        expected_path=expected["row_label_dom_path"], binding=binding)
    fields["column_header"] = _field_result(
        field="column_header", expected_value=expected["column_header_dom_path"],
        expected_path=expected["column_header_dom_path"], binding=binding)
    if expected.get("period_text") is None and expected.get("comparison_label") is not None:
        fields["period"] = _field_result(field="period", expected_value=None,
                                         expected_path=None, binding=binding,
                                         null_is_na=True)
        fields["comparison_label"] = {
            "field": "comparison_label",
            "decision": ("CORRECT_EXPLICIT" if normalize(binding.get("comparison_label"))
                         == normalize(expected["comparison_label"]) else "WRONG_EXPLICIT"),
            "expected_value": expected["comparison_label"],
            "predicted_value": binding.get("comparison_label"),
        }
    else:
        fields["period"] = _field_result(
            field="period", expected_value=expected.get("period_text"),
            expected_path=expected["column_header_dom_path"], binding=binding)

    source_unknown_currency = (expected.get("currency") is None
                               and expected.get("measurement_kind") in {"amount", "per_share_amount"})
    fields["currency"] = _field_result(
        field="currency", expected_value=expected.get("currency"),
        expected_path=None, binding=binding, ambiguity=source_unknown_currency,
        null_is_na=not source_unknown_currency and expected.get("currency") is None)
    fields["scale"] = _field_result(
        field="scale", expected_value=expected.get("scale"),
        expected_path=expected["unit_dom_path"] if expected.get("scale") is not None else None,
        binding=binding, null_is_na=expected.get("scale") is None)
    fields["measurement_kind"] = _field_result(
        field="measurement_kind", expected_value=expected["measurement_kind"],
        expected_path=None, binding=binding)
    fields["basis"] = _field_result(
        field="basis", expected_value=expected["basis_text"],
        expected_path=expected["basis_dom_path"], binding=binding)

    footnote = _component(binding, "footnote")
    foot_status = _status(footnote)
    mu_ambiguous_footnote = "footnote_link_status" in expected
    if mu_ambiguous_footnote:
        marker = expected.get("footnote_marker")
        candidate_paths = {expected["value_dom_path"], expected["row_label_dom_path"],
                           expected["column_header_dom_path"], expected["basis_dom_path"]}
        marker_paths = _paths(footnote)
        valid_markers = bool(marker_paths) and all(
            path in candidate_paths and marker is not None
            and marker in table_cells.get(path, {}).get("text", "")
            for path in marker_paths)
        foot_decision = ("CORRECT_ABSTENTION" if foot_status == "UNKNOWN" and valid_markers
                         else "CONFLICT_ABSTENTION" if foot_status == "CONFLICT" and valid_markers
                         else "WRONG_FOOTNOTE_MARKER_OR_LINK")
    else:
        foot_decision = ("CORRECT_NOT_APPLICABLE" if foot_status == "NOT_APPLICABLE"
                         and not _paths(footnote) else "WRONG_EXPLICIT")
    fields["footnote"] = {"field": "footnote", "decision": foot_decision,
                          "expected_value": expected.get("footnote_link_status", expected.get("footnote_status")),
                          "predicted_value": foot_status,
                          "expected_source_path": expected.get("footnote_definition_dom_path"),
                          "predicted_source_paths": _paths(footnote),
                          "predicted_status": foot_status, "reason": footnote.get("reason")}

    column_paths = _paths(_component(binding, "column_header"))
    ledger["forbidden_header_links"] = sorted(set(column_paths) & forbidden_header_paths)
    expected_header = expected["column_header_dom_path"]
    ledger["unverified_explicit_paths"] = sorted(set(column_paths) - {expected_header}
                                                   - forbidden_header_paths)
    edges, issues = _explicit_edge_audit(expected, binding, table_cells)
    ledger["explicit_edge_review"] = edges
    ledger["unverified_explicit_edges"] = issues
    if binding.get("unit_dom_path") != expected["unit_dom_path"]:
        ledger["unit_scope_decision"] = "WRONG_UNIT_SCOPE"
    else:
        ledger["unit_scope_decision"] = "CORRECT_UNIT_SCOPE"
    wrong_field = any(f["decision"].startswith("WRONG") or f["decision"] in {"UNEXPECTED_CONFLICT", "MISSED_KNOWN"}
                      for f in fields.values())
    unknown_known = any(f["decision"] in {"UNKNOWN_ON_KNOWN", "CONFLICT_ON_KNOWN"}
                        for f in fields.values())
    unresolved_source = any(f["decision"] in {"CORRECT_ABSTENTION", "CONFLICT_ABSTENTION"}
                            for f in fields.values())
    ledger["known_fields_complete"] = bool(
        ledger["anchor_exact"] and ledger["geometry_exact"]
        and not wrong_field and not unknown_known
        and not ledger["forbidden_header_links"] and not ledger["unverified_explicit_paths"]
        and not ledger["unverified_explicit_edges"]
        and ledger["unit_scope_decision"] == "CORRECT_UNIT_SCOPE"
    )
    ledger["fully_bound"] = ledger["known_fields_complete"] and not unresolved_source
    ledger["wrong_complete_declaration"] = bool(
        binding.get("full_status") in {"EXPLICIT", "MECHANICALLY_COMPLETE", "SOURCE_SUPPORTED"}
        and not ledger["fully_bound"])
    return ledger


def score_reference(reference: dict, parsed_documents: list[dict]) -> tuple[dict, dict]:
    """Score all frozen source cells, row roles, and emitted explicit relations."""
    expected_tables = reference.get("expected_tables")
    if not isinstance(expected_tables, list) or len(expected_tables) != 4:
        raise EvaluationContractError("frozen four-table reference missing")
    predicted_tables = {}
    for document in parsed_documents:
        for table in document.get("tables", []):
            key = (document["document_id"], table["dom_path"])
            if key in predicted_tables:
                raise EvaluationContractError("duplicate parsed table")
            predicted_tables[key] = (document, table)
    if len(predicted_tables) != 4:
        raise EvaluationContractError("parsed table denominator differs")
    private_tables = []
    public_tables = []
    totals = Counter()
    witness_paths = set()
    for expected_table in expected_tables:
        key = (expected_table["document_id"], expected_table["table_dom_path"])
        if key not in predicted_tables:
            raise EvaluationContractError("frozen table absent from parser output")
        document, table = predicted_tables[key]
        if document.get("source_sha256") != expected_table["raw_body_sha256"]:
            raise EvaluationContractError("parser source hash differs from reference")
        expected_cells = expected_table["expected_value_cells"]
        expected_by_path = {c["value_dom_path"]: c for c in expected_cells}
        if len(expected_by_path) != len(expected_cells):
            raise EvaluationContractError("duplicate expected source cell")
        bindings = table.get("bindings", [])
        by_path = {b["value_cell_dom_path"]: b for b in bindings}
        if len(by_path) != len(bindings):
            raise EvaluationContractError("duplicate parser value cell")
        rows = {str(r["row_index"]): r for r in table.get("rows", [])}
        if len(rows) != len(table.get("rows", [])):
            raise EvaluationContractError("duplicate parser row")
        table_cells = {cell["dom_path"]: cell for row in table.get("rows", [])
                       for cell in row.get("cells", [])}
        if len(table_cells) != sum(len(r.get("cells", [])) for r in table.get("rows", [])):
            raise EvaluationContractError("duplicate parser physical cell")
        role_rows = []
        for row_index, role in expected_table["row_roles_zero_based"].items():
            predicted_role = rows.get(row_index, {}).get("role", "MISSED")
            false_header = role == "DATA" and predicted_role in HEADER_ROLES
            role_rows.append({"row_index": int(row_index), "expected_role": role,
                              "predicted_role": predicted_role, "false_header": false_header})
            totals["false_header_rows"] += int(false_header)
            totals["missed_rows"] += int(predicted_role == "MISSED")
            totals["wrong_row_roles"] += int(predicted_role != role)

        forbidden = set(expected_table.get("forbidden_column_header_target_dom_paths", []))
        cell_rows = []
        for exp in expected_cells:
            row = score_cell(exp, by_path.get(exp["value_dom_path"]),
                             forbidden_header_paths=forbidden, table_cells=table_cells)
            cell_rows.append(row)
            totals["expected_numeric_cells"] += 1
            totals["missed_numeric_cells"] += int(row["missed"])
            totals["matched_numeric_cells"] += int(not row["missed"])
            totals["known_fields_complete"] += int(row["known_fields_complete"])
            totals["fully_bound"] += int(row["fully_bound"])
            totals["wrong_complete_declarations"] += int(row["wrong_complete_declaration"])
            totals["false_header_links"] += len(row["forbidden_header_links"])
            totals["unverified_explicit_links"] += len(row["unverified_explicit_paths"])
            totals["unverified_explicit_edges"] += len(row["unverified_explicit_edges"])
            totals["wrong_unit_scope_cells"] += int(row.get("unit_scope_decision") == "WRONG_UNIT_SCOPE")
            for field, item in row["fields"].items():
                totals[f"{field}:{item['decision']}"] += 1
            if exp["value_dom_path"] in {w["value_dom_path"] for w in expected_table.get("frozen_witnesses", [])}:
                witness_paths.add((key, exp["value_dom_path"]))
                totals["nvda_witnesses"] += 1
                totals["nvda_witness_fully_bound"] += int(row["fully_bound"])
        extra = sorted(set(by_path) - set(expected_by_path))
        totals["unexpected_numeric_cells"] += len(extra)
        unverified_extra = []
        for path in extra:
            b = by_path[path]
            for field in RELATION_FIELDS:
                c = _component(b, field)
                if _status(c) == "EXPLICIT":
                    unverified_extra.append({"cell_dom_path": path, "field": field,
                                             "source_dom_paths": _paths(c)})
                    totals["unverified_explicit_relations"] += 1
        table_counts = Counter()
        for row in cell_rows:
            table_counts["expected_numeric_cells"] += 1
            table_counts["missed_numeric_cells"] += int(row["missed"])
            table_counts["known_fields_complete"] += int(row["known_fields_complete"])
            table_counts["fully_bound"] += int(row["fully_bound"])
            table_counts["false_header_links"] += len(row["forbidden_header_links"])
            table_counts["unverified_explicit_edges"] += len(row["unverified_explicit_edges"])
            table_counts["wrong_unit_scope_cells"] += int(row.get("unit_scope_decision") == "WRONG_UNIT_SCOPE")
            for field, item in row["fields"].items():
                table_counts[f"{field}:{item['decision']}"] += 1
        table_counts["false_header_rows"] = sum(int(r["false_header"]) for r in role_rows)
        table_counts["unexpected_numeric_cells"] = len(extra)
        table_counts["unverified_explicit_relations"] = len(unverified_extra)
        private_tables.append({"symbol": expected_table["symbol"], "announcement_date": expected_table["announcement_date"],
                               "document_id": key[0], "table_dom_path": key[1],
                               "source_sha256": expected_table["raw_body_sha256"],
                               "row_role_review": role_rows, "cell_review": cell_rows,
                               "unexpected_numeric_cell_paths": extra,
                               "unverified_extra_explicit": unverified_extra})
        public_tables.append({"symbol": expected_table["symbol"], "announcement_date": expected_table["announcement_date"],
                              "counts": dict(sorted(table_counts.items()))})
    if totals["expected_numeric_cells"] != 138 or totals["nvda_witnesses"] != 6:
        raise EvaluationContractError("frozen 138/6 denominators differ")
    totals["wrong_period_links"] = totals["period:WRONG_EXPLICIT"]
    totals["wrong_currency_scope"] = totals["currency:WRONG_EXPLICIT"]
    totals["wrong_scale_scope"] = totals["scale:WRONG_EXPLICIT"]
    totals["wrong_measurement_kind"] = totals["measurement_kind:WRONG_EXPLICIT"]
    totals["unknown_fields_on_known_source"] = sum(
        value for key, value in totals.items() if key.endswith(":UNKNOWN_ON_KNOWN"))
    totals["conflict_fields_on_known_source"] = sum(
        value for key, value in totals.items() if key.endswith(":CONFLICT_ON_KNOWN"))
    totals["correct_abstentions"] = sum(
        value for key, value in totals.items() if key.endswith(":CORRECT_ABSTENTION"))
    totals["wrong_explicit_fields"] = sum(
        value for key, value in totals.items() if key.endswith(":WRONG_EXPLICIT"))
    totals["wrong_field_decisions"] = sum(
        value for key, value in totals.items() if ":WRONG" in key)
    totals["wrong_abstention_or_applicability"] = sum(
        value for key, value in totals.items()
        if key.endswith(":WRONG_UNKNOWN_WHEN_NA") or key.endswith(":WRONG_NA_ON_UNKNOWN")
        or key.endswith(":UNEXPECTED_CONFLICT"))
    mu = next(t for t in public_tables if t["symbol"] == "MU")
    totals["mu_known_fields_complete"] = mu["counts"].get("known_fields_complete", 0)
    totals["mu_fully_bound"] = mu["counts"].get("fully_bound", 0)
    totals["mu_correct_abstentions"] = sum(v for k, v in mu["counts"].items()
                                           if k.endswith(":CORRECT_ABSTENTION"))
    fail_keys = ("false_header_rows", "false_header_links", "wrong_period_links",
                 "wrong_unit_scope_cells", "wrong_complete_declarations",
                 "wrong_row_roles", "unverified_explicit_links", "unverified_explicit_edges",
                 "unverified_explicit_relations", "unexpected_numeric_cells", "missed_numeric_cells",
                 "wrong_field_decisions",
                 "wrong_abstention_or_applicability", "unknown_fields_on_known_source",
                 "conflict_fields_on_known_source")
    gate = (totals["nvda_witness_fully_bound"] == 6
            and totals["mu_known_fields_complete"] == 48
            and all(totals[key] == 0 for key in fail_keys))
    public = {"schema": "financial_text_v24_small_regression_redacted_v1",
              "definition_version": DEFINITION_VERSION,
              "source_review_role": "CODEX_REFERENCE_DRAFT_NOT_INDEPENDENT_GOLD",
              "training_material_regression_only": True,
              "denominators": {"tables": 4, "expected_numeric_cells": 138, "nvda_witnesses": 6,
                               "mu_numeric_cells": 48},
              "counts": dict(sorted(totals.items())), "per_table": public_tables,
              "small_scope_gate": "PASS" if gate else "FAIL",
              "not_run": ["eight_event_build", "20000_character_adapter", "model_inference",
                          "model_fitting", "return_evaluation", "trading"]}
    private = {"schema": "financial_text_v24_small_regression_private_v1",
               "definition_version": DEFINITION_VERSION, "tables": private_tables,
               "public_summary": public}
    return private, public


def verify_frozen_inputs(freeze: dict, selection: dict, reference: dict,
                         *, root: Path = ROOT) -> dict[str, bytes]:
    """Verify every frozen dependency and every selected saved raw source."""
    if freeze.get("schema") != "financial_text_v24_freeze_v1" or freeze.get("run_id") != "frozen_v1":
        raise EvaluationContractError("freeze identity differs")
    if freeze.get("expected_tables") != 4 or freeze.get("expected_value_cells") != 138:
        raise EvaluationContractError("freeze denominator differs")
    file_map = freeze.get("file_sha256")
    if not isinstance(file_map, dict) or not file_map:
        raise EvaluationContractError("freeze file map missing")
    required = {"scripts/market_observer_financial_text_v24/semantic.py",
                "scripts/market_observer_financial_text_v24/evaluate.py",
                "artifacts/market_observer_financial_text_v24_20261116/selection_manifest.private.json",
                "artifacts/market_observer_financial_text_v24_20261116/expected_relations.private.json"}
    if not required.issubset(file_map):
        raise EvaluationContractError("freeze lacks required dependency")
    data = {}
    for rel, digest in file_map.items():
        posix = PurePosixPath(rel)
        if posix.is_absolute() or ".." in posix.parts or "\\" in rel or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise EvaluationContractError("unsafe frozen file entry")
        path = root.joinpath(*posix.parts)
        if not path.is_file():
            raise EvaluationContractError(f"frozen file missing: {rel}")
        actual = path.read_bytes()
        if sha(actual) != digest:
            raise EvaluationContractError(f"frozen file hash differs: {rel}")
        data[rel] = actual
    selected = selection.get("selected_tables")
    expected = reference.get("expected_tables")
    if not isinstance(selected, list) or len(selected) != 4 or not isinstance(expected, list) or len(expected) != 4:
        raise EvaluationContractError("selected/reference table denominator differs")
    by_key = {(r["document_id"], r["table_dom_path"]): r for r in expected}
    if len(by_key) != 4:
        raise EvaluationContractError("duplicate expected table")
    source_map = freeze.get("source_raw_sha256")
    if not isinstance(source_map, dict) or len(source_map) != 4:
        raise EvaluationContractError("freeze source map differs")
    raw_by_document = {}
    for item in selected:
        key = (item["document_id"], item["table_dom_path"])
        ref = by_key.get(key)
        if ref is None or item["raw_body_sha256"] != ref["raw_body_sha256"]:
            raise EvaluationContractError("selection/reference source differs")
        when = date.fromisoformat(item["announcement_date"])
        if not TRAIN_START <= when <= TRAIN_END or when.isoformat() != ref["announcement_date"]:
            raise EvaluationContractError("source outside frozen training window")
        path = Path(item["raw_body_path_local_only"])
        digest = item["raw_body_sha256"]
        if not path.is_file() or sha(path.read_bytes()) != digest or source_map.get(item["document_id"]) != digest:
            raise EvaluationContractError("saved raw source missing/hash differs")
        raw_by_document[item["document_id"]] = path.read_bytes()
        snippet = item["snippet"]
        snippet_rel = PurePosixPath(snippet["path"])
        if snippet_rel.is_absolute() or ".." in snippet_rel.parts or "\\" in snippet["path"]:
            raise EvaluationContractError("unsafe snippet path")
        snippet_path = root.joinpath(*snippet_rel.parts)
        if not snippet_path.is_file() or sha(snippet_path.read_bytes()) != snippet["sha256"]:
            raise EvaluationContractError("frozen snippet missing/hash differs")
        if file_map.get(snippet["path"]) != snippet["sha256"]:
            raise EvaluationContractError("snippet omitted from frozen file map")
    if sum(len(t["expected_value_cells"]) for t in expected) != 138:
        raise EvaluationContractError("reference cell denominator differs")
    return raw_by_document


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _csv_view(private: dict) -> bytes:
    rows = []
    for table in private["tables"]:
        for cell in table["cell_review"]:
            if cell["missed"]:
                rows.append({"symbol": table["symbol"], "announcement_date": table["announcement_date"],
                             "table_dom_path": table["table_dom_path"], "value_dom_path": cell["value_dom_path"],
                             "field": "VALUE_CELL", "expected": cell["expected_value_text"],
                             "predicted": "", "decision": "MISSED", "status": "MISSED", "reason": ""})
                continue
            for field, item in cell["fields"].items():
                rows.append({"symbol": table["symbol"], "announcement_date": table["announcement_date"],
                             "table_dom_path": table["table_dom_path"], "value_dom_path": cell["value_dom_path"],
                             "field": field, "expected": item.get("expected_value"),
                             "predicted": item.get("predicted_value"), "decision": item["decision"],
                             "status": item.get("predicted_status", ""), "reason": item.get("reason", "")})
    out = io.StringIO(newline="")
    columns = ("symbol", "announcement_date", "table_dom_path", "value_dom_path", "field",
               "expected", "predicted", "decision", "status", "reason")
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def run_frozen(*, freeze_path: Path, run_id: str = "frozen_v1",
               private_dir: Path = PRIVATE_DIR, public_dir: Path = PUBLIC_DIR) -> dict:
    """One append-only scored run after an external code/config/reference freeze."""
    if not re.fullmatch(r"[a-z0-9_]+", run_id) or run_id != "frozen_v1":
        raise EvaluationContractError("unexpected frozen run ID")
    private_path = private_dir / f"evaluation_{run_id}.private.json"
    csv_path = private_dir / f"cell_relations_{run_id}.private.csv"
    public_path = public_dir / f"evaluation_{run_id}_redacted.json"
    if any(p.exists() for p in (private_path, csv_path, public_path)):
        raise FileExistsError("frozen output already exists; preserve prior run")
    if not freeze_path.is_file():
        raise FileNotFoundError(freeze_path)
    freeze_bytes = freeze_path.read_bytes()
    freeze = json.loads(freeze_bytes)
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    raw_by_document = verify_frozen_inputs(freeze, selection, reference)
    from scripts.market_observer_financial_text_v24.semantic import parse_document

    paths_by_document = defaultdict(list)
    meta_by_document = {}
    for item in selection["selected_tables"]:
        did = item["document_id"]
        paths_by_document[did].append(item["table_dom_path"])
        meta_by_document[did] = {"document_id": did, "raw_body_sha256": item["raw_body_sha256"],
                                 "symbol": item["symbol"], "announcement_date": item["announcement_date"]}
    parsed = [parse_document(raw_by_document[did], meta_by_document[did], paths)
              for did, paths in paths_by_document.items()]
    private, public = score_reference(reference, parsed)
    now = datetime.now(timezone.utc)
    common = {"run_id": run_id, "created_at_utc": now.isoformat(),
              "created_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
              "freeze_sha256": sha(freeze_bytes), "reference_sha256": sha(REFERENCE.read_bytes()),
              "selection_sha256": sha(SELECTION.read_bytes()),
              "source_sha256": freeze["source_raw_sha256"],
              "parsed_documents": len(parsed), "production_build_calls": 0,
              "parser_calls": len(parsed), "model_fit_calls": 0, "model_inference_calls": 0}
    private.update(common)
    public.update(common)
    private_data = _canonical(private)
    csv_data = _csv_view(private)
    public["private_per_cell_sha256"] = sha(private_data)
    public["private_csv_sha256"] = sha(csv_data)
    public_data = _canonical(public)
    for path in (private_path, csv_path, public_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    with private_path.open("xb") as handle:
        handle.write(private_data)
    with csv_path.open("xb") as handle:
        handle.write(csv_data)
    with public_path.open("xb") as handle:
        handle.write(public_data)
    return {"public_path": str(public_path), "public_sha256": sha(public_data),
            "private_sha256": sha(private_data), "private_csv_sha256": sha(csv_data),
            "small_scope_gate": public["small_scope_gate"], "counts": public["counts"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="frozen_v1")
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    args = parser.parse_args()
    print(json.dumps(run_frozen(freeze_path=args.freeze, run_id=args.run_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
