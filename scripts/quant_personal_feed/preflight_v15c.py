"""Tokenizer-only context check for the frozen evidence-ID batch."""
from __future__ import annotations

import hashlib
import json
import os

from .analysis_contract_v15c import messages
from .run_v15c_batch import CONFIG, INPUT, PUBLIC, RUNTIME, write_json
from .generate_v15b import EXPECTED_REVISION, EXPECTED_TOKENIZER


def main() -> None:
    runtime = json.loads(RUNTIME.read_text(encoding='utf-8'))
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    frozen = json.loads(INPUT.read_text(encoding='utf-8'))
    model_dir = runtime['model_dir']
    from pathlib import Path
    if Path(model_dir).name != EXPECTED_REVISION:
        raise RuntimeError('Model revision mismatch')
    if hashlib.sha256((Path(model_dir)/'tokenizer.json').read_bytes()).hexdigest() != EXPECTED_TOKENIZER:
        raise RuntimeError('Tokenizer mismatch')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
    results = []
    for item in frozen['items']:
        counts = {}
        for repair in (False, True):
            prompt = tok.apply_chat_template(messages(item['spans'], repair=repair),
                                             tokenize=False, add_generation_prompt=True)
            counts['repair' if repair else 'first'] = len(tok(prompt, add_special_tokens=False).input_ids)
        results.append({'index': item['index'], 'input_tokens': counts,
                        'within_budget': max(counts.values()) <= cfg['max_input_tokens']})
    write_json(PUBLIC/'token_preflight_redacted.json', {
        'schema': 'v15c_tokenizer_preflight_redacted', 'model_revision': EXPECTED_REVISION,
        'max_input_tokens': cfg['max_input_tokens'], 'items': results,
        'all_within_budget': all(x['within_budget'] for x in results)})
    if not all(x['within_budget'] for x in results):
        raise RuntimeError('At least one input exceeds frozen budget')
    print(json.dumps({'items': len(results), 'max_tokens': max(max(x['input_tokens'].values()) for x in results),
                      'all_within_budget': True}))


if __name__ == '__main__':
    main()
