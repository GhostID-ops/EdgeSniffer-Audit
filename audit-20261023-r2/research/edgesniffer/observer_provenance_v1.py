"""Read-only Linux observer manifest. Never dump the process environment.

The adjacent source hash is provenance evidence, NOT proof it built the binary.
Only an explicit research configuration allowlist is emitted; no keys/endpoints.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

SERVICE = 'edgesniffer-independent-native-pump-account-observer-v3.service'
ALLOWED = ('RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED',
           'RUST_SELECTED_CANDIDATE_NO_SUBMIT_DRY_BUILD_ENABLED',
           'RUST_SELECTED_CANDIDATE_CURVE_CACHE_ENABLED',
           'RUST_SELECTED_CANDIDATE_CURVE_CACHE_OWNER_WIDE',
           'RUST_SELECTED_CANDIDATE_CURVE_RPC_FALLBACK_ENABLED',
           'RUST_SELECTED_CANDIDATE_YELLOWSTONE_TX_REPLAY_ENABLED',
           'RUST_SELECTED_CANDIDATE_YELLOWSTONE_TX_OWNER_WIDE_ENABLED')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for blob in iter(lambda: source.read(1024*1024), b''):
            h.update(blob)
    return h.hexdigest()


def inspect():
    pid = int(subprocess.check_output(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value'], text=True))
    if pid <= 0:
        raise ValueError('observer not running')
    proc = Path('/proc')/str(pid)
    binary = (proc/'exe').resolve(strict=True)
    working = (proc/'cwd').resolve(strict=True)
    adjacent = working/'src/main.rs'
    env = {}
    for part in (proc/'environ').read_bytes().split(b'\0'):
        key, sep, value = part.partition(b'=')
        decoded = key.decode(errors='replace')
        if decoded in ALLOWED:
            # These settings are Booleans. Unexpected values are never printed.
            val = value.decode(errors='replace')
            env[decoded] = val if val.lower() in {'true', 'false', '0', '1'} else 'UNEXPECTED_VALUE_REDACTED'
    source = adjacent.read_text()
    markers = ('rustNativePumpCurveTransactionReceipt', 'rustNativeTargetTransactionReceipt',
               'rustNativePumpCurveAccountState', 'failed: Some(false)',
               'CommitmentLevel::Processed', 'account_txn_signature')
    return {'schema': 'OBSERVER_PROVENANCE_V1', 'service': SERVICE, 'pid': pid,
            'binary': str(binary), 'running_binary_sha256': sha(proc/'exe'),
            'on_disk_binary_sha256': sha(binary), 'working_directory': str(working),
            'adjacent_source': str(adjacent), 'adjacent_source_sha256': sha(adjacent),
            'source_marker_counts': {marker: source.count(marker) for marker in markers},
            'allowlisted_effective_environment': env,
            'source_to_binary_build_mapping': 'UNPROVEN',
            'subscription_delivery_completeness': 'UNPROVEN',
            'limitations': ['Adjacent source and binary hashes do not prove reproducible-build equivalence.',
                            'Source marker counts do not prove effective subscription or metadata coverage.',
                            'No service changes, credentials, RPC calls or trading actions.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inspect()
    with args.output.open('x', encoding='utf-8') as out:
        out.write(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, sort_keys=True))
