"""Frozen 20-item local speaker-locked evidence retry. Raw sources and responses stay private."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v15c import build_spans
from .analysis_contract_v15d import PROMPT_VERSION, SYSTEM, mask_speakers, parse_and_validate
from .generate_v15b import EXPECTED_CONFIG, EXPECTED_REVISION, EXPECTED_TOKENIZER

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / 'apps/quant_portal/var/v15d_speaker_lock_retry_20261116'
PUBLIC = ROOT / 'results/quant_portal_personal_v15d_20261116'
OLD_BATCH = ROOT / 'apps/quant_portal/var/v15_20261116T0431Z/analysis_batch_private.json'
FEED = ROOT / 'apps/quant_portal/var/personal_feeds.sqlite3'
RUNTIME = ROOT / 'apps/quant_portal/var/analysis_v15b_model_runtime.json'
CONFIG = PUBLIC / 'run_config.json'
INPUT = PRIVATE / 'input_manifest.json'
HASHED_CODE = [
    'scripts/quant_personal_feed/analysis_contract_v15b.py',
    'scripts/quant_personal_feed/analysis_contract_v15c.py',
    'scripts/quant_personal_feed/analysis_contract_v15d.py',
    'scripts/quant_personal_feed/generate_v15d.py',
    'scripts/quant_personal_feed/generate_v15b.py',
    'scripts/quant_personal_feed/preflight_v15d.py',
    'scripts/quant_personal_feed/run_v15d_batch.py',
]


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode('utf-8')


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def code_hashes() -> dict[str, str]:
    return {rel: digest((ROOT / rel).read_bytes()) for rel in HASHED_CODE}


def config() -> dict:
    return {'schema': 'quant_personal_news_v15d_run_config',
            'prompt_version': PROMPT_VERSION, 'prompt_sha256': digest(SYSTEM.encode('utf-8')),
            'model_revision': EXPECTED_REVISION,
            'model_config_sha256': EXPECTED_CONFIG,
            'tokenizer_sha256': EXPECTED_TOKENIZER,
            'planned_items': 20, 'max_attempts_per_item': 2,
            'max_source_characters': 1600, 'max_input_tokens': 2048,
            'max_new_tokens': 768, 'seed': 1729, 'do_sample': False,
            'model_load_timeout_seconds': 240,
            'generation_timeout_seconds': 180,
            'semantic_review': 'CODEX_REVIEW_SAME_TEAM',
            'scope': 'REPEATED_DEVELOPMENT_NEWS_NOT_INDEPENDENT_TEST'}


def prepare() -> None:
    if INPUT.exists() or CONFIG.exists():
        raise FileExistsError('Frozen v15d input or configuration already exists')
    old = json.loads(OLD_BATCH.read_text(encoding='utf-8'))
    if len(old['items']) != 20:
        raise ValueError('Expected exactly the old 20-item development batch')
    rows = []
    with sqlite3.connect(FEED.resolve().as_uri()+'?mode=ro', uri=True) as db:
        for index, item in enumerate(old['items'], 1):
            result = db.execute('''SELECT title,content,content_sha256 FROM news_versions
              WHERE item_key=? AND version=?''',
              (item['item_key'], item['version'])).fetchone()
            if not result or result[2] != item['content_sha256']:
                raise ValueError(f'Frozen source version changed at item {index}')
            spans, metadata = build_spans(result[0], result[1],
                                max_source_characters=config()['max_source_characters'])
            if metadata['omitted_spans']:
                raise ValueError(f'Complete span omitted at item {index}; revise before freeze')
            masked, mapping = mask_speakers(spans)
            rows.append({'index': index, 'item_key': item['item_key'],
                         'version': item['version'], 'content_sha256': item['content_sha256'],
                         'event_group_id': item['event_group_id'],
                         'spans': masked, 'original_spans': spans,
                         'speaker_mapping': mapping, 'span_metadata': metadata})
    frozen = {'schema': 'private_v15d_frozen_inputs', 'prepared_at_utc': stamp(),
              'source_batch_sha256': digest(OLD_BATCH.read_bytes()),
              'code_hashes': code_hashes(), 'config_sha256': digest(json_bytes(config())),
              'items': rows}
    write_json(INPUT, frozen)
    write_json(CONFIG, config())
    write_json(PUBLIC / 'input_manifest_redacted.json', {
        'schema': 'v15d_input_manifest_redacted',
        'prepared_at_utc': frozen['prepared_at_utc'],
        'source_batch_sha256': frozen['source_batch_sha256'],
        'private_manifest_sha256': digest(INPUT.read_bytes()),
        'config_sha256': frozen['config_sha256'],
        'code_hashes': frozen['code_hashes'],
        'items': [{'index': x['index'], 'item_key': x['item_key'],
                   'version': x['version'], 'content_sha256': x['content_sha256'],
                   'event_group_id': x['event_group_id'],
                   'source_sha256': x['span_metadata']['source_sha256'],
                   'selected_sha256': x['span_metadata']['selected_sha256'],
                   'speaker_lock_count': len(x['speaker_mapping']),
                   'span_count': x['span_metadata']['span_count'],
                   'selected_characters': x['span_metadata']['selected_characters']}
                  for x in rows]})
    print(json.dumps({'prepared': len(rows), 'private_manifest_sha256': digest(INPUT.read_bytes()),
                      'code_hashes_captured': len(frozen['code_hashes'])}))


def kill_child(proc: subprocess.Popen) -> bool:
    if proc.poll() is None:
        if os.name == 'nt':
            result = subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'],
                                    capture_output=True, timeout=15)
            if result.returncode != 0 and proc.poll() is None:
                return False
        else:
            proc.kill()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        return False
    return proc.poll() is not None


def generate(request: Path, response: Path, runtime: dict, cfg: dict) -> tuple[dict | None, str | None, float]:
    env = os.environ.copy()
    env['QUANT_V15_MODEL_PYTHON'] = runtime['python_exe']
    env['QUANT_V15_MODEL_DIR'] = runtime['model_dir']
    started = time.monotonic()
    proc = subprocess.Popen([runtime['python_exe'], '-m',
               'scripts.quant_personal_feed.generate_v15d', '--request', str(request),
               '--response', str(response)], cwd=ROOT, stdin=subprocess.DEVNULL,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    loaded = request.with_suffix('.loaded')
    loaded.unlink(missing_ok=True)
    deadline = started + cfg['model_load_timeout_seconds']
    while proc.poll() is None and not loaded.exists() and time.monotonic() < deadline:
        time.sleep(.25)
    if proc.poll() is None and not loaded.exists():
        return None, ('MODEL_LOAD_TIMEOUT_PROCESS_TERMINATED' if kill_child(proc)
                      else 'MODEL_LOAD_TIMEOUT_PROCESS_UNCONFIRMED'), time.monotonic()-started
    if loaded.exists() and proc.poll() is None:
        try:
            proc.wait(timeout=cfg['generation_timeout_seconds'])
        except subprocess.TimeoutExpired:
            return None, ('GENERATION_TIMEOUT_PROCESS_TERMINATED' if kill_child(proc)
                          else 'GENERATION_TIMEOUT_PROCESS_UNCONFIRMED'), time.monotonic()-started
    elapsed = time.monotonic()-started
    if proc.returncode or not response.exists():
        return None, 'MODEL_PROCESS_FAILED', elapsed
    try:
        return json.loads(response.read_text(encoding='utf-8')), None, elapsed
    except (json.JSONDecodeError, OSError):
        return None, 'MODEL_RESPONSE_FILE_INVALID', elapsed


def run(max_new_items: int) -> None:
    frozen = json.loads(INPUT.read_text(encoding='utf-8'))
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    if frozen['code_hashes'] != code_hashes() or frozen['config_sha256'] != digest(CONFIG.read_bytes()):
        raise ValueError('Frozen code or configuration changed')
    runtime = json.loads(RUNTIME.read_text(encoding='utf-8'))
    if not Path(runtime['python_exe']).is_file() or not Path(runtime['model_dir']).is_dir():
        raise RuntimeError('Pinned local model runtime unavailable')
    new_count = 0
    for item in frozen['items']:
        index = item['index']
        result_path = PRIVATE / f'item_{index:02d}_result.json'
        if result_path.exists():
            continue
        if new_count >= max_new_items:
            break
        attempts = []
        selected = None
        status = 'FAILED'
        fatal = False
        for attempt in (1, 2):
            request = PRIVATE / f'item_{index:02d}_attempt_{attempt}.request.json'
            response = PRIVATE / f'item_{index:02d}_attempt_{attempt}.response.json'
            write_json(request, {'spans': item['spans'], 'repair': attempt == 2,
                 'model_revision': cfg['model_revision'], 'seed': cfg['seed'],
                 'max_input_tokens': cfg['max_input_tokens'],
                 'max_new_tokens': cfg['max_new_tokens']})
            output, error, elapsed = generate(request, response, runtime, cfg)
            raw = output.get('raw') if isinstance(output, dict) else None
            check = None
            if not error and isinstance(output, dict) and output.get('error'):
                error = output['error']
            if not error and not isinstance(raw, str):
                error = 'MODEL_RAW_MISSING'
            if not error:
                try:
                    obj, display, check, mode, view = parse_and_validate(
                        raw, item['spans'], item['original_spans'], item['speaker_mapping'])
                    if check['valid']:
                        selected = {'object': display, 'raw_object': obj,
                                    'evidence': check['evidence'],
                                    'placeholder_expansion': check['placeholder_expansion'],
                                    'parse_mode': mode, 'parsed_view_sha256': digest(view.encode('utf-8')),
                                    'raw_sha256': digest(raw.encode('utf-8'))}
                        status = 'MECHANICAL_COMPLETE'
                    else:
                        error = 'CONTRACT_INVALID'
                except (ValueError, json.JSONDecodeError) as exc:
                    error = 'PARSE_' + type(exc).__name__
            attempts.append({'attempt': attempt, 'request_sha256': digest(request.read_bytes()),
                             'response_sha256': digest(response.read_bytes()) if response.exists() else None,
                             'raw_sha256': digest(raw.encode('utf-8')) if isinstance(raw, str) else None,
                             'error_class': error, 'validation_errors': check['errors'] if check else [],
                             'elapsed_seconds': round(elapsed, 3),
                             'input_tokens': output.get('input_tokens') if isinstance(output, dict) else None,
                             'generated_tokens': output.get('generated_tokens') if isinstance(output, dict) else None})
            if selected:
                break
            if error in {'MODEL_PROCESS_FAILED', 'MODEL_RESPONSE_FILE_INVALID',
                         'MODEL_LOAD_TIMEOUT_PROCESS_TERMINATED',
                         'MODEL_LOAD_TIMEOUT_PROCESS_UNCONFIRMED',
                         'GENERATION_TIMEOUT_PROCESS_TERMINATED',
                         'GENERATION_TIMEOUT_PROCESS_UNCONFIRMED',
                         'INPUT_TOKEN_BUDGET'}:
                fatal = True
                break
        write_json(result_path, {'index': index, 'item_key': item['item_key'],
            'version': item['version'], 'content_sha256': item['content_sha256'],
            'input_selected_sha256': item['span_metadata']['selected_sha256'],
            'model_revision': cfg['model_revision'], 'prompt_version': cfg['prompt_version'],
            'completed_at_utc': stamp(), 'status': status, 'attempts': attempts,
            'selected': selected})
        new_count += 1
        print(json.dumps({'item': index, 'status': status, 'attempts': len(attempts),
                          'fatal': fatal}), flush=True)
        if fatal:
            break
    audit()


def audit() -> None:
    frozen = json.loads(INPUT.read_text(encoding='utf-8'))
    results = [json.loads(p.read_text(encoding='utf-8')) for p in
               sorted(PRIVATE.glob('item_*_result.json'))]
    errors = collections.Counter(e for row in results for attempt in row['attempts']
                                 for e in attempt['validation_errors'])
    process_errors = collections.Counter(a['error_class'] or 'NONE' for row in results
                                         for a in row['attempts'])
    write_json(PUBLIC / 'run_redacted.json', {
        'schema': 'v15d_speaker_lock_retry_redacted', 'audited_at_utc': stamp(),
        'private_manifest_sha256': digest(INPUT.read_bytes()),
        'config_sha256': digest(CONFIG.read_bytes()),
        'code_hashes_at_audit': code_hashes(),
        'planned_items': len(frozen['items']), 'attempted_items': len(results),
        'generation_attempts': sum(len(x['attempts']) for x in results),
        'item_status': dict(collections.Counter(x['status'] for x in results)),
        'attempt_error_class': dict(process_errors),
        'validation_error_fields': dict(errors),
        'semantic_review_status': 'NOT_RUN',
        'scope': 'REPEATED_DEVELOPMENT_NEWS_NOT_INDEPENDENT_TEST'})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run', 'audit'])
    parser.add_argument('--max-new-items', type=int, default=20)
    args = parser.parse_args()
    if args.action == 'prepare': prepare()
    elif args.action == 'run': run(args.max_new_items)
    else: audit()


if __name__ == '__main__':
    main()
