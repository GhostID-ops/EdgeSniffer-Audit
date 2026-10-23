"""Bounded receive-only observation. Fixed duration, not outcome-dependent stopping."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from burst_persistence_v1 import CONTRACT, summary


def admissible_quality(q):
    reasons = []
    if q['input']['start_byte'] != 0 or q['input']['unsealed_tail_bytes_excluded']:
        reasons.append('truncated_capture')
    if q.get('session_start_ns') is None or q.get('session_end_ns') is None:
        reasons.append('session_boundaries_missing')
    if q.get('reconnect_gaps'): reasons.append('reconnect_gap')
    for name in ('malformed_lines', 'writer_sequence_discontinuities', 'invalid_native_clock_rows',
                 'wall_clock_regressions', 'raw_hash_mismatches', 'raw_payload_unavailable',
                 'transaction_body_unavailable', 'normalization_errors'):
        if q['counts'].get(name, 0): reasons.append(name)
    if q.get('decoder_gaps'): reasons.append('decoder_gaps')
    if q.get('finalized_comparison', {}).get('missing_transactions', 0): reasons.append('sampled_missing_transactions')
    if any(v for k,v in q.get('native_vs_finalized_metadata', {}).items() if k.endswith('_mismatches')):
        reasons.append('sampled_metadata_mismatch')
    return reasons


def aggregate(root):
    rows = []; seen = set(); duplicates = 0; sessions = []
    for path in sorted(root.glob('session-*/experiment/opportunities.jsonl')):
        sessions.append(str(path.parent.parent))
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row['mint'] in seen:
                duplicates += 1; continue  # First seed per mint across sessions; never choose best outcome.
            seen.add(row['mint']); rows.append(row)
    result = {'completed_analyzed_sessions': len(sessions), 'independent_first_mint_seeds': len(rows),
              'later_same_mint_seeds_retained_but_not_independent': duplicates,
              'nonempty_seed_time_20s_blocks': len({r['seed_at_ns']//20_000_000_000 for r in rows}),
              'first100_engineering_floor_reached': len(rows) >= 100,
              'session_paths': sessions, 'arms': {},
              'caveat': 'Separate bounded sessions with observable intersession gaps; first-mint population, reused controls/market regimes not statistically independent. Native observed-price diagnostics, not fills/PnL.'}
    for arm in ('burst', 'persistence'):
        active = [r['arms'][arm] for r in rows if r['arms'][arm]['admitted']]
        result['arms'][arm] = {'admitted': len(active),
            'admitted_target_returns': summary([x['target']['return_bps'] for x in active]),
            'admitted_matched_excess': summary([x['paired_excess_bps'] for x in active]),
            'all_seed_policy_proxy': summary([r['arms'][arm]['policy_return_bps'] for r in rows])}
    return result


def run(args):
    root = args.output; root.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 'FIXED_BURST_OBSERVATION_BATCH_V1', 'started_at_ns': time.time_ns(),
        'sessions': args.sessions, 'seconds_each': 120, 'receive_only': True,
        'stop_rule': 'fixed session count or operational safety failure; never returns',
        'minimum_free_bytes_before_session': 16*1024**3,
        'contract_sha256': hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'binary_sha256': hashlib.sha256(args.binary.read_bytes()).hexdigest()}
    (root/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    status = 'COMPLETE'; reason = None
    try:
        for n in range(args.sessions):
            if shutil.disk_usage(root).free < 16*1024**3:
                status, reason = 'STOPPED_SAFETY', 'free_space_below_reserved_floor'; break
            session = root/f'session-{n+1:02}'; session.mkdir()
            with (session/'commands.log').open('x') as log:
                subprocess.run([sys.executable, str(args.capture_code/'run_capture_v1.py'), '--binary', str(args.binary),
                    '--env-file', str(args.env_file), '--output-dir', str(session/'capture'), '--seconds', '120'],
                    check=True, stdout=log, stderr=log, timeout=170)
                subprocess.run([sys.executable, str(args.capture_code/'capture_quality_v1.py'), '--input', str(session/'capture/capture.jsonl'),
                    '--output', str(session/'quality'), '--rpc-env', str(args.env_file), '--block-limit', '4'],
                    check=True, stdout=log, stderr=log, timeout=300)
                q = json.loads((session/'quality/report.json').read_text())
                rejected = admissible_quality(q)
                (session/'admission.json').write_text(json.dumps({'rejection_reasons': rejected})+'\n')
                if rejected:
                    status, reason = 'STOPPED_QUALITY', rejected; break
                subprocess.run([sys.executable, str(Path(__file__).with_name('burst_persistence_v1.py')),
                    '--source', str(session/'quality/transactions.jsonl'), '--output', str(session/'experiment'),
                    '--population', root.name+':'+session.name], check=True, stdout=log, stderr=log, timeout=120)
            (root/f'progress-{n+1:02}.json').write_text(json.dumps(aggregate(root), indent=2)+'\n')
    except (OSError, subprocess.SubprocessError) as exc:
        status, reason = 'STOPPED_ERROR', type(exc).__name__
    result = aggregate(root)
    result.update(status=status, stop_reason=reason, finished_at_ns=time.time_ns())
    (root/'completion.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('output', 'capture-code', 'binary', 'env-file'): p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--sessions', type=int, default=8)
    a = p.parse_args()
    if not 1 <= a.sessions <= 8: p.error('bounded1..8 sessions')
    print(json.dumps(run(a), indent=2))
