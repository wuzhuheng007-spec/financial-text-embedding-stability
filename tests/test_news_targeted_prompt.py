"""Contract checks for the single targeted wire-v2 prompt candidate."""

import hashlib
import json
from pathlib import Path
import sys


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from render_news_wire_label_table import render_label_table  # noqa: E402
from run_news_n1_targeted import render_messages  # noqa: E402


SCHEMA = REPO / "docs/news_research/company_event_wire_v2.schema.json"
PROMPT = REPO / "docs/news_research/company_event_prompt_v4_targeted.md"


def _section(text: str, start: str, end: str) -> str:
    return text.split(start + "\n", 1)[1].split("\n" + end, 1)[0]


def test_allowed_labels_are_derived_from_pinned_schema() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    prompt = PROMPT.read_text(encoding="utf-8")
    assert _section(prompt, "BEGIN_ALLOWED_LABELS", "END_ALLOWED_LABELS") == render_label_table(schema)
    assert "records[].unknown_reason: " in prompt
    assert "records[].evidence_spans[].role: " in prompt


def test_complete_wire_schema_is_in_model_system_section() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    prompt = PROMPT.read_text(encoding="utf-8")
    system = _section(prompt, "## System message", "## User message template")
    assert json.loads(_section(system, "BEGIN_WIRE_SCHEMA", "END_WIRE_SCHEMA")) == schema
    assert "{{ARTICLE_INPUT_JSON}}" not in system
    assert "input_binding" in system  # explicit instruction to omit it from model output


def test_actual_renderer_sends_schema_and_label_table_to_system_message() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    prompt = PROMPT.read_text(encoding="utf-8")
    messages = render_messages(prompt, {
        "source_date": "2001-01-02",
        "source_ticker_candidate": "ZZZ",
        "title": "Invented headline",
        "body": "Invented article body.",
    })
    assert [message["role"] for message in messages] == ["system", "user"]
    assert json.loads(_section(messages[0]["content"], "BEGIN_WIRE_SCHEMA", "END_WIRE_SCHEMA")) == schema
    assert _section(messages[0]["content"], "BEGIN_ALLOWED_LABELS", "END_ALLOWED_LABELS") == render_label_table(schema)
    assert "Invented article body." not in messages[0]["content"]
    assert "Invented article body." in messages[1]["content"]


def test_targeted_rules_keep_event_type_free_text_and_no_after_the_fact_repair() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    event_type = schema["$defs"]["company_event"]["properties"]["event_type"]
    assert event_type == {"type": "string", "minLength": 1}
    prompt = PROMPT.read_text(encoding="utf-8")
    assert "event_type, event_description, target_company_text and inference_premise are free text" in prompt
    assert "Do not add fields, replace strings with booleans, normalize labels after generation" in prompt
    assert "Do not quote from the title, paraphrase, prepend rewritten wording" in prompt


def test_v5_config_changes_only_the_recorded_version_and_runtime_policy() -> None:
    old_bytes = (REPO / "configs/news_n1_smoke_v4_grounded.json").read_bytes()
    old = json.loads(old_bytes)
    new = json.loads((REPO / "configs/news_n1_smoke_v5_targeted.json").read_bytes())

    def leaves(obj: dict, prefix: str = "") -> dict:
        result = {}
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            result.update(leaves(value, path) if isinstance(value, dict) else {path: value})
        return result

    old_leaves, new_leaves = leaves(old), leaves(new)
    changed = {key for key in old_leaves | new_leaves if old_leaves.get(key) != new_leaves.get(key)}
    assert changed == {
        "schema", "created_date", "purpose", "contract.prompt_path", "contract.prompt_sha256",
        "pipeline_version", "runtime_policy", "experiment_id", "baseline_commit",
        "baseline_config_sha256",
    }
    assert new["contract"]["prompt_sha256"] == hashlib.sha256(PROMPT.read_bytes()).hexdigest()
    assert new["baseline_config_sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert new["model_revision"] == old["model_revision"]
    assert new["generation"] == old["generation"]
    assert new["budget"] == old["budget"]
