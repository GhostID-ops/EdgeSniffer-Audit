"""Bounded read-only schema inventory; prints keys/counts, never raw records."""
import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path

FILES = ('native-yellowstone-pump-transaction-receipts-v1.jsonl',
         'native-pump-account-state-events.jsonl',
         'native-pump-fee-config-snapshots-v1.jsonl')


def inspect(path):
    if not path.exists():
        return {'present': False}
    end = path.stat().st_size
    start = max(0, end - 4*1024*1024)
    with path.open('rb') as f:
        f.seek(start)
        if start:
            f.readline()
        effective_start = f.tell()
        data = f.read(max(0, end-effective_start))
    keys, nested, clocks, counts = Counter(), {}, {}, Counter()
    for blob in deque(data.splitlines(keepends=True), maxlen=200):
        if not blob.endswith(b'\n'):
            counts['incomplete_tail'] += 1
            continue
        try:
            row = json.loads(blob)
            if not isinstance(row, dict):
                counts['non_object'] += 1
                continue
        except (ValueError, TypeError):
            counts['invalid_json'] += 1
            continue
        counts['sample_rows'] += 1
        keys.update(row.keys())
        for key, value in row.items():
            if isinstance(value, dict):
                nested.setdefault(key, Counter()).update(value.keys())
            if type(value) is int and any(x in key.lower() for x in ('ms', 'ns', 'slot')):
                bounds = clocks.setdefault(key, [value, value])
                bounds[0], bounds[1] = min(bounds[0], value), max(bounds[1], value)
        execution = row.get('execution')
        if isinstance(execution, dict) and type(execution.get('success')) is bool:
            counts['explicit_success' if execution['success'] else 'explicit_failure'] += 1
    return {'present': True, 'file_prefix_bytes': end, 'sample_start_byte': effective_start,
            'sample_bytes_sha256': hashlib.sha256(data).hexdigest(),
            'counts': dict(counts), 'top_level_keys': dict(keys),
            'nested_keys': {k: dict(v) for k,v in nested.items()}, 'clock_ranges': clocks}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--file', action='append', type=Path, default=[],
                   help='Additional explicit retained artifact to inspect (keys/counts only)')
    p.add_argument('--output', type=Path, help='Optional new immutable report file')
    a = p.parse_args()
    result = {'schema': 'RETAINED_SOURCE_INVENTORY_V1',
        'sampling': 'last <=200 complete lines within final <=4MiB prefix per source; not a coverage estimate',
        'sources': {name: inspect(a.root/name) for name in FILES},
        'additional_sources': {str(path): inspect(path) for path in a.file}}
    if a.output:
        with a.output.open('x', encoding='utf-8') as out:
            out.write(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, sort_keys=True))
