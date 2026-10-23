"""Run the existing read-only protobuf normalizer against retained matches."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess


def run(source, normalizer, output):
    output.mkdir(parents=True, exist_ok=False)
    kinds, responses = Counter(), Counter()
    records = []
    for line in source.read_text().splitlines():
        row = json.loads(line)
        fmt = row['original_metadata'].get('rawFormat')
        kinds[str(fmt)] += 1
        if str(fmt).startswith('yellowstone_'):
            records.append({'signature': row['signature'], 'rawBase64': row['raw_base64']})
    with (output/'normalized.jsonl').open('x') as destination:
        result = subprocess.run([str(normalizer), '--all'], input=''.join(json.dumps(r)+'\n' for r in records),
                                text=True, stdout=destination, stderr=subprocess.PIPE, timeout=90)
    for line in (output/'normalized.jsonl').read_text().splitlines():
        row = json.loads(line)
        responses['decoded' if row.get('ok') else str(row.get('error')).split(':')[0]] += 1
    report = {'schema': 'RETAINED_METADATA_CAPABILITY_V1', 'formats': dict(kinds),
              'protobuf_inputs': len(records), 'normalizer_exit': result.returncode,
              'results': dict(responses), 'native_availability_certified': False,
              'scope': 'Existing decoder capability only; original raw frames preserved. No backdating.'}
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True); p.add_argument('--normalizer', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(run(a.input, a.normalizer, a.output)))
