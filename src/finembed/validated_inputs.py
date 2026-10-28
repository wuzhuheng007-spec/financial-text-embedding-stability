"""Fail-closed, version-bound inputs for the document research assistant.

The original extract path keeps the existing exact-substring gate. A structured
derivative is verified as a derivative of that extract; it is never labeled a
verbatim SEC passage. This module does not load a model or run an experiment.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any

from .extract import ExtractionError, primary_text
from .qpilot import _primary_source
from .run import sha256
from .sec import raw_filer_cik, raw_header


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SHA = re.compile(r"^[0-9a-f]{64}$")
_STRUCTURED_SECTION = "PART_I_ITEM_2_MDA"

# These hashes are a code-reviewed trust anchor, not values supplied by a spec.
# Adding another accepted input requires an explicit code change and review.
TRUSTED_ACCEPTED_INPUTS = {
    "AMZN": {
        "cik": "0001018724", "accession": "0001018724-25-000036",
        "raw": "834b76e2ff7363205aa7acc8e9219ded61259ecda3debd5b04c79be7388352b5",
        "parent_section": "43015b9006e416f31ff627e52d20bf522e50935c109d269d798641c48c420033",
        "derivative": "2d9a0eed0b191decee43a6fea9e23ce6af601ce34a44e8eb9aa7a622898bc6d7",
        "provenance": "80538642393a5dceb707f7f5c677658e1222fef0bfd8526232dafbce09322645",
        "acceptance": "36d3add2909ae5024801f2c63d8188526c1db581e950ed60668ed62dfffe6237",
        "project_validation": "21edba17da36b9d46956214adcbf4ffa70a03ab4c3eee286bf81b32ee08aebd0",
    },
    "GOOGL": {
        "cik": "0001652044", "accession": "0001652044-25-000043",
        "raw": "e39849eb31baac03f4d0f0ba254735f20ef039635b1812339aa640c0bfb17730",
        "parent_section": "5e75ca737c479302764d35550f581badd5461183479da2665806ce109a924883",
        "derivative": "8d9eb5244c1293e77260a0d8c4031c0d604f1b7294fa88827cb30c63f8c6dc70",
        "provenance": "bece9f71053a583992e6576c051d0e368d6edf3d0fa1938e8f8353b230548f75",
        "acceptance": "44b87d1dca5f1484b5915fbecd9d216e011c324fdd07e5294f027333cdfeff22",
        "project_validation": "21edba17da36b9d46956214adcbf4ffa70a03ab4c3eee286bf81b32ee08aebd0",
    },
}


class InputValidationError(ValueError):
    """An input lacks the source, review, or transformation evidence required."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise InputValidationError(message)


def _read_json(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InputValidationError(f"Invalid {label} JSON") from exc
    _need(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _reference(root: Path, ref: dict, label: str) -> tuple[Path, bytes]:
    _need(isinstance(ref, dict), f"Missing {label} file reference")
    path_value, expected = ref.get("path"), ref.get("sha256")
    _need(isinstance(path_value, str) and path_value and isinstance(expected, str)
          and _SHA.fullmatch(expected) is not None, f"Invalid {label} reference")
    relative = Path(path_value)
    _need(not relative.is_absolute() and not any(part == ".." for part in relative.parts),
          f"{label} path must remain within the project root")
    path = (root / relative).resolve()
    _need(path.is_relative_to(root) and path.is_file(), f"Missing or out-of-root {label}")
    data = path.read_bytes()
    _need(sha256(data) == expected, f"{label} SHA-256 mismatch")
    return path, data


def _path_record(root: Path, ref: dict, label: str) -> tuple[Path, bytes]:
    # Existing M9 provenance uses repository-relative backslash paths on Windows.
    return _reference(root, ref, label)


def _span(value: Any, size: int, label: str, *, allow_empty: bool = False) -> tuple[int, int]:
    _need(isinstance(value, list) and len(value) == 2 and
          all(type(v) is int for v in value), f"Invalid {label} span")
    start, end = value
    _need(0 <= start <= end <= size and (allow_empty or start < end),
          f"Out-of-range {label} span")
    return start, end


def _compact(value: str) -> str:
    return "".join(value.split())


class _VisibleCell(HTMLParser):
    """Re-derive visible source-cell text independently of saved provenance."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "div", "p", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"div", "p", "li"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _visible_cell_text(fragment: str) -> str:
    parser = _VisibleCell()
    parser.feed(fragment)
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts).replace("\xa0", " ")).strip()


def _verify_primary_html(raw: bytes, saved_html: bytes) -> None:
    """Bind the saved HTML to the primary 10-Q TEXT, not another raw document."""
    try:
        primary_bytes, _source = _primary_source(raw)
    except ExtractionError as exc:
        raise InputValidationError("Cannot identify one raw primary 10-Q document") from exc
    _need(primary_bytes == saved_html,
          "Saved primary HTML is not the exact raw primary 10-Q TEXT bytes")


def _validate_acceptance(root: Path, event: dict, ticker: str, identity: dict,
                         hashes: dict[str, str]) -> dict:
    kind = event.get("kind")
    _need(event.get("accepted_use_scope", "").find("descriptive") >= 0,
          "Acceptance does not permit descriptive saved-text use")
    if kind == "AMZN_10Q_MDA_OVERALL_RESEARCHER_DECISION_EVENT":
        _need(ticker == "AMZN" and event.get("overall_researcher_decision") == "ACCEPT",
              "AMZN overall decision mismatch")
        _need(event.get("filing", {}).get("cik") == identity["cik"] and
              event["filing"].get("accession") == identity["accession"] and
              event["filing"].get("form") == identity["form"] and
              event["filing"].get("section") == "Part I Item 2 MD&A",
              "AMZN acceptance filing identity mismatch")
        bindings = event.get("version_binding", {})
        for field, key in (("raw_submission_sha256", "raw"),
                           ("original_item2_sha256", "parent_section"),
                           ("proposed_structured_item2_sha256", "derivative"),
                           ("structured_provenance_sha256", "provenance")):
            _need(bindings.get(field) == hashes[key], f"AMZN acceptance {field} mismatch")
        prior = event.get("prior_item_decisions")
        _reference(root, prior, "AMZN item decisions")
        choices = event.get("item_decisions")
        _need(isinstance(choices, dict) and len(choices) == 12 and
              all(v == "ACCEPT" for v in choices.values()),
              "AMZN item decisions are incomplete")
        return {"event_kind": kind, "item_decisions": len(choices),
                "overall_decision": "ACCEPT"}
    if kind == "GOOGL_10Q_MDA_OVERALL_RESEARCHER_DECISION_EVENT":
        _need(ticker == "GOOGL" and event.get("overall_decision") == "ACCEPT",
              "GOOGL overall decision mismatch")
        reviewed = event.get("reviewed_object", {})
        _need(reviewed.get("cik") == identity["cik"] and
              reviewed.get("accession") == identity["accession"] and
              reviewed.get("form") == identity["form"] and
              reviewed.get("section") == "Part I Item 2 MD&A",
              "GOOGL acceptance filing identity mismatch")
        for field, key in (("original_submission", "raw"),
                           ("original_item2", "parent_section"),
                           ("proposed_structured_item2", "derivative")):
            record = reviewed.get(field, {})
            _need(record.get("sha256") == hashes[key],
                  f"GOOGL acceptance {field} mismatch")
            _reference(root, record, f"GOOGL acceptance {field}")
        prior = event.get("prior_item_state")
        _, prior_bytes = _reference(root, prior, "GOOGL item state")
        state = _read_json(prior_bytes, "GOOGL item state")
        items = state.get("items")
        _need(isinstance(items, dict) and len(items) == 18 and
              all(isinstance(v, dict) and v.get("decision") == "ACCEPT"
                  for v in items.values()), "GOOGL item decisions are incomplete")
        return {"event_kind": kind, "item_decisions": len(items),
                "overall_decision": "ACCEPT"}
    raise InputValidationError("Unsupported acceptance event schema")


def _validate_project_record(record: dict, ticker: str, hashes: dict[str, str]) -> None:
    _need(record.get("kind") == "M9_AMZN_GOOGL_SEPARATE_PROJECT_VALIDATION_RECEIPT" and
          record.get("content_provenance_status", {}).get(ticker) in
          {"PASS", "PASS_WITH_LIMITATIONS"}, "Project validation content gate missing")
    _need(record.get("status") == "PASS_WITH_LIMITATIONS_AND_EXECUTION_BLOCKED",
          "Unsupported project validation receipt state")
    checks = record.get("checks")
    _need(isinstance(checks, list) and checks and
          all(isinstance(row, dict) and row.get("status") == "PASS" for row in checks),
          "Project validation contains failed checks")
    inputs = record.get("inputs", {})
    for label, key in (("raw", "raw"), ("old_section", "parent_section"),
                       ("derivative", "derivative"), ("provenance", "provenance"),
                       ("overall event", "acceptance")):
        _need(inputs.get(f"{ticker} {label}", {}).get("sha256") == hashes[key],
              f"Project validation {ticker} {label} binding mismatch")


def _validate_tables(provenance: dict, parent: str, derivative: str,
                     html: str, raw: bytes) -> tuple[list[dict], list[str]]:
    replacements = provenance.get("replacements")
    segments = provenance.get("unchanged_segments")
    _need(isinstance(replacements, list) and replacements and
          isinstance(segments, list) and len(segments) == len(replacements) + 1,
          "Incomplete replacement or unchanged-segment inventory")
    cursor_parent = cursor_derived = 0
    tables: list[dict] = []
    limitations: list[str] = []
    for i, table in enumerate(replacements):
        table_id = table.get("table_id")
        _need(isinstance(table_id, str) and table_id == f"MDA-{i+1}",
              "Unexpected or reordered table ID")
        old_start, old_end = _span(table.get("parent_section_unicode_span"), len(parent),
                                    f"{table_id} parent")
        new_start, new_end = _span(table.get("derivative_unicode_span"), len(derivative),
                                    f"{table_id} derivative")
        _need(cursor_parent <= old_start and cursor_derived <= new_start,
              f"Overlapping {table_id} replacement")
        old_gap, new_gap = parent[cursor_parent:old_start], derivative[cursor_derived:new_start]
        _need(old_gap == new_gap, f"Changed non-table text before {table_id}")
        segment = segments[i]
        _need(segment.get("parent_section_unicode_span") == [cursor_parent, old_start] and
              segment.get("derivative_unicode_span") == [cursor_derived, new_start] and
              segment.get("sha256") == sha256(old_gap.encode("utf-8")),
              f"Unchanged-segment evidence mismatch before {table_id}")
        old_piece, new_piece = parent[old_start:old_end], derivative[new_start:new_end]
        _need(sha256(old_piece.encode("utf-8")) == table.get("parent_table_text_sha256") and
              sha256(new_piece.encode("utf-8")) == table.get("derivative_table_text_sha256"),
              f"{table_id} replacement text hash mismatch")
        html_start, html_end = _span(table.get("source_html_unicode_span"), len(html),
                                      f"{table_id} HTML")
        fragment = html[html_start:html_end]
        fragment_bytes = fragment.encode("utf-8")
        if "source_html_byte_span" in table:
            html_byte_start, html_byte_end = _span(table["source_html_byte_span"],
                                                    len(html.encode("utf-8")),
                                                    f"{table_id} HTML bytes")
            _need(html.encode("utf-8")[html_byte_start:html_byte_end] == fragment_bytes,
                  f"{table_id} HTML byte coordinates disagree with Unicode coordinates")
        raw_start, raw_end = _span(table.get("raw_submission_byte_span"), len(raw),
                                    f"{table_id} raw bytes")
        _need(sha256(fragment_bytes) == table.get("source_html_fragment_sha256") and
              raw[raw_start:raw_end] == fragment_bytes,
              f"{table_id} HTML fragment is not the saved raw bytes")
        cells = table.get("source_cells")
        rows = table.get("source_row_to_output")
        _need(isinstance(cells, list) and cells and isinstance(rows, list) and rows,
              f"{table_id} source cell or row inventory missing")
        inventory = {}
        for cell in cells:
            cell_id = cell.get("cell_id")
            _need(isinstance(cell_id, str) and cell_id not in inventory,
                  f"{table_id} duplicate source cell")
            c0, c1 = _span(cell.get("primary_html_unicode_span"), len(html),
                           f"{table_id} {cell_id} source cell")
            _need(html_start <= c0 < c1 <= html_end and
                  sha256(html[c0:c1].encode("utf-8")) == cell.get("html_fragment_sha256"),
                  f"{table_id} {cell_id} source cell hash mismatch")
            _need(cell.get("text") == _visible_cell_text(html[c0:c1]),
                  f"{table_id} {cell_id} declared text differs from saved HTML cell")
            if "html_fragment_char_span" in cell:
                f0, f1 = _span(cell["html_fragment_char_span"], len(fragment),
                                f"{table_id} {cell_id} relative cell")
                _need(fragment[f0:f1] == html[c0:c1],
                      f"{table_id} {cell_id} relative source span mismatch")
            inventory[cell_id] = cell
        _need(len(inventory) == table.get("source_cell_count"),
              f"{table_id} source-cell count mismatch")
        seen_row_cells: list[str] = []
        for row in rows:
            r0, r1 = _span(row.get("derivative_unicode_span"), len(derivative),
                           f"{table_id} output row")
            _need(new_start <= r0 < r1 <= new_end and
                  isinstance(row.get("source_cell_ids"), list),
                  f"{table_id} output row not inside replacement")
            row_text = _compact(derivative[r0:r1])
            for cell_id in row["source_cell_ids"]:
                _need(cell_id in inventory, f"{table_id} output row references unknown cell")
                source_text = _compact(inventory[cell_id].get("text", ""))
                if source_text:
                    _need(source_text in row_text,
                          f"{table_id} source cell text missing from output row")
            seen_row_cells.extend(row["source_cell_ids"])
        _need(seen_row_cells == list(inventory), f"{table_id} source row order changed")
        if table.get("source_cell_to_output"):
            mapped_ids = []
            for mapping in table["source_cell_to_output"]:
                cell_id = mapping.get("source_cell_id")
                _need(cell_id in inventory, f"{table_id} unknown mapped source cell")
                m0, m1 = _span(mapping.get("derivative_unicode_span"), len(derivative),
                               f"{table_id} mapped cell")
                _need(new_start <= m0 < m1 <= new_end and
                      _compact(inventory[cell_id].get("text", "")) in
                      _compact(derivative[m0:m1]),
                      f"{table_id} mapped source cell text changed")
                mapped_ids.append(cell_id)
            _need(mapped_ids == [cell_id for cell_id, cell in inventory.items()
                                 if cell.get("text", "").strip()],
                  f"{table_id} nonempty-cell mapping incomplete")
        else:
            for mapping in table.get("source_value_to_output", []):
                ids = mapping.get("source_value_cell_ids")
                _need(isinstance(ids, list) and ids and all(cell_id in inventory for cell_id in ids),
                      f"{table_id} value mapping references unknown cells")
                m0, m1 = _span(mapping.get("derivative_unicode_span"), len(derivative),
                               f"{table_id} mapped value")
                _need(new_start <= m0 < m1 <= new_end and
                      _compact(mapping.get("formatted_value", "")) in
                      _compact(derivative[m0:m1]),
                      f"{table_id} mapped value text changed")
        status = table.get("header_evidence", {}).get("association_status")
        if isinstance(status, str) and status.startswith("UNRESOLVED_"):
            _need(not table.get("source_value_to_output"),
                  f"{table_id} asserts mappings despite unresolved header association")
            limitations.append(f"{table_id}:{status}")
        tables.append({"table_id": table_id,
                       "source_html_fragment_sha256": table["source_html_fragment_sha256"],
                       "parent_table_text_sha256": table["parent_table_text_sha256"],
                       "derivative_table_text_sha256": table["derivative_table_text_sha256"],
                       "source_cell_count": len(inventory),
                       "header_association_status": status})
        cursor_parent, cursor_derived = old_end, new_end
    old_tail, new_tail = parent[cursor_parent:], derivative[cursor_derived:]
    _need(old_tail == new_tail, "Changed trailing non-table text")
    segment = segments[-1]
    _need(segment.get("parent_section_unicode_span") == [cursor_parent, len(parent)] and
          segment.get("derivative_unicode_span") == [cursor_derived, len(derivative)] and
          segment.get("sha256") == sha256(old_tail.encode("utf-8")),
          "Trailing unchanged-segment evidence mismatch")
    return tables, limitations


def load_validated_input(spec_path: Path, project_root: Path | None = None) -> tuple[dict, dict]:
    """Load one reviewed original extract or one accepted structured derivative.

    The caller owns the spec file. Referenced paths must remain inside
    ``project_root`` (the repository by default) and every reference has a
    SHA-256. Returned text is the exact UTF-8 input, not generated review text.
    """
    root = (project_root or PROJECT_ROOT).resolve()
    spec_path = Path(spec_path).resolve()
    _need(spec_path.is_file() and spec_path.is_relative_to(root),
          "Input spec must be a file within the project root")
    spec = _read_json(spec_path.read_bytes(), "input spec")
    _need(spec.get("schema_version") == 1, "Unsupported input spec schema")
    input_type = spec.get("input_type")
    if input_type == "original_extract":
        _, index_bytes = _reference(root, spec.get("section_index"), "original section index")
        from .m6 import load_sections
        path = (root / spec["section_index"]["path"]).resolve()
        index = _read_json(index_bytes, "original section index")
        validated_files = [spec["section_index"]]
        for entry in index.get("entries", []):
            for field in ("raw", "text"):
                source = (path.parent / entry[f"{field}_path"]).resolve()
                _need(source.is_relative_to(root),
                      "Original section source escapes project root")
                record = {"path": source.relative_to(root).as_posix(),
                          "sha256": entry[f"{field}_sha256"]}
                _reference(root, record, f"original {field}")
                validated_files.append(record)
        docs, audit = load_sections(path)
        _need(len(docs) == 1, "Original-extract spec must select exactly one document")
        doc = docs[0]
        doc["input_type"] = "original_extract"
        doc["input_version"] = sha256(spec_path.read_bytes())
        doc["limitations"] = ["Historical public availability is not verified"]
        return doc, {**audit, "input_type": input_type,
                     "validation_status": "PASS",
                     "spec_sha256": doc["input_version"], "validated_document_count": 1,
                     "validated_files": validated_files}
    _need(input_type == "structured_derivative", "Unsupported input type")
    identity = spec.get("identity")
    _need(isinstance(identity, dict) and
          all(isinstance(identity.get(key), str) and identity[key]
              for key in ("ticker", "cik", "accession", "form", "section")),
          "Structured input identity is incomplete")
    _need(identity["form"] == "10-Q" and identity["section"] == _STRUCTURED_SECTION,
          "Unsupported structured form/section")
    ticker = identity["ticker"]
    trusted = TRUSTED_ACCEPTED_INPUTS.get(ticker)
    _need(trusted is not None and identity["cik"] == trusted["cik"] and
          identity["accession"] == trusted["accession"],
          "Structured input has no trusted accepted identity")
    _, provenance_bytes = _reference(root, spec.get("provenance"), "structured provenance")
    provenance = _read_json(provenance_bytes, "structured provenance")
    _need(provenance.get("filing", {}).get("cik") == identity["cik"] and
          provenance["filing"].get("accession") == identity["accession"] and
          provenance["filing"].get("form") == identity["form"] and
          provenance["filing"].get("target") == "Part I Item 2 MD&A",
          "Provenance filing identity mismatch")
    _need(provenance.get("serializer_version") == spec.get("serializer_version") and
          isinstance(spec.get("serializer_version"), str),
          "Serializer version mismatch")
    _, serializer_bytes = _reference(root, spec.get("serializer"), "serializer source")
    _need(sha256(serializer_bytes) == provenance.get("serializer_source_sha256"),
          "Serializer source does not match provenance")
    config_path, config_bytes = _path_record(root, {"path": provenance.get("config_path"),
                                              "sha256": provenance.get("config_sha256")},
                                              "serializer configuration")
    del config_path, config_bytes
    parent_inputs = provenance.get("parent_inputs")
    _need(isinstance(parent_inputs, dict), "Structured provenance lacks parent inputs")
    source_data = {}
    for name in ("original_submission.txt", "primary_document_source.html",
                 "primary_text.txt", "item2_candidate.txt", "table_index.json"):
        _, source_data[name] = _path_record(root, parent_inputs.get(name), f"parent {name}")
    _, derivative_bytes = _reference(root, spec.get("derivative"), "structured derivative")
    _need(sha256(derivative_bytes) == provenance.get("derivative_sha256"),
          "Derivative hash does not match provenance")
    raw = source_data["original_submission.txt"]
    raw_header(raw, identity["accession"], identity["form"])
    _need(raw_filer_cik(raw) == identity["cik"], "SEC header CIK mismatch")
    html_bytes = source_data["primary_document_source.html"]
    _verify_primary_html(raw, html_bytes)
    rendered, _ = primary_text(raw, identity["form"])
    _need(rendered.encode("utf-8") == source_data["primary_text.txt"],
          "Saved primary text cannot be reproduced from raw filing")
    _, boundary_bytes = _reference(root, spec.get("boundary_evidence"),
                                   "section boundary evidence")
    boundary = _read_json(boundary_bytes, "section boundary evidence")
    selected = boundary.get("selected_candidate_index")
    candidates = boundary.get("candidates")
    _need(type(selected) is int and isinstance(candidates, list) and
          0 <= selected < len(candidates), "Boundary selection is missing")
    candidate = candidates[selected]
    start = candidate.get("start_char")
    end = candidate.get("end_char_exclusive")
    _need(type(start) is int and type(end) is int and 0 <= start < end <= len(rendered),
          "Boundary coordinates are invalid")
    parent_bytes = source_data["item2_candidate.txt"]
    _need(rendered[start:end].encode("utf-8") == parent_bytes and
          sha256(parent_bytes) == candidate.get("content_sha256"),
          "Original Item 2 is not the verified primary-text substring")
    try:
        parent = parent_bytes.decode("utf-8")
        derivative = derivative_bytes.decode("utf-8")
        html = html_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise InputValidationError("Invalid UTF-8 in structured source or derivative") from exc
    tables, limitations = _validate_tables(provenance, parent, derivative, html, raw)
    notes = provenance.get("adjacent_notes_outside_tables", [])
    _need(isinstance(notes, list), "Invalid adjacent-note inventory")
    for note in notes:
        p0, p1 = _span(note.get("parent_section_unicode_span"), len(parent),
                        "adjacent note in parent")
        d0, d1 = _span(note.get("derivative_unicode_span"), len(derivative),
                        "adjacent note in derivative")
        _need(parent[p0:p1] == derivative[d0:d1] and
              sha256(parent[p0:p1].encode("utf-8")) == note.get("text_sha256") and
              note.get("text_preserved_exactly") is True,
              "Adjacent note text or provenance changed")
    if notes:
        limitations.append("Adjacent table-note attribution remains a source candidate")
    hashes = {"raw": sha256(raw), "parent_section": sha256(parent_bytes),
              "derivative": sha256(derivative_bytes),
              "provenance": sha256(provenance_bytes)}
    _, event_bytes = _reference(root, spec.get("acceptance_event"),
                                "researcher acceptance event")
    hashes["acceptance"] = sha256(event_bytes)
    _need(all(hashes[key] == trusted[key] for key in hashes),
          "Structured input differs from trusted M9 source or acceptance hashes")
    event = _read_json(event_bytes, "researcher acceptance event")
    acceptance = _validate_acceptance(root, event, ticker, identity, hashes)
    prior_ref = (event["prior_item_decisions"] if ticker == "AMZN"
                 else event["prior_item_state"])
    original_reply_ref = (event.get("original_reply_path") if ticker == "AMZN"
                          else event.get("private_original_reply"))
    _reference(root, original_reply_ref, "private original researcher reply")
    _, validation_bytes = _reference(root, spec.get("project_validation"),
                                     "project validation receipt")
    _need(sha256(validation_bytes) == trusted["project_validation"],
          "Project validation differs from trusted M9 receipt")
    _validate_project_record(_read_json(validation_bytes, "project validation receipt"),
                             ticker, hashes)
    if ticker == "GOOGL":
        _need({x for x in limitations if x.startswith("MDA-")} == {
            "MDA-1:UNRESOLVED_SPARSE_HEADER_TO_VALUE_MAPPING",
            "MDA-5:UNRESOLVED_SPARSE_MULTI_LEVEL_HEADER_TO_VALUE_MAPPING"},
            "GOOGL sparse-header limitations not preserved")
    limitations.extend(["Generated structural labels are not SEC wording",
                        "Historical first-public and tradable times are unverified"])
    doc = {"id": "|".join((identity["cik"], identity["accession"], identity["section"],
                                hashes["derivative"])),
           **identity, "input_type": input_type, "input_version": hashes["derivative"],
           "provenance_path": spec["provenance"]["path"],
           "review_references": {
               "acceptance_event": spec["acceptance_event"],
               "project_validation": spec["project_validation"],
           },
           "text": derivative, "text_sha256": hashes["derivative"],
           "raw_sha256": hashes["raw"], "parent_section_sha256": hashes["parent_section"],
           "provenance_sha256": hashes["provenance"],
           "acceptance_event_sha256": hashes["acceptance"],
           "project_validation_sha256": sha256(validation_bytes),
           "boundary_evidence_sha256": sha256(boundary_bytes),
           "serializer_version": provenance["serializer_version"],
           "serializer_source_sha256": provenance["serializer_source_sha256"],
           "generated_structural_labels": True, "table_ids": [t["table_id"] for t in tables],
           "table_provenance": [{"table_id": t["table_id"],
                                 "derivative_unicode_span": source["derivative_unicode_span"],
                                 "parent_section_unicode_span": source["parent_section_unicode_span"],
                                 "source_html_unicode_span": source["source_html_unicode_span"],
                                 "raw_submission_byte_span": source["raw_submission_byte_span"]}
                                for t, source in zip(tables, provenance["replacements"])],
           "limitations": limitations}
    audit = {"source": "validated-structured-derivative", "input_type": input_type,
             "spec_sha256": sha256(spec_path.read_bytes()), "identity": identity,
             "hashes": hashes, "acceptance": acceptance,
             "project_content_status": "PASS_WITH_LIMITATIONS",
             "validation_status": "PASS_WITH_LIMITATIONS",
             "source_tables": tables, "non_table_text_preserved": True,
             "header_association_limitations": [x for x in limitations if x.startswith("MDA-")],
             "historical_public_availability": "NOT_VERIFIED",
             "validated_files": [spec["derivative"], spec["provenance"], spec["serializer"],
                                 spec["boundary_evidence"], spec["acceptance_event"],
                                 spec["project_validation"], prior_ref, original_reply_ref,
                                 {"path": spec_path.relative_to(root).as_posix(),
                                  "sha256": sha256(spec_path.read_bytes())},
                                 {"path": provenance["config_path"],
                                  "sha256": provenance["config_sha256"]},
                                 *[parent_inputs[name] for name in
                                   ("original_submission.txt", "primary_document_source.html",
                                    "primary_text.txt", "item2_candidate.txt", "table_index.json")]]}
    return doc, audit
