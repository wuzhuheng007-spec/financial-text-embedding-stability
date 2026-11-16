"""Package only reviewed public v1.5 code and redacted receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MEMBERS=[
 'apps/quant_portal/README.md',
 'apps/quant_portal/core/middleware.py','apps/quant_portal/core/models.py',
 'apps/quant_portal/core/personal_feed_cache.py','apps/quant_portal/core/urls.py',
 'apps/quant_portal/core/views.py','apps/quant_portal/core/tests.py',
 'apps/quant_portal/core/migrations/0006_userpreferences_display_language.py',
 'apps/quant_portal/ops/analysis_personal.ps1','apps/quant_portal/ops/start_personal.ps1',
 'apps/quant_portal/ops/status_personal.ps1','apps/quant_portal/ops/stop_personal.ps1',
 'apps/quant_portal/static/core/i18n_v15.js','apps/quant_portal/static/core/workspace_v15.js',
 'apps/quant_portal/static/core/personal_feed.js','apps/quant_portal/static/core/personal_timeline.js',
 'apps/quant_portal/static/core/portal.css','apps/quant_portal/templates/core/base.html',
 'apps/quant_portal/templates/core/dashboard.html',
 'scripts/quant_personal_feed/relevance_v15.py','scripts/quant_personal_feed/collector.py',
 'scripts/quant_personal_feed/store.py','scripts/quant_personal_feed/analysis_contract_v15.py',
 'scripts/quant_personal_feed/analysis_contract_v15b.py',
 'scripts/quant_personal_feed/analysis_v15.py','scripts/quant_personal_feed/analysis_v15b.py',
 'scripts/quant_personal_feed/generate_v15.py','scripts/quant_personal_feed/generate_v15b.py',
 'scripts/quant_personal_feed/review_v15b.py','scripts/quant_personal_feed/audit_v15b.py',
 'scripts/quant_personal_feed/runtime_v15.py','scripts/quant_personal_feed/freeze_v15.py',
 'scripts/quant_personal_feed/view_v15.py','scripts/quant_personal_feed/backfill_v15.py',
 'tests/test_quant_personal_v15.py','configs/quant_portal_personal_v15_20261116.json',
 'docs/news_research/README.md','docs/news_research/STATUS.json',
 'docs/news_research/CHANGELOG.md','docs/quant_portal/personal_user_guide.zh.md',
 'reports/2026-11-16_quant_portal_personal_v15.en.md',
 'reports/2026-11-16_quant_portal_personal_v15.zh.md',
 'results/quant_portal_personal_v15_20261116/plan_execution.csv',
 'results/quant_portal_personal_v15_20261116/freeze_redacted.json',
 'results/quant_portal_personal_v15_20261116/analysis_batch_redacted.json',
 'results/quant_portal_personal_v15_20261116/legacy_backfill_redacted.json',
 'results/quant_portal_personal_v15_20261116/model_trial_v15a_redacted.json',
 'results/quant_portal_personal_v15_20261116/model_run_redacted.json',
 'results/quant_portal_personal_v15_20261116/semantic_review_redacted.json',
 'results/quant_portal_personal_v15_20261116/runtime_redacted.json',
 'results/quant_portal_personal_v15_20261116/focused_tests.log',
 'results/quant_portal_personal_v15_20261116/django_tests.log',
 'results/quant_portal_personal_v15_20261116/js_syntax_checks.log',
]


def build(output:Path)->dict:
    output.mkdir(parents=True,exist_ok=True)
    folder=output/'quant_personal_demo_v15_20261116'
    folder.mkdir(exist_ok=True)
    manifest=[]
    for rel in MEMBERS:
        if any(part in {'var','local_only','backups'} for part in Path(rel).parts):
            raise ValueError('Private path in package')
        src=ROOT/rel
        if not src.is_file(): raise FileNotFoundError(src)
        data=src.read_bytes()
        if src.suffix in {'.md','.json','.csv','.js','.py','.html','.ps1'}:
            data.decode('utf-8-sig')
        dst=folder/rel
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_bytes(data)
        manifest.append({'path':rel,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    readme=folder/'HANDOFF_README.md'
    readme.write_text('# Personal Demo v1.5 handoff\n\nRead the Chinese or English report, then plan_execution.csv and the redacted receipts. Source code is for continued work. Run only on an authorized local account. The package contains no source news bodies, prices, private model responses, databases, credentials, or screenshots. Financial v2.5 remains REJECTED_CANDIDATE. Browser visual acceptance for v1.5 is NOT_RUN.\n',encoding='utf-8')
    manifest.append({'path':'HANDOFF_README.md','sha256':hashlib.sha256(readme.read_bytes()).hexdigest(),'bytes':readme.stat().st_size})
    (folder/'MANIFEST.json').write_text(json.dumps({'schema':'v15_public_handoff','members':manifest},indent=2)+'\n',encoding='utf-8')
    archive=output/'quant_personal_demo_v15_20261116.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for entry in manifest:
            z.write(folder/entry['path'],entry['path'])
        z.write(folder/'MANIFEST.json','MANIFEST.json')
    with zipfile.ZipFile(archive) as z:
        bad=z.testzip()
        if bad: raise ValueError('CRC failure: '+bad)
        for entry in manifest:
            if hashlib.sha256(z.read(entry['path'])).hexdigest()!=entry['sha256']:
                raise ValueError('ZIP member hash mismatch')
    return {'folder':str(folder),'zip':str(archive),'members':len(manifest)+1,
            'zip_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'crc':'PASS'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path)
    print(json.dumps(build(p.parse_args().output),indent=2))
