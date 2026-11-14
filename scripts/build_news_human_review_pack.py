"""Build a private, blank human-adjudication pack from the fixed N1 sample.

Article text and row-level AI drafts remain under the repository's ignored
artifacts/ directory. The output is a review aid, not completed human labels.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs/news_company_event_contract_v1.json"
GUIDE = ROOT / "docs/news_research/event_annotation_template.md"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(sample: Path, source_manifest: Path, ai_draft: Path, ai_receipt: Path,
          output_dir: Path) -> dict:
    artifacts = (ROOT / "artifacts").resolve()
    out = output_dir.resolve()
    if out == artifacts or not out.is_relative_to(artifacts):
        raise ValueError("OUTPUT_MUST_BE_UNDER_IGNORED_ARTIFACTS")
    if out.exists():
        raise FileExistsError(out)
    source = json.loads(source_manifest.read_text(encoding="utf-8"))
    ai_meta = json.loads(ai_receipt.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sample_sha = file_hash(sample)
    ai_sha = file_hash(ai_draft)
    if sample_sha != source["sample"]["sha256"] or sample_sha != ai_meta["source_sample_sha256"]:
        raise ValueError("SAMPLE_HASH_MISMATCH")
    if ai_sha != ai_meta["v2_annotation_sha256"]:
        raise ValueError("AI_DRAFT_HASH_MISMATCH")
    for item in contract["files"].values():
        if file_hash(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("CONTRACT_FILE_HASH_MISMATCH")
    header = source["result"]["header"]
    if len(header) != len(set(header)):
        raise ValueError("DUPLICATE_SOURCE_HEADER")
    rows = []
    with sample.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                values = dict(zip(header, row["fields"], strict=True))
                if values["Article"].strip():
                    rows.append((row["source_row_ordinal"], values))
            if len(rows) == 10:
                break
    if len(rows) != 10 or [ordinal for ordinal, _ in rows] != list(range(1, 11)):
        raise ValueError("FIRST_TEN_SOURCE_ORDER_NOT_AVAILABLE")
    drafts = [json.loads(line) for line in ai_draft.read_text(encoding="utf-8").splitlines() if line.strip()]
    grouped: dict[int, list[dict]] = defaultdict(list)
    bodies = {ordinal: fields["Article"] for ordinal, fields in rows}
    for draft in drafts:
        ref = draft["article_version_ref"]
        ordinal = ref["source_row_ordinal"]
        if ordinal not in bodies:
            raise ValueError("AI_DRAFT_OUTSIDE_FIRST_TEN")
        body = bodies[ordinal]
        if ref["body_sha256"] != hashlib.sha256(body.encode("utf-8")).hexdigest():
            raise ValueError("AI_DRAFT_BODY_HASH_MISMATCH")
        for span in draft["evidence_location"].values():
            if span is not None and not (0 <= span["char_start"] < span["char_end"] <= len(body)):
                raise ValueError("AI_DRAFT_SPAN_OUT_OF_BOUNDS")
        grouped[ordinal].append(draft)
    if len(drafts) != ai_meta["draft_records"]:
        raise ValueError("AI_DRAFT_COUNT_MISMATCH")

    out.mkdir(parents=True)
    blank_rows = []
    article_sections = []
    draft_sections = []
    for position, (ordinal, fields) in enumerate(rows, start=1):
        body = fields["Article"]
        body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        ref = {
            "dataset": source["source"]["dataset"],
            "dataset_revision": source["source"]["revision"],
            "source_row_ordinal": ordinal,
            "source_article_version_id": None,
            "body_sha256": body_sha,
            "normalization_applied": False,
            "normalization_mapping_ref": None,
        }
        blank_rows.append({
            "input_binding": ref,
            "human_reviewer_id": None,
            "human_reviewed_at": None,
            "human_article_disposition": None,
            "human_article_unknown_reason": None,
            "human_market_outcome_reported": None,
            "human_date_conflict": None,
            "human_identity_conflict": None,
            "human_company_events": None,
            "human_evidence_spans": None,
            "human_notes": None,
            "human_adjudication_status": None,
            "human_disagreement_with_ai": None,
        })
        (out / f"body_{position:02d}.txt").write_bytes(body.encode("utf-8"))
        meta = (
            f"Source position {position}; source row ordinal {ordinal}; "
            f"stored Date: {fields['Date']}; source ticker candidate: {fields['Stock_symbol']}; "
            f"original body SHA-256: {body_sha}"
        )
        article_sections.append(
            f"<section><h2>Article {position:02d}</h2><p>{html.escape(meta)}</p>"
            f"<p>Source URL: {html.escape(fields['Url'])}</p>"
            f"<h3>Title</h3><pre>{html.escape(fields['Article_title'])}</pre>"
            f"<h3>Original Article field</h3><pre>{html.escape(body)}</pre>"
            f"<p>For exact Unicode offsets, open <code>body_{position:02d}.txt</code>; "
            "count Python-style characters in that unnormalized text.</p>"
            "<h3>Blank human adjudication</h3>"
            "<p>Reviewer ID: ______ &nbsp; Review date: ______ &nbsp; Article disposition: ______</p>"
            "<p>Event × target company: ______ &nbsp; Evidence [start,end): ______ &nbsp; "
            "Business impact: ______ &nbsp; Market outcome: ______</p>"
            "<p>Date conflict: ______ &nbsp; Identity conflict: ______ &nbsp; "
            "Unknown reason: ______ &nbsp; Disagreement/change note: ______</p></section>"
        )
        draft_json = json.dumps(grouped[ordinal], ensure_ascii=False, indent=2)
        draft_sections.append(
            f"<section><h2>Article {position:02d} — AI draft only</h2><pre>{html.escape(draft_json)}</pre></section>"
        )

    styles = "<style>body{font:16px/1.5 sans-serif;max-width:1000px;margin:auto;padding:2rem}section{border-top:2px solid #888;padding:2rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f6f6;padding:1rem}</style>"
    review_html = (
        "<!doctype html><meta charset='utf-8'><title>Human review first</title>" + styles +
        "<h1>01 — Read original text and fill blank fields first</h1>"
        "<p>This fixed first-ten batch is a development set. These are source Article fields, "
        "not verified publisher-complete originals. Source dates and tickers are candidates only. "
        "Do not open 02_AI_DRAFT_REFERENCE.html until an initial independent pass is complete. "
        "All human decisions start blank in human_adjudication_blank.jsonl.</p>"
        "<p>One article may yield multiple event × company records or a no-event/no-supported-company disposition. "
        "Separate business impact, already reported market outcomes and unknown future returns. "
        "Use exact original-body Unicode [start,end) spans. Indirect impacts need relationship evidence. "
        "Use NO_CLEAR_IMPACT, UNKNOWN and MIXED distinctly; do not infer a competitor's negative effect by default. "
        "Date/identity flags are diagnostics, not automatic financial exclusion rules.</p>" +
        "".join(article_sections)
    )
    (out / "01_REVIEW_FIRST.html").write_text(review_html, encoding="utf-8", newline="\n")
    ai_html = (
        "<!doctype html><meta charset='utf-8'><title>AI drafts separate</title>" + styles +
        "<h1>02 — Existing AI assistant drafts (not human gold)</h1>"
        "<p>Open only after completing an independent review. These drafts may be wrong and "
        "must never prefill human fields. Compare after review and record disagreements separately.</p>" +
        "".join(draft_sections)
    )
    (out / "02_AI_DRAFT_REFERENCE.html").write_text(ai_html, encoding="utf-8", newline="\n")
    (out / "human_adjudication_blank.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in blank_rows),
        encoding="utf-8", newline="\n"
    )
    (out / "change_log_template.csv").write_text(
        "source_row_ordinal,reviewer_id,changed_at,rule_version,field,old_value,new_value,reason\n",
        encoding="utf-8", newline="\n"
    )
    (out / "disagreements_template.csv").write_text(
        "source_row_ordinal,reviewer_id,compared_at,field,human_value,ai_draft_value,resolution,reason\n",
        encoding="utf-8", newline="\n"
    )
    instructions = (
        "# Human adjudication batch: start here\n\n"
        "1. Open `01_REVIEW_FIRST.html` and read the original saved Article field. "
        "Fill a copy of `human_adjudication_blank.jsonl` independently; all human fields are null/empty.\n"
        "2. Cite original-body Unicode character offsets `[start,end)` using the matching `body_NN.txt`. "
        "The body files have no added newline or normalization. Verify each proposed span by slicing the original body.\n"
        "3. Only after the initial pass, open `02_AI_DRAFT_REFERENCE.html`. "
        "Record differences in `disagreements_template.csv`, and rule changes in `change_log_template.csv`.\n"
        "4. This first-ten source-order batch is development material, not a blind test or financial sample. "
        "No human adjudication has been performed by generating this pack.\n"
    )
    (out / "README_FIRST.md").write_text(instructions, encoding="utf-8", newline="\n")

    file_entries = []
    for path in sorted(out.iterdir()):
        if path.is_file():
            file_entries.append({"file": path.name, "bytes": path.stat().st_size, "sha256": file_hash(path)})
    receipt = {
        "schema": "news_n1_human_review_pack_v1",
        "source_sample_sha256": sample_sha,
        "source_manifest_sha256": file_hash(source_manifest),
        "ai_draft_v2_sha256": ai_sha,
        "guide_sha256": file_hash(GUIDE),
        "schema_sha256": contract["files"]["schema"]["sha256"],
        "prompt_sha256": contract["files"]["prompt"]["sha256"],
        "reviewed_article_count": 10,
        "ai_draft_record_count": len(drafts),
        "fixed_order_source_row_ordinals": list(range(1, 11)),
        "development_set": True,
        "human_adjudication_status": "NOT_STARTED",
        "human_fields_prefilled": False,
        "files": file_entries,
    }
    (out / "batch_manifest.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sample", "source-manifest", "ai-draft", "ai-receipt", "output-dir"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    receipt = build(args.sample, args.source_manifest, args.ai_draft, args.ai_receipt, args.output_dir)
    print(json.dumps({"reviewed_article_count": receipt["reviewed_article_count"],
                      "ai_draft_record_count": receipt["ai_draft_record_count"],
                      "human_adjudication_status": receipt["human_adjudication_status"]}))


if __name__ == "__main__":
    main()
