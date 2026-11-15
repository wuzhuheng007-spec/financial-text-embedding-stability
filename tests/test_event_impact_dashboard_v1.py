"""Focused source, arithmetic, reference and rendering risks for impact cards."""

from __future__ import annotations

import pytest

from scripts import build_event_impact_dashboard_v1 as impact


def test_saved_source_facts_and_edges_replay():
    manifest, bundles, facts = impact.load_and_validate()
    assert [item["case"] for item in bundles] == list(impact.CASES)
    assert len(manifest["events"]) == 3
    assert len(facts) == 44
    assert sum(len(item["edges"]) for item in bundles) == 9
    assert facts["CPI_F08"]["raw_value"] is None
    assert facts["CPI_F08"]["evidence_status"] == "NOT_DISCLOSED"


def test_derived_values_replay_named_source_inputs():
    _, _, facts = impact.load_and_validate()
    specs = impact.read_json(impact.DATA / "calculation_specs.json")["calculations"]
    results = {row["calculation_id"]: row for row in impact.calculate(specs, facts)}
    assert results["A_D06"]["value"] == 21341
    assert results["B_CALC01"]["value"] == 14511
    assert results["B_CALC02"]["value"] == 2
    assert results["CPI_D01"]["value"] == pytest.approx(-0.2)
    assert results["CPI_D02"]["value"] == 0
    assert all(row["input_fact_ids"] and row["formula"] for row in results.values())


def test_calculation_rejects_missing_input_zero_denominator_and_cross_event():
    facts = {"a": {"raw_value": 1, "event_id": "E1"},
             "b": {"raw_value": 0, "event_id": "E1"},
             "c": {"raw_value": 2, "event_id": "E2"}}
    base = {"calculation_id": "bad", "event_id": "E1", "operation": "percent_change",
            "input_fact_ids": ["a", "b"], "unit": "percent", "comparability_note": "synthetic"}
    with pytest.raises(ValueError, match="Zero denominator"):
        impact.calculate([base], facts)
    with pytest.raises(ValueError, match="Unknown calculation input"):
        impact.calculate([dict(base, input_fact_ids=["a", "missing"])], facts)
    with pytest.raises(ValueError, match="Cross-event"):
        impact.calculate([dict(base, input_fact_ids=["a", "c"])], facts)


def test_missing_value_and_known_zero_are_distinct():
    assert "unknown" in impact._fact_value({"raw_value": None, "evidence_status": "NOT_DISCLOSED"})
    assert impact._fact_value({"raw_value": 0, "unit": "percent", "evidence_status": "SOURCE_SUPPORTED"}) == "0 %"
    assert impact._fact_tag({"evidence_status": "NOT_DISCLOSED"}) == "来源缺口 / SOURCE_MISSING"


def test_render_escapes_user_visible_fields_and_separates_time(monkeypatch):
    monkeypatch.setattr(impact, "render_v2", lambda feed: "<html><style></style><nav></nav><main></main><footer></footer></html>")
    fact = {"fact_id": "f1", "description_zh": "<script>alert(1)</script>",
            "description_en": "test", "raw_value": 0, "unit": "percent", "period": "P",
            "accounting_basis": "reported", "evidence_status": "SOURCE_SUPPORTED",
            "source_url": "https://example.org/source", "locator": "paragraph 1", "short_quote": "zero"}
    card = {"title": "<script>alert(2)</script>", "happened": "fact", "new_info": "unknown",
            "affected": [], "pathways": [], "conditions": [], "counterforces": [],
            "watch": [], "unknowns": [], "key_fact_ids": ["f1"]}
    bundle = {"case": "a_nvidia", "event": {"event_id": "E", "published_at": "2026-01-01",
              "event_time_note_zh": "date only", "retrieved_at_utc": "2026-01-02T00:00:00Z",
              "analysis_created_at_utc": "2026-01-03T00:00:00Z", "highlight_fact_ids": ["f1"]},
              "facts": {"facts": [fact]}, "zh": card, "en": card, "edges": []}
    page = impact.render_impact({}, [bundle], [], None)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "事件时间依据" in page and "材料获取" in page and "分析制作" in page
    assert "开发分析草稿，非预测结果" in page
    assert "新闻语气: NOT_SCORED" in page and "实际价格反应: NOT_RUN" in page
