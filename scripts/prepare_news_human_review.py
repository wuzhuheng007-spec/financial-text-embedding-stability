"""Prepare a private, article-first and unlabelled N1 human review workspace.

This tool never reads or embeds model responses. Generated pages contain original
saved article text and blank manual fields; all pages must remain in ignored
``artifacts/``. It does not submit or upload reviews.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = REPO / "artifacts/news_n1_20261114/upstream_nasdaq_bf9189c_run1/raw_fields_with_body.jsonl"
DEFAULT_MANIFEST = REPO / "artifacts/news_n1_20261114/upstream_nasdaq_bf9189c_run1/local_manifest.json"
DEFAULT_CONFIG = REPO / "configs/news_n1_smoke_v4_grounded.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_first_ten(sample: Path, manifest_path: Path, config_path: Path) -> tuple[list[dict], dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    sample_digest = sha256(sample)
    if (sample_digest != manifest["sample"]["sha256"]
            or sample_digest != config["source"]["sample_sha256"]
            or sha256(manifest_path) != config["source"]["sample_manifest_sha256"]):
        raise ValueError("SOURCE_SAMPLE_OR_MANIFEST_HASH_MISMATCH")
    header = manifest["result"]["header"]
    needed = {"Date", "Article_title", "Stock_symbol", "Article"}
    if not needed.issubset(header) or len(set(header)) != len(header):
        raise ValueError("SOURCE_HEADER_INVALID")
    rows = []
    with sample.open("r", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            if len(item["fields"]) != len(header):
                raise ValueError("SOURCE_FIELD_WIDTH_MISMATCH")
            fields = dict(zip(header, item["fields"]))
            ordinal = item["source_row_ordinal"]
            if ordinal != len(rows) + 1 or not fields["Article"].strip():
                raise ValueError("FIRST_TEN_SOURCE_ORDER_OR_BODY_INVALID")
            rows.append({"ordinal": ordinal, "fields": fields})
            if len(rows) == 10:
                break
    if len(rows) != 10:
        raise ValueError("FIRST_TEN_ARTICLES_UNAVAILABLE")
    return rows, {"sample_sha256": sample_digest,
                  "sample_manifest_sha256": sha256(manifest_path),
                  "config_sha256": sha256(config_path),
                  "dataset_revision": config["source"]["dataset_revision"]}


def article_page(row: dict, *, body_sha256: str) -> str:
    ordinal = row["ordinal"]
    fields = row["fields"]
    title = html.escape(fields["Article_title"], quote=True)
    body = html.escape(fields["Article"], quote=True)
    date = html.escape(fields["Date"], quote=True)
    ticker = html.escape(fields["Stock_symbol"], quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Manual article review {ordinal:02d}</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#17212b}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:1rem;border-radius:.4rem}}
label{{display:block;font-weight:600;margin-top:1rem}}textarea,input,select{{display:block;box-sizing:border-box;width:100%;font:inherit;padding:.45rem}}
fieldset{{margin:1.5rem 0;border:1px solid #aab6c2}}legend{{font-weight:700}}button{{font:inherit;padding:.5rem 1rem}}</style></head>
<body><nav><a href="index.html">Review index</a></nav><h1>Article {ordinal:02d}: {title}</h1>
<p><strong>Read the original article before entering any decision.</strong> No AI draft or prior adjudication is loaded.</p>
<pre id="original-article">{body}</pre>
<details><summary>Source metadata (open only if needed)</summary><p>Date as supplied: {date}</p>
<p>Source ticker candidate, not a verified historical security identity: {ticker}</p></details>
<form id="manual-review" data-ordinal="{ordinal}" data-body-sha256="{body_sha256}">
<fieldset><legend>Blank manual adjudication / 人工裁定</legend>
<label for="reviewer_id">Reviewer identifier</label><input id="reviewer_id" name="reviewer_id" autocomplete="off">
<label for="prior_model_exposure">Had you seen any model output for this article before this review?</label>
<select id="prior_model_exposure" name="prior_model_exposure"><option value="">Select / 未填</option><option value="yes">Yes / 是</option><option value="no">No / 否</option><option value="uncertain">Uncertain / 不确定</option></select>
<label for="exposure_note">If yes or uncertain, describe what you saw</label><textarea id="exposure_note" name="exposure_note" rows="2"></textarea>
<label for="target_company">Target company named in the article</label><textarea id="target_company" name="target_company" rows="2"></textarea>
<label for="event_description">Company event stated by the article</label><textarea id="event_description" name="event_description" rows="3"></textarea>
<label for="company_role">Company role</label><select id="company_role" name="company_role"><option value="">Select / 未填</option><option value="direct">Direct / 直接</option><option value="indirect">Indirect / 间接</option><option value="unclear">Unclear / 不明</option></select>
<label for="impact_direction">Impact direction for this company</label><select id="impact_direction" name="impact_direction"><option value="">Select / 未填</option><option value="positive">Positive / 正面</option><option value="negative">Negative / 负面</option><option value="mixed">Mixed / 混合</option><option value="unknown">Unknown / 无法判断</option></select>
<label for="statement_status">Article statement or reviewer inference?</label><select id="statement_status" name="statement_status"><option value="">Select / 未填</option><option value="explicit">Explicit / 明确陈述</option><option value="inferred">Inferred / 推断</option><option value="unclear">Unclear / 不明</option></select>
<label for="evidence_quote">Exact evidence quotation from the original article</label><textarea id="evidence_quote" name="evidence_quote" rows="3"></textarea>
<label for="quote_support">Does that quotation support the company, event and direction?</label><select id="quote_support" name="quote_support"><option value="">Select / 未填</option><option value="yes">Yes / 支持</option><option value="partial">Partial / 部分支持</option><option value="no">No / 不支持</option><option value="unclear">Unclear / 不明</option></select>
<label for="unable_to_judge_reason">If unable to judge, explain why</label><textarea id="unable_to_judge_reason" name="unable_to_judge_reason" rows="2"></textarea>
<label for="review_note">Other uncertainty or multiple-event note</label><textarea id="review_note" name="review_note" rows="3"></textarea>
</fieldset><button type="button" id="export">Download this manual review as JSON</button></form>
<script>document.getElementById('export').addEventListener('click',()=>{{
const form=document.getElementById('manual-review');const values=Object.fromEntries(new FormData(form).entries());
const record={{schema:'news_n1_manual_review_entry_v1',source_row_ordinal:Number(form.dataset.ordinal),body_sha256:form.dataset.bodySha256,
reviewer_provenance:{{prior_model_exposure:values.prior_model_exposure||null,exposure_note:values.exposure_note||null,reviewer_id:values.reviewer_id||null}},
adjudication:{{target_company:values.target_company||null,event_description:values.event_description||null,company_role:values.company_role||null,
impact_direction:values.impact_direction||null,statement_status:values.statement_status||null,evidence_quote:values.evidence_quote||null,
quote_support:values.quote_support||null,unable_to_judge_reason:values.unable_to_judge_reason||null,review_note:values.review_note||null}}}};
const blob=new Blob([JSON.stringify(record,null,2)],{{type:'application/json'}});const url=URL.createObjectURL(blob);const a=document.createElement('a');
a.href=url;a.download='manual_review_{ordinal:02d}.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}});</script></body></html>"""


def build(sample: Path, manifest: Path, config: Path, output_dir: Path,
          public_receipt: Path | None = None) -> dict:
    rows, source = load_first_ten(sample, manifest, config)
    artifacts_root = (REPO / "artifacts").resolve()
    output_dir = output_dir.resolve()
    if output_dir == artifacts_root or not output_dir.is_relative_to(artifacts_root):
        raise ValueError("REVIEW_OUTPUT_MUST_BE_IGNORED_ARTIFACTS")
    if output_dir.exists():
        raise FileExistsError("REVIEW_OUTPUT_ALREADY_EXISTS")
    output_dir.mkdir(parents=True)
    manifest_rows = []
    blank_rows = []
    links = []
    for row in rows:
        ordinal = row["ordinal"]
        body_digest = hashlib.sha256(row["fields"]["Article"].encode("utf-8")).hexdigest()
        name = f"article_{ordinal:02d}.html"
        page = output_dir / name
        page.write_text(article_page(row, body_sha256=body_digest), encoding="utf-8", newline="\n")
        manifest_rows.append({"ordinal": ordinal, "body_sha256": body_digest,
                              "page": name, "page_sha256": sha256(page)})
        blank_rows.append({"schema": "news_n1_manual_review_entry_v1",
                           "source_row_ordinal": ordinal, "body_sha256": body_digest,
                           "reviewer_provenance": {"reviewer_id": None,
                                                   "prior_model_exposure": None,
                                                   "exposure_note": None},
                           "adjudication": {key: None for key in
                                            ("target_company", "event_description", "company_role",
                                             "impact_direction", "statement_status", "evidence_quote",
                                             "quote_support", "unable_to_judge_reason", "review_note")}})
        links.append(f'<li><a href="{name}">Article {ordinal:02d}</a></li>')
    (output_dir / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Private N1 manual review</title>'
        '<body><h1>Private article-first review</h1><p>Open each original article before any model draft. '
        'All labels are blank; disclose prior model exposure. Files stay local.</p><ol>'
        + "".join(links) + '</ol></body></html>\n', encoding="utf-8", newline="\n")
    template = output_dir / "blank_review_template.jsonl"
    template.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in blank_rows),
                        encoding="utf-8", newline="\n")
    private_manifest = output_dir / "review_manifest.json"
    private_manifest.write_text(json.dumps({"schema": "news_n1_manual_review_manifest_v1",
                                            **source, "rows": manifest_rows,
                                            "index_sha256": sha256(output_dir / "index.html"),
                                            "template_sha256": sha256(template),
                                            "ai_draft_embedded": False,
                                            "adjudications_prefilled": False},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = {"schema": "news_n1_human_review_preparation_v1",
               "date": "2026-11-15", "timezone": "Asia/Shanghai",
               "development_articles": len(rows), **source,
               "review_code_sha256": sha256(Path(__file__)),
               "private_manifest_sha256": sha256(private_manifest),
               "ai_draft_embedded": False, "adjudications_prefilled": False,
               "annotation_completed": 0,
               "interpretation": "Local article-first blank review entry only; prior model exposure must be disclosed by each reviewer. No quality result."}
    if public_receipt is not None:
        public_receipt.parent.mkdir(parents=True, exist_ok=True)
        if public_receipt.exists():
            raise FileExistsError("PUBLIC_RECEIPT_ALREADY_EXISTS")
        public_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--public-receipt", type=Path)
    args = parser.parse_args()
    receipt = build(args.sample, args.manifest, args.config, args.output_dir,
                    args.public_receipt)
    print(json.dumps({"development_articles": receipt["development_articles"],
                      "ai_draft_embedded": receipt["ai_draft_embedded"],
                      "annotation_completed": receipt["annotation_completed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
