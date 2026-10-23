"""Bounded historical recovery with explicit later availability. Read-only RPC."""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import time
import urllib.request

from transaction_evidence_v1 import digest, normalize


def rpc(url, method, params):
    request = urllib.request.Request(url, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
                                     {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if payload.get('error'):
        # Never store provider messages containing endpoint credentials.
        return None, 'RPC_ERROR_' + str(payload['error'].get('code', 'UNKNOWN'))
    return payload.get('result'), None


def read_endpoint(path, name):
    for line in path.read_text().splitlines():
        if line.startswith(name+'='):
            return line.split('=', 1)[1].strip().strip('"').strip("'")
    raise ValueError('configured endpoint variable missing')


def frames(path):
    process = subprocess.Popen(['zstd', '-dc', str(path)], stdout=subprocess.PIPE) if path.suffix == '.zst' else None
    stream = process.stdout if process else path.open('rb')
    try:
        if stream.read(8) != b'PEL1FRM\0':
            raise ValueError('invalid frame magic')
        while True:
            prefix = stream.read(8)
            if not prefix:
                break
            if prefix == b'PEL1FRM\0':
                continue
            if len(prefix) != 8:
                raise ValueError('truncated frame')
            m, n = struct.unpack('<II', prefix)
            if m+n > 128*1024*1024:
                raise ValueError('oversized frame')
            body = stream.read(m+n)
            checksum = stream.read(32)
            if hashlib.sha256(body).digest() != checksum:
                raise ValueError('frame checksum mismatch')
            yield json.loads(body[:m]), body[m:]
    finally:
        stream.close()
        if process and process.wait() != 0:
            raise ValueError('archive decompression failed')


def run(receipts, output, archives, endpoint=None, limit=32):
    output.mkdir(parents=True, exist_ok=False)
    blob = receipts.read_bytes()
    rows = [json.loads(x) for x in blob.splitlines()]
    by_signature = {}
    for i, row in enumerate(rows, 1):
        if row.get('signature'):
            by_signature.setdefault(row['signature'], []).append((i, row))
    inventory, retained = [], {}
    with (output/'retained-matches.jsonl').open('x') as out:
        for archive in archives:
            count, matched, kinds = 0, 0, Counter()
            try:
                for metadata, raw in frames(archive):
                    count += 1
                    kinds[str(metadata.get('rawFormat'))] += 1
                    sig = metadata.get('signature')
                    if sig in by_signature:
                        matched += 1
                        retained.setdefault(sig, []).append(metadata.get('rawFormat'))
                        out.write(json.dumps({'signature': sig, 'original_metadata': metadata,
                                              'raw_base64': base64.b64encode(raw).decode(),
                                              'raw_sha256': hashlib.sha256(raw).hexdigest(),
                                              'source': str(archive), 'archive_frame_index': count,
                                              'recovery_indexed_at_ns': time.time_ns(),
                                              'original_decision_availability': 'UNPROVEN'})+'\n')
                error = None
            except (ValueError, OSError) as exc:
                error = type(exc).__name__
            inventory.append({'path': str(archive), 'bytes': archive.stat().st_size, 'frames': count,
                              'matched_frames': matched, 'formats': dict(kinds), 'error': error})
    # Hash-ranked before outcomes: deterministic bounded capability sample, not
    # recovery of every signature and not a strategy sample.
    selected = sorted(by_signature, key=lambda s: hashlib.sha256(s.encode()).hexdigest())[:limit] if endpoint else []
    counts = Counter()
    with (output/'rpc-recovery.jsonl').open('x') as out:
        for sig in selected:
            began = time.time_ns()
            try:
                tx, error = rpc(endpoint, 'getTransaction', [sig, {'encoding': 'json', 'commitment': 'finalized',
                                                                  'maxSupportedTransactionVersion': 1}])
            except Exception as exc:
                tx, error = None, type(exc).__name__
            ended = time.time_ns()
            view = normalize(tx, sig) if isinstance(tx, dict) else None
            status = 'RECOVERED' if view and 'signature_missing_or_mismatch' not in view['gaps'] else 'ERROR' if error else 'NOT_FOUND_OR_UNSUPPORTED'
            counts[status] += 1
            if view:
                counts['execution_'+view['execution_status']] += 1
                counts['complete_instruction_identity'] += view['instruction_identity_complete']
                counts['pump_trade_event_prefixes'] += len(view['pump_trade_event_prefixes'])
            out.write(json.dumps({'schema': 'HISTORICAL_RECEIPT_RECOVERY_V1', 'signature': sig,
                                  'status': status, 'error': error, 'source_receipt_lines': [x[0] for x in by_signature[sig]],
                                  'original_receive_ns': [x[1].get('native_receive_unix_ns') for x in by_signature[sig]],
                                  'recovery_request_ns': began, 'recovery_response_ns': ended,
                                  'metadata_available_at_ns': ended, 'commitment': 'finalized',
                                  'usable_at_original_decision': False, 'raw_rpc_result': tx, 'view': view})+'\n')
            out.flush()
            time.sleep(0.2)  # bounded offline RPC, never hot path
    report = {'schema': 'RECEIPT_RECOVERY_REPORT_V1', 'receipt_rows': len(rows),
              'unique_signatures': len(by_signature), 'input_sha256': hashlib.sha256(blob).hexdigest(),
              'archives': inventory, 'retained_unique_matches': len(retained), 'rpc_sample_size': len(selected),
              'rpc_counts': dict(counts), 'rpc_unqueried_signatures': len(by_signature)-len(selected),
              'sampling': 'lowest SHA256(signature), outcome-blind capability sample',
              'native_decision_metadata_recovered': 0, 'economics_computed': False,
              'limitations': ['Historical execution knowledge is not native decision-time knowledge.',
                              'Archived matching raw records require format-specific decoding and availability validation.',
                              'Unqueried or null RPC results are not proof of failed execution.']}
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--receipts', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--archive', type=Path, action='append', default=[])
    p.add_argument('--rpc-env', type=Path); p.add_argument('--rpc-key', default='SECURE_RPC_URL')
    p.add_argument('--limit', type=int, default=32)
    a = p.parse_args()
    if not 0 <= a.limit <= 256:
        p.error('capability sample limit must be 0..256')
    print(json.dumps(run(a.receipts, a.output, a.archive, read_endpoint(a.rpc_env, a.rpc_key) if a.rpc_env else None, a.limit)))
