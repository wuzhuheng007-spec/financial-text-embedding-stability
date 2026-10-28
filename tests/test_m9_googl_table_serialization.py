"""Synthetic software fixtures for GOOGL profiles, never research results."""
from __future__ import annotations

import pytest

from scripts.m9_serialize_googl_tables import serialize_table
from scripts.m9_serialize_tables import SerializationError


def simple_two_year() -> str:
    return ("<table><tr><td></td></tr><tr><td>Three Months Ended</td></tr>"
            "<tr><td>March 31,</td></tr><tr><td>2024</td><td>2025</td></tr>"
            "<tr><td>Income</td><td>$</td><td>10</td><td>$</td><td>20</td></tr>"
            "<tr><td>Margin</td><td>12</td><td>%</td><td>13</td><td>%</td></tr></table>")


def test_simple_source_year_columns_and_signs() -> None:
    text, detail = serialize_table(simple_two_year(), "MDA-2")
    assert "20242025" not in text
    assert "period: Three Months Ended March 31,; year: 2024] = $ 10" in text
    assert "period: Three Months Ended March 31,; year: 2025] = 13 %" in text
    assert len(detail["source_value_to_output"]) == 4
    assert detail["header_evidence"]["association_status"].endswith("HUMAN_PENDING")


def test_sparse_mda1_keeps_cells_without_guessing_a_header_match() -> None:
    rows = ["<tr><td></td></tr>", "<tr><td>Three Months Ended</td></tr>",
            "<tr><td>March 31,</td></tr>",
            "<tr><td>2024</td><td>2025</td><td>$ Change</td><td>% Change</td></tr>",
            "<tr><td>Income</td><td>$</td><td>10</td><td>$</td><td>20</td><td>$</td><td>10</td><td>100</td><td>%</td></tr>",
            "<tr><td>Constant currency (1)</td><td>14</td><td>%</td></tr>"]
    rows.extend("<tr><td></td></tr>" for _ in range(6))
    rows.append("<tr><td>Margin</td><td>32</td><td>%</td><td>34</td><td>%</td><td>2</td><td>%</td></tr>")
    rows.extend("<tr><td></td></tr>" for _ in range(4))
    text, detail = serialize_table("<table>" + "".join(rows) + "</table>", "MDA-1")
    assert "Constant currency (1)" in text and "Margin" in text
    assert "source grid Column" in text
    assert "year: 2024]" not in text
    assert detail["source_value_to_output"] == []
    assert len(detail["source_cell_to_output"]) > 0
    assert detail["header_evidence"]["association_status"].startswith("UNRESOLVED")


def test_complex_mda5_preserves_sparse_rows_and_no_zero_imputation() -> None:
    rows = ["<tr><td></td></tr>", "<tr><td>Three Months Ended March 31, 2025</td></tr>",
            "<tr><td>% Change from Prior Period</td></tr>",
            "<tr>" + "".join(f"<td>{x}</td>" for x in
                ("Three Months Ended March 31,", "Less FX Effect", "Constant Currency Revenues",
                 "As Reported", "Less Hedging Effect", "Less FX Effect", "Constant Currency Revenues")) + "</tr>",
            "<tr><td>2024</td><td>2025</td></tr>",
            "<tr><td>US</td><td>10</td><td>20</td><td>0</td><td>20</td><td>13</td><td>%</td><td>0</td><td>%</td><td>13</td><td>%</td></tr>"]
    rows.extend("<tr><td></td></tr>" for _ in range(4))
    rows.append("<tr><td>Hedging</td><td>1</td><td>2</td></tr>")
    rows.append("<tr><td>Total (1)</td><td>10</td><td>20</td><td>0</td><td>%</td></tr>")
    text, detail = serialize_table("<table>" + "".join(rows) + "</table>", "MDA-5")
    assert "Hedging" in text and "Total (1)" in text
    assert "[blank source spacer row; no observation]" in text
    assert detail["source_value_to_output"] == []
    assert len(detail["source_cell_to_output"]) > 0
    assert detail["header_evidence"]["association_status"].startswith("UNRESOLVED")


def test_one_column_percentage_has_no_invented_year() -> None:
    html = ("<table><tr><td></td></tr><tr><td>Google Search &amp; other</td></tr>"
            "<tr><td>Clicks</td><td>(5)</td><td>%</td></tr>"
            "<tr><td>Cost</td><td>7</td><td>%</td></tr>"
            "<tr><td>Google Network</td></tr>"
            "<tr><td>Impressions</td><td>2</td><td>%</td></tr>"
            "<tr><td>Cost</td><td>4</td><td>%</td></tr></table>")
    text, detail = serialize_table(html, "MDA-3")
    assert "[percentage; no year in table] = (5) %" in text
    assert "year: 2024" not in text
    assert detail["header_evidence"]["source_year_header"] is None
    assert all("output_char_span" in row for row in detail["source_value_to_output"])


def test_single_period_shares_amount_not_two_years() -> None:
    html = ("<table><tr><td></td></tr><tr><td>Three Months Ended March 31, 2025</td></tr>"
            "<tr><td>Shares</td><td>Amount</td></tr>"
            "<tr><td>Class A</td><td>15</td><td>$</td><td>2,766</td></tr>"
            "<tr><td>Class C</td><td>68</td><td>12,535</td></tr></table>")
    text, detail = serialize_table(html, "MDA-14")
    assert "measure: Shares] = 15" in text
    assert "measure: Amount] = $ 2,766" in text
    assert "year: 2024" not in text
    assert len(detail["source_value_to_output"]) == 4


def test_changed_two_year_profile_fails_closed() -> None:
    bad = simple_two_year().replace("<td>2025</td>", "<td>Unknown</td>")
    with pytest.raises(SerializationError, match="source header profile mismatch"):
        serialize_table(bad, "MDA-2")
