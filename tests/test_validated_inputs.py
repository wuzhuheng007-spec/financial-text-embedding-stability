"""Synthetic software fixtures for version-bound input loading, not research data."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import finembed.validated_inputs as validated_inputs
from finembed.extract import primary_text
from finembed.run import sha256
from finembed.validated_inputs import (InputValidationError, _validate_tables,
                                        _verify_primary_html, load_validated_input)


def _save(root: Path, name: str, data: bytes) -> dict:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": name, "sha256": sha256(data)}


def _json(root: Path, name: str, value: dict) -> dict:
    return _save(root, name, (json.dumps(value, sort_keys=True) + "\n").encode())


def _fixture(root: Path, monkeypatch) -> tuple[Path, dict]:
    # One deliberately small table plus a long synthetic narrative for the
    # primary_text plausibility check; no SEC or company result is implied.
    narrative = "SYNTHETIC SOFTWARE TEST narrative only. " * 80
    html = f"<html><p>Part I Item 2 MD&amp;A</p><p>{narrative}</p><table><tr><td>Cloud 12</td></tr></table><p>End narrative.</p></html>"
    accession, cik = "0000000001-25-000001", "0000000001"
    raw = (f"<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20250102120000\nACCESSION NUMBER: {accession}\n"
           f"CONFORMED SUBMISSION TYPE: 10-Q\nFILED AS OF DATE: 20250102\n"
           f"FILER:\n\tCOMPANY DATA:\n\t\tCENTRAL INDEX KEY: {cik}\n</SEC-HEADER>\n"
           f"<DOCUMENT>\n<TYPE>10-Q\n<FILENAME>main.htm\n<TEXT>{html}</TEXT>\n</DOCUMENT>").encode()
    rendered, _ = primary_text(raw, "10-Q")
    old_start = rendered.index("Cloud 12")
    old_end = old_start + len("Cloud 12")
    new_piece = "Source row 1: Source cell R1C1 = Cloud 12"
    derivative = rendered[:old_start] + new_piece + rendered[old_end:]
    new_start, new_end = old_start, old_start + len(new_piece)
    frag = "<table><tr><td>Cloud 12</td></tr></table>"
    html_start, raw_start = html.index(frag), raw.decode().index(frag)
    cell = "<td>Cloud 12</td>"
    cell_start = html.index(cell)
    raw_ref = _save(root, "raw.txt", raw)
    html_ref = _save(root, "primary.html", html.encode())
    primary_ref = _save(root, "primary.txt", rendered.encode())
    old_ref = _save(root, "old.txt", rendered.encode())
    table_index_ref = _json(root, "table_index.json", {"fixture": True})
    derivative_ref = _save(root, "derivative.txt", derivative.encode())
    serializer_ref = _save(root, "serializer.py", b"# synthetic fixture serializer\n")
    config_ref = _save(root, "config.yaml", b"fixture: true\n")
    boundary_ref = _json(root, "boundary.json", {
        "selected_candidate_index": 0,
        "candidates": [{"start_char": 0, "end_char_exclusive": len(rendered),
                        "content_sha256": old_ref["sha256"]}],
    })
    table = {
        "table_id": "MDA-1", "parent_section_unicode_span": [old_start, old_end],
        "derivative_unicode_span": [new_start, new_end],
        "parent_table_text_sha256": sha256(rendered[old_start:old_end].encode()),
        "derivative_table_text_sha256": sha256(new_piece.encode()),
        "source_html_unicode_span": [html_start, html_start + len(frag)],
        "source_html_fragment_sha256": sha256(frag.encode()),
        "raw_submission_byte_span": [raw_start, raw_start + len(frag)],
        "source_cell_count": 1,
        "source_cells": [{"cell_id": "R1C1", "text": "Cloud 12",
                          "primary_html_unicode_span": [cell_start, cell_start + len(cell)],
                          "html_fragment_sha256": sha256(cell.encode())}],
        "source_row_to_output": [{"source_row": 1, "source_cell_ids": ["R1C1"],
                                  "derivative_unicode_span": [new_start, new_end]}],
        "source_value_to_output": [{"source_value_cell_ids": ["R1C1"],
                                    "formatted_value": "Cloud 12",
                                    "derivative_unicode_span": [new_start, new_end]}],
    }
    provenance_ref = _json(root, "provenance.json", {
        "filing": {"cik": cik, "accession": accession, "form": "10-Q",
                   "target": "Part I Item 2 MD&A"},
        "serializer_version": "synthetic_source_grid_v1",
        "serializer_source_sha256": serializer_ref["sha256"],
        "config_path": config_ref["path"], "config_sha256": config_ref["sha256"],
        "parent_inputs": {
            "original_submission.txt": raw_ref,
            "primary_document_source.html": html_ref,
            "primary_text.txt": primary_ref,
            "item2_candidate.txt": old_ref,
            "table_index.json": table_index_ref,
        },
        "derivative_sha256": derivative_ref["sha256"],
        "replacements": [table],
        "unchanged_segments": [
            {"parent_section_unicode_span": [0, old_start],
             "derivative_unicode_span": [0, new_start],
             "sha256": sha256(rendered[:old_start].encode())},
            {"parent_section_unicode_span": [old_end, len(rendered)],
             "derivative_unicode_span": [new_end, len(derivative)],
             "sha256": sha256(rendered[old_end:].encode())},
        ],
    })
    prior_ref = _json(root, "prior_items.json", {"fixture": "12 synthetic decisions"})
    original_reply_ref = _save(root, "original_reply_private.txt",
                               b"SYNTHETIC SOFTWARE TEST reply, not a researcher review")
    event_ref = _json(root, "acceptance.json", {
        "kind": "AMZN_10Q_MDA_OVERALL_RESEARCHER_DECISION_EVENT",
        "accepted_use_scope": "synthetic descriptive test only",
        "overall_researcher_decision": "ACCEPT",
        "filing": {"cik": cik, "accession": accession, "form": "10-Q",
                   "section": "Part I Item 2 MD&A"},
        "version_binding": {
            "raw_submission_sha256": raw_ref["sha256"],
            "original_item2_sha256": old_ref["sha256"],
            "proposed_structured_item2_sha256": derivative_ref["sha256"],
            "structured_provenance_sha256": provenance_ref["sha256"],
        },
        "prior_item_decisions": prior_ref,
        "original_reply_path": original_reply_ref,
        "item_decisions": {f"ITEM-{i}": "ACCEPT" for i in range(12)},
    })
    project_ref = _json(root, "project_receipt.json", {
        "kind": "M9_AMZN_GOOGL_SEPARATE_PROJECT_VALIDATION_RECEIPT",
        "status": "PASS_WITH_LIMITATIONS_AND_EXECUTION_BLOCKED",
        "content_provenance_status": {"AMZN": "PASS_WITH_LIMITATIONS"},
        "checks": [{"check": "synthetic software fixture", "status": "PASS"}],
        "inputs": {
            "AMZN raw": {"sha256": raw_ref["sha256"]},
            "AMZN old_section": {"sha256": old_ref["sha256"]},
            "AMZN derivative": {"sha256": derivative_ref["sha256"]},
            "AMZN provenance": {"sha256": provenance_ref["sha256"]},
            "AMZN overall event": {"sha256": event_ref["sha256"]},
        },
    })
    spec = {
        "schema_version": 1, "input_type": "structured_derivative",
        "identity": {"ticker": "AMZN", "cik": cik, "accession": accession,
                     "form": "10-Q", "section": "PART_I_ITEM_2_MDA"},
        "derivative": derivative_ref, "provenance": provenance_ref,
        "serializer": serializer_ref, "serializer_version": "synthetic_source_grid_v1",
        "boundary_evidence": boundary_ref, "acceptance_event": event_ref,
        "project_validation": project_ref,
    }
    spec_path = root / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    # Test-only trust anchor. Production never accepts a spec's self-declared
    # hashes as evidence that a researcher actually approved it.
    monkeypatch.setattr(validated_inputs, "TRUSTED_ACCEPTED_INPUTS", {"AMZN": {
        "cik": cik, "accession": accession,
        "raw": raw_ref["sha256"], "parent_section": old_ref["sha256"],
        "derivative": derivative_ref["sha256"],
        "provenance": provenance_ref["sha256"],
        "acceptance": event_ref["sha256"],
        "project_validation": project_ref["sha256"],
    }})
    return spec_path, spec


def test_synthetic_structured_derivative_loads_only_with_full_provenance(tmp_path, monkeypatch):
    path, spec = _fixture(tmp_path, monkeypatch)
    doc, audit = load_validated_input(path, project_root=tmp_path)
    assert doc["input_type"] == "structured_derivative"
    assert doc["text_sha256"] == spec["derivative"]["sha256"]
    assert doc["raw_sha256"] != doc["text_sha256"]
    assert audit["non_table_text_preserved"] is True
    assert audit["acceptance"]["item_decisions"] == 12
    assert doc["table_ids"] == ["MDA-1"]
    assert "Generated structural labels are not SEC wording" in doc["limitations"]


@pytest.mark.parametrize("field", ["derivative", "provenance", "serializer",
                                    "boundary_evidence", "acceptance_event",
                                    "project_validation"])
def test_missing_or_changed_required_reference_fails(tmp_path, monkeypatch, field):
    path, spec = _fixture(tmp_path, monkeypatch)
    spec[field]["sha256"] = "0" * 64
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(InputValidationError, match="SHA-256 mismatch"):
        load_validated_input(path, project_root=tmp_path)


def test_changed_raw_bytes_and_wrong_cik_fail(tmp_path, monkeypatch):
    path, spec = _fixture(tmp_path, monkeypatch)
    (tmp_path / "raw.txt").write_bytes(b"tampered raw")
    with pytest.raises(InputValidationError, match="SHA-256 mismatch"):
        load_validated_input(path, project_root=tmp_path)
    path, spec = _fixture(tmp_path, monkeypatch)
    spec["identity"]["cik"] = "9999999999"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(InputValidationError, match="trusted accepted identity"):
        load_validated_input(path, project_root=tmp_path)


def test_acceptance_binding_must_match_current_derivative(tmp_path, monkeypatch):
    path, spec = _fixture(tmp_path, monkeypatch)
    event_path = tmp_path / spec["acceptance_event"]["path"]
    event = json.loads(event_path.read_text())
    event["version_binding"]["proposed_structured_item2_sha256"] = "0" * 64
    spec["acceptance_event"] = _json(tmp_path, "wrong_acceptance.json", event)
    trusted = dict(validated_inputs.TRUSTED_ACCEPTED_INPUTS["AMZN"])
    trusted["acceptance"] = spec["acceptance_event"]["sha256"]
    monkeypatch.setattr(validated_inputs, "TRUSTED_ACCEPTED_INPUTS", {"AMZN": trusted})
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(InputValidationError, match="acceptance .* mismatch"):
        load_validated_input(path, project_root=tmp_path)


def test_project_receipt_cannot_be_a_bare_status_string(tmp_path, monkeypatch):
    path, spec = _fixture(tmp_path, monkeypatch)
    spec["project_validation"] = _json(tmp_path, "bare_status.json", {"status": "PASS"})
    trusted = dict(validated_inputs.TRUSTED_ACCEPTED_INPUTS["AMZN"])
    trusted["project_validation"] = spec["project_validation"]["sha256"]
    monkeypatch.setattr(validated_inputs, "TRUSTED_ACCEPTED_INPUTS", {"AMZN": trusted})
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(InputValidationError, match="content gate"):
        load_validated_input(path, project_root=tmp_path)


def test_unsupported_type_and_unreviewed_original_rejected(tmp_path, monkeypatch):
    path, spec = _fixture(tmp_path, monkeypatch)
    spec["input_type"] = "arbitrary_text"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(InputValidationError, match="Unsupported input type"):
        load_validated_input(path, project_root=tmp_path)
    spec["input_type"] = "original_extract"
    index = {"entries": [{"ticker": "TEST", "cik": "0000000001",
                           "accession": "0000000001-25-000001", "form": "10-Q",
                           "section": "PART_I_ITEM_2_MDA", "boundary_reviewed": False,
                           "raw_path": "raw.txt", "raw_sha256": sha256((tmp_path / "raw.txt").read_bytes()),
                           "text_path": "old.txt", "text_sha256": sha256((tmp_path / "old.txt").read_bytes()),
                           "start_char": 0, "end_char_exclusive": 10}]}
    spec["section_index"] = _json(tmp_path, "original_index.json", index)
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="attestation"):
        load_validated_input(path, project_root=tmp_path)


def test_reviewed_original_uses_existing_exact_substring_gate(tmp_path, monkeypatch):
    path, spec = _fixture(tmp_path, monkeypatch)
    spec["input_type"] = "original_extract"
    original = (tmp_path / "old.txt").read_text(encoding="utf-8")
    index = {"entries": [{"ticker": "TEST", "cik": "0000000001",
                           "accession": "0000000001-25-000001", "form": "10-Q",
                           "section": "PART_I_ITEM_2_MDA", "boundary_reviewed": True,
                           "raw_path": "raw.txt", "raw_sha256": sha256((tmp_path / "raw.txt").read_bytes()),
                           "text_path": "old.txt", "text_sha256": sha256(original.encode()),
                           "start_char": 0, "end_char_exclusive": len(original)}]}
    spec["section_index"] = _json(tmp_path, "original_index.json", index)
    path.write_text(json.dumps(spec), encoding="utf-8")
    doc, audit = load_validated_input(path, project_root=tmp_path)
    assert doc["input_type"] == "original_extract"
    assert doc["text"] == original
    assert audit["raw_and_substring_checks"] == "PASS"
    assert audit["validation_status"] == "PASS"
    assert len(audit["validated_files"]) == 3
    (tmp_path / "old.txt").write_text(original.replace("End narrative", "Changed content"),
                                       encoding="utf-8")
    with pytest.raises(InputValidationError, match="SHA-256 mismatch"):
        load_validated_input(path, project_root=tmp_path)


def test_structured_spec_cannot_authenticate_itself(tmp_path, monkeypatch):
    path, _ = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(validated_inputs, "TRUSTED_ACCEPTED_INPUTS", {})
    with pytest.raises(InputValidationError, match="trusted accepted identity"):
        load_validated_input(path, project_root=tmp_path)


def test_unresolved_header_cannot_assert_value_mapping_or_change_narrative(tmp_path, monkeypatch):
    _, _ = _fixture(tmp_path, monkeypatch)
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    parent = (tmp_path / "old.txt").read_text()
    derivative = (tmp_path / "derivative.txt").read_text()
    html = (tmp_path / "primary.html").read_text()
    raw = (tmp_path / "raw.txt").read_bytes()
    provenance["replacements"][0]["header_evidence"] = {
        "association_status": "UNRESOLVED_SYNTHETIC_MAPPING"}
    with pytest.raises(InputValidationError, match="asserts mappings"):
        _validate_tables(provenance, parent, derivative, html, raw)
    del provenance["replacements"][0]["header_evidence"]
    with pytest.raises(InputValidationError, match="Changed trailing non-table text"):
        _validate_tables(provenance, parent,
                         derivative.replace("End narrative", "Altered narrative"), html, raw)


def test_declared_cell_text_must_match_raw_bound_html(tmp_path, monkeypatch):
    """A synthetic wrong value must fail even with matching declared output hashes."""
    _fixture(tmp_path, monkeypatch)
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    parent = (tmp_path / "old.txt").read_text()
    derivative = (tmp_path / "derivative.txt").read_text().replace("Cloud 12", "Cloud 99")
    html = (tmp_path / "primary.html").read_text()
    raw = (tmp_path / "raw.txt").read_bytes()
    table = provenance["replacements"][0]
    table["source_cells"][0]["text"] = "Cloud 99"
    table["source_value_to_output"][0]["formatted_value"] = "Cloud 99"
    table["derivative_table_text_sha256"] = sha256(
        derivative[table["derivative_unicode_span"][0]:table["derivative_unicode_span"][1]].encode())
    with pytest.raises(InputValidationError, match="declared text differs from saved HTML cell"):
        _validate_tables(provenance, parent, derivative, html, raw)


def test_secondary_document_html_cannot_be_labeled_primary():
    """Identical visible text in an exhibit does not establish primary identity."""
    primary = b"<html><table><tr><td>Cloud 12</td></tr></table></html>"
    secondary = b"<html><table><tr><td><span>Cloud 12</span></td></tr></table></html>"
    raw = (b"<DOCUMENT>\n<TYPE>10-Q\n<FILENAME>main.htm\n<TEXT>" + primary +
           b"</TEXT>\n</DOCUMENT>\n<DOCUMENT>\n<TYPE>EX-99\n<FILENAME>ex.htm\n<TEXT>" +
           secondary + b"</TEXT>\n</DOCUMENT>")
    _verify_primary_html(raw, primary)
    with pytest.raises(InputValidationError, match="not the exact raw primary"):
        _verify_primary_html(raw, secondary)
