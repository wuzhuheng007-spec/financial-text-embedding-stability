"""Offline diagnostics only. Input/output with article text MUST stay private.

python diagnose_wire_failures.py --input PRIVATE.json --schema wire_v2.json \
    --private-out PRIVATE_DETAILS.json --public-out aggregate.json

Input: list of {ordinal, body, body_sha256, response_text}. The caller exports
unaltered saved bodies/responses and records their original file hashes.
No model calls, output repair, canonical construction or network access.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator

PINNED_SCHEMA_SHA = 'afc517b5c6ef96e06d89380905e51843cecc6b34aa31888c5ca14e0ee07b2022'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def known_properties(schema):
    names = set()
    def walk(x):
        if isinstance(x, dict):
            names.update(x.get('properties', {}).keys())
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(schema)
    return names

def diagnose(rows, schema):
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    allowed = known_properties(schema)
    counts, errors, patterns, quotes = Counter(), Counter(), Counter(), Counter()
    private, ordinals = [], set()
    for row in rows:
        ordinal = row['ordinal']
        if type(ordinal) is not int or ordinal < 1 or ordinal in ordinals:
            raise ValueError('invalid or duplicate ordinal')
        ordinals.add(ordinal)
        body, raw = row['body'], row['response_text']
        if not isinstance(body, str) or not isinstance(raw, str):
            raise ValueError('body and response_text must be strings')
        if digest(body.encode('utf-8')) != row['body_sha256']:
            raise ValueError('body hash mismatch')
        counts['responses'] += 1
        record = {'ordinal': ordinal, 'body_sha256': row['body_sha256'],
                  'response_text_sha256': digest(raw.encode('utf-8')), 'errors': [], 'quotes': []}
        private.append(record)
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            counts['json_invalid'] += 1
            record['parse_error'] = {'line': exc.lineno, 'column': exc.colno, 'message': exc.msg}
            continue
        counts['json_valid'] += 1
        issues = list(validator.iter_errors(obj))
        counts['wire_invalid' if issues else 'wire_valid'] += 1
        for e in issues:
            # Public paths reveal only schema property names, never model keys.
            safe_path = '/' + '/'.join('*' if isinstance(p, int) else
                    (p if p in allowed else '<unknown-key>') for p in e.absolute_path)
            keyword = str(e.validator)
            errors[(safe_path, keyword)] += 1
            record['errors'].append({'path': list(e.absolute_path), 'schema_path': list(e.absolute_schema_path),
                                    'keyword': keyword, 'value': e.instance, 'constraint': e.validator_value})
            if keyword == 'enum':
                if isinstance(e.instance, str) and any(isinstance(v, str) and e.instance.casefold() == v.casefold()
                                                       for v in e.validator_value):
                    patterns['case_only'] += 1
                elif not isinstance(e.instance, str):
                    patterns['non_string'] += 1
                else:
                    patterns['other_unlisted_string'] += 1
        if issues:
            continue  # Match v4 stage denominators; no bypass of invalid wire.
        containers = [('/article_evidence', obj['article_evidence'])]
        for i, event in enumerate(obj['records']):
            for key in ('evidence_spans', 'relationship_evidence_spans'):
                containers.append((f'/records/{i}/{key}', event[key]))
        span_count = 0
        for path, spans in containers:
            for i, span in enumerate(spans):
                q = span['text']
                first = body.find(q)
                status = 'NOT_FOUND' if first < 0 else ('AMBIGUOUS' if body.find(q, first + 1) >= 0 else 'UNIQUE_EXACT')
                quotes[status] += 1
                span_count += 1
                record['quotes'].append({'path': f'{path}/{i}', 'role': span['role'], 'text': q, 'status': status})
        if not span_count:
            counts['wire_valid_no_quotes'] += 1
    public = {'schema': 'wire_failure_diagnostic_v1', 'model_calls': 0,
              'counts': dict(counts), 'error_instances': sum(errors.values()),
              'error_groups': [{'path': p, 'keyword': k, 'instances': n} for (p,k),n in sorted(errors.items())],
              'enum_patterns': dict(patterns), 'quotes_from_wire_valid_only': dict(quotes),
              'limitations': ['No repair or canonical construction.', 'Quotes are not independent articles or semantic accuracy.',
                              'Raw values and response-level details are private.']}
    return public, private

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'schema', 'private-out', 'public-out'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    paths = [args.input.resolve(), args.schema.resolve(), args.private_out.resolve(), args.public_out.resolve()]
    if len(set(paths)) != 4 or args.private_out.exists() or args.public_out.exists():
        raise ValueError('Use distinct paths and new output filenames')
    source = args.schema.read_bytes()
    if digest(source) != PINNED_SCHEMA_SHA:
        raise ValueError('unexpected wire Schema version')
    raw = args.input.read_bytes()
    public, private = diagnose(json.loads(raw), json.loads(source))
    public['input_export_sha256'] = digest(raw)
    public['wire_schema_sha256'] = digest(source)
    private_bytes = (json.dumps(private, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    public['private_details_sha256'] = digest(private_bytes)
    args.private_out.write_bytes(private_bytes)
    args.public_out.write_text(json.dumps(public, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(public))

if __name__ == '__main__':
    main()
