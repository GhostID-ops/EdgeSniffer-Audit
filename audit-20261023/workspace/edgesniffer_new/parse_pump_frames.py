import json
import struct
import subprocess

targets = {
    '2JQsnjCzrKs7WmYKiR98kYY42WXzDzS3ChqbjFkuQHjnFZ2Tz8VY5u4JhbKz1T2Ngxy9Fn4REqG1S4oLNVbb4C1g',
    '2Drx5nGGP796T2ezeRDHWuB6V5NWajzk9v3QdFhCJA6fxKc3tdzo3MYr4ctFttpm1HUo5MHxWJvSUtxk9KuLP1VL',
    '2wyWHGvtP2AdExKMcKNpxAajknVTAEtDF8Sfdbkn3QP9ohBDJVhrVzdHDvukL1A4sKD1WEtHSdc1CJpVvdF7Unbe',
    '3p3C6gi86PJSBksT9hfq3NR1nGdUghx9jsNsxtwArK9U2ktfk6k4p8E9r9DPwaFucGpwShMEJ9SyxHQst5pGKEeu',
    '4AQDpfW7GtYbb23eFzdU2qHgWTMvSZFLZyjL2nhJBV1yCcmfv5TZhyNopAx5Pn4TkpETfZZdTxwQCE69m7iBtYXm',
}
paths = [
    '/var/lib/pump-exact-evidence-ledger-v1/partitions/2026/09/22/18/evidence.frames.zst',
    '/var/lib/pump-exact-evidence-ledger-v1/partitions/2026/09/22/19/evidence.frames.zst',
]
found = {}
for path in paths:
    proc = subprocess.Popen(['/usr/bin/zstd', '-dc', path], stdout=subprocess.PIPE)
    assert proc.stdout is not None
    first = True
    while True:
        if first:
            magic = proc.stdout.read(8)
            if magic != b'PEL1FRM\x00':
                raise RuntimeError(f'bad magic {magic!r} in {path}')
            first = False
        header = proc.stdout.read(8)
        if len(header) != 8:
            break
        size, _frame_header = struct.unpack('<II', header)
        payload = proc.stdout.read(size)
        if len(payload) != size:
            raise RuntimeError('short frame payload')
        row = json.loads(payload)
        raw_size = _frame_header
        raw_payload = proc.stdout.read(raw_size)
        if len(raw_payload) != raw_size:
            raise RuntimeError('short raw payload')
        checksum = proc.stdout.read(32)
        if len(checksum) != 32:
            raise RuntimeError('short raw checksum')
        sig = row.get('signature')
        if sig in targets:
            found[sig] = {
                'path': path,
                'keys': sorted(row),
                'row': row,
            }
    code = proc.wait()
    if code:
        raise SystemExit(code)
print(json.dumps(found, sort_keys=True, default=str))
