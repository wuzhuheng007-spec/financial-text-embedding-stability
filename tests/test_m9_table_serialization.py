"""Synthetic software fixtures for the source-grid serializer, not research data."""
from __future__ import annotations

import pytest

from scripts.m7_build_10q_review import source_rendered
from scripts.m9_serialize_tables import (Profile, SerializationError, build_derivative,
                                         check_adjacent_notes, parse_table, serialize_table, sha)


def one_group_table() -> str:
    return ("<table><tr><td></td></tr>"
            "<tr><td colspan='4'>Three Months Ended<br>March 31,</td></tr>"
            "<tr><td></td><td>2024</td><td>2025</td></tr>"
            "<tr><td>Revenue</td><td>$</td><td>10</td><td>$</td><td>20</td></tr>"
            "<tr><td>Change</td><td>(5)</td><td>8</td></tr>"
            "<tr><td>Rate</td><td>12</td><td>%</td><td>13</td><td>%</td></tr></table>")


def test_adjacent_years_currency_negative_percent_and_cell_breaks() -> None:
    text, detail = serialize_table(one_group_table(), "MDA-X", Profile((2,)))
    assert "Three Months Ended March 31," in text
    assert "20242025" not in text
    assert "year: 2024] = $ 10" in text
    assert "year: 2025] = $ 20" in text
    assert "year: 2024] = (5)" in text
    assert "year: 2025] = 13 %" in text
    assert len(detail["source_value_to_output"]) == 6
    assert all("html_fragment_sha256" in cell for cell in detail["source_cells"])


def test_multi_level_period_groups_and_subheaders() -> None:
    multi = ("<table><tr><td></td></tr>"
             "<tr><td>Three Months Ended<br>March 31,</td>"
             "<td>Twelve Months Ended<br>March 31,</td></tr>"
             "<tr><td>2024</td><td>2025</td><td>2024</td><td>2025</td></tr>"
             "<tr><td>Cash flow:</td></tr>"
             "<tr><td>Operations</td><td>1</td><td>2</td><td>3</td><td>4</td></tr></table>")
    text, detail = serialize_table(multi, "MDA-X", Profile((2, 2)))
    assert "Column 3 [period: Twelve Months Ended March 31,; year: 2024] = 3" in text
    assert [row["formatted_value"] for row in detail["source_value_to_output"]] == ["1", "2", "3", "4"]

    fx = ("<table><tr><td></td></tr>"
          "<tr><td>Three Months Ended<br>March 31,</td></tr>"
          "<tr><td>2024</td><td>2025</td></tr>"
          "<tr><td>As<br>Reported</td><td>Exchange<br>Effect (1)</td><td>Prior Rates (2)</td>"
          "<td>As Reported</td><td>Exchange Effect (1)</td><td>Prior Rates (2)</td></tr>"
          "<tr><td>Income</td><td>10</td><td>(2)</td><td>8</td><td>20</td><td>(3)</td><td>17</td></tr></table>")
    text, detail = serialize_table(fx, "MDA-X", Profile((2,), 3))
    assert "year: 2025; basis: Exchange Effect (1)] = (3)" in text
    assert "As Reported" in text
    assert len(detail["source_value_to_output"]) == 6


def test_rowspan_colspan_covered_and_blank_cells_do_not_become_zeros() -> None:
    rows = parse_table("<table><tr><td rowspan='2' colspan='2'>Label</td><td></td></tr>"
                       "<tr><td>7</td></tr></table>")
    assert rows[0][0]["grid_column_span"] == [1, 3]
    assert rows[1][0]["grid_column_span"] == [3, 4]
    assert rows[0][1]["text"] == ""
    assert all(cell["text"] != "0" for row in rows for cell in row)


def test_nested_tables_and_inconsistent_headers_fail_closed() -> None:
    with pytest.raises(ValueError, match="Nested HTML tables"):
        parse_table("<table><tr><td><table><tr><td>1</td></tr></table></td></tr></table>")
    bad = one_group_table().replace("<td>2025</td>", "<td>unknown year</td>", 1)
    with pytest.raises(SerializationError, match="year header"):
        serialize_table(bad, "MDA-X", Profile((2,)))


def test_only_table_span_changes_and_external_note_is_preserved() -> None:
    fragment = one_group_table()
    old = source_rendered(fragment)
    prefix = "Unit: millions.\n\n"
    suffix = "\n\n(1) Original external note.\nOther unchanged paragraph."
    section = prefix + old + suffix
    html = "<div>Before</div>" + fragment + "<div>After</div>"
    a = len(prefix)
    h0 = html.index(fragment)
    note = "(1) Original external note."
    note_start = section.index(note)
    index = {"tables": [{"table_id": "MDA-X", "source_table_index": 1,
                         "html_source_char_start": h0, "html_source_char_end_exclusive": h0 + len(fragment),
                         "html_source_byte_start": h0, "html_source_byte_end_exclusive": h0 + len(fragment),
                         "complete_submission_byte_start": h0,
                         "complete_submission_byte_end_exclusive": h0 + len(fragment),
                         "raw_html_fragment_sha256": sha(fragment.encode()),
                         "flattened_section_char_start": a,
                         "flattened_section_char_end_exclusive": a + len(old),
                         "flattened_text": old, "flattened_text_sha256": sha(old.encode()),
                         "adjacent_text_note_candidates": [{"label": "(1)",
                             "normalized_section_char_span": [note_start, note_start + len(note)],
                             "text_sha256": sha(note.encode()),
                             "status": "ADJACENT_NOTE_CANDIDATE_NEEDS_HUMAN_REVIEW"}]}]}
    derivative, replacements, unchanged = build_derivative(section, html, index,
                                                            {"MDA-X": Profile((2,))})
    assert derivative.startswith(prefix)
    assert derivative.endswith(suffix)
    assert len(replacements) == 1 and len(unchanged) == 2
    notes = check_adjacent_notes(section, derivative, index, unchanged)
    assert len(notes) == 1 and notes[0]["text_preserved_exactly"]
    assert "(1) Original external note." in derivative
