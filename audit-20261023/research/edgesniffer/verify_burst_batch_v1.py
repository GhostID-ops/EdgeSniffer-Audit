"""Post-run integration verification and common-population report; no collection."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from burst_persistence_v1 import run as replay, report
from run_burst_observation_v1 import admissible_quality


def verify(batch, output, prior_opportunities=None):
    output.mkdir(parents=True, exist_ok=False)
    completion = json.loads((batch/'completion.json').read_text())
    manifest = json.loads((batch/'manifest.json').read_text())
    rows = []; seen = set(); repeats = 0; sessions = []; counts = Counter()
    starts = []; ends = []; observed_ns = 0
    for session in sorted(batch.glob('session-*')):
        if not (session/'experiment/report.json').exists(): continue
        quality = json.loads((session/'quality/report.json').read_text())
        if admissible_quality(quality): raise ValueError('quality changed after admission')
        original = json.loads((session/'experiment/report.json').read_text())
        current = replay(session/'quality/transactions.jsonl', output/session.name, original['population'])
        if current['input_sha256'] != original['input_sha256'] or current['artifacts'] != original['artifacts']:
            raise ValueError('current-code replay changed immutable opportunities/attempts')
        if current['contract_sha256'] != manifest['contract_sha256']:
            raise ValueError('policy changed within batch')
        counts.update(current['counts'])
        starts.append(quality['session_start_ns']); ends.append(quality['session_end_ns'])
        observed_ns += quality['session_end_ns']-quality['session_start_ns']
        for line in (output/session.name/'opportunities.jsonl').read_text().splitlines():
            row = json.loads(line)
            if not quality['session_start_ns'] <= row['seed_at_ns'] <= quality['session_end_ns']:
                raise ValueError('seed outside captured session')
            if row['mint'] in seen:
                repeats += 1; continue
            seen.add(row['mint']); rows.append(row)
        sessions.append({'session': session.name, 'replay_artifacts_equal': True,
            'source_sha256': current['input_sha256'], 'raw_capture_sha256': quality['input']['sha256'],
            'input_transactions': current['counts']['input_transactions'], 'opportunities': current['opportunities'],
            'raw_capture_bytes': quality['input']['snapshot_bytes'],
            'recorded_reconnect_gaps': len(quality['reconnect_gaps']),
            'decoder_gaps': quality['decoder_gaps'], 'finalized_comparison': quality['finalized_comparison'],
            'native_vs_finalized_metadata': quality['native_vs_finalized_metadata'],
            'slot_numeric_discontinuities': quality['slot_numeric_discontinuities']})
    study = SimpleNamespace(counts=counts, start=min(starts) if starts else None, end=max(ends) if ends else None)
    result = report(study, rows)
    if prior_opportunities:
        prior = {json.loads(line)['mint'] for line in prior_opportunities.read_text().splitlines()}
        result['prior_cohort_mint_overlap'] = {'prior_unique_mints': len(prior),
            'overlapping_mints': len(prior & seen), 'new_mints': len(seen-prior),
            'interpretation': 'Temporal observations kept separate; overlapping assets are not new independent assets'}
    result.update(batch=str(batch), batch_status=completion['status'], expected_sessions=manifest['sessions'],
                  verified_sessions=sessions, later_same_mint_seeds= repeats, observed_seconds=observed_ns/1e9,
                  intersession_unobserved_seconds=((max(ends)-min(starts)-observed_ns)/1e9) if starts else None,
                  seed_time_20s_blocks=len({r['seed_at_ns']//20_000_000_000 for r in rows}),
                  verification_implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  analysis_implementation_sha256=hashlib.sha256(Path(__file__).with_name('burst_persistence_v1.py').read_bytes()).hexdigest(),
                  provenance='Current source-provenance guard added after batch start; identical native opportunity/attempt replay proves no selection or outcome changes. Original source/results preserved.',
                  engineering_floor='100 unique seed mints and30 seed-time blocks, not a powered validation target')
    (output/'report.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--batch', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--prior-opportunities', type=Path)
    a = p.parse_args(); print(json.dumps(verify(a.batch, a.output, a.prior_opportunities), indent=2))
