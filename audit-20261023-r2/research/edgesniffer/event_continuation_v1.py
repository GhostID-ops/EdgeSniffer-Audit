"""Same-seed secondary activity endpoint. No stale prices, no execution/PnL claims."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics

from burst_persistence_v1 import NS, Study, uncertainty
from run_burst_observation_v1 import admissible_quality

CONTRACT = Path(__file__).with_name('event-continuation-contract-v1.json')


def load(source, population):
    s = Study(population); ambiguous = defaultdict(list); failed = defaultdict(list)
    sha = hashlib.sha256()
    with source.open('rb') as incoming:
        for raw in incoming:
            sha.update(raw); row = json.loads(raw)
            before = s.counts['duplicate_transactions']
            s.consume(row)
            if s.counts['duplicate_transactions'] != before: continue
            known = set(s.history) & set(row['resolved_account_keys'])
            at = row['metadata_available_at_ns']; status = row['execution_status']
            if status == 'FAILED':
                for mint in known: failed[mint].append(at)
            elif status == 'UNKNOWN' or (status == 'SUCCESS' and row['pump_instruction_count'] and not row['pump_trade_event_prefixes']):
                for mint in known: ambiguous[mint].append(at)
    return s, ambiguous, failed, sha.hexdigest()


def interval(s, mint, start, end, ambiguous, failed):
    if mint is None: return {'status': 'NO_CONTROL'}
    points = [p for p in s.history.get(mint, []) if start < p['at'] <= end]
    valid = [p['event'] for p in points if p['event'] is not None]
    unknown = sum(p['event'] is None for p in points)+sum(start < t <= end for t in ambiguous.get(mint, []))
    status = 'RIGHT_CENSORED' if end > s.end else 'AMBIGUOUS_CONTEXT' if unknown else 'OBSERVED_INTERVAL'
    return {'status': status, 'start_ns': start, 'end_ns': end, 'unknown_context_records': unknown,
            'observed_events': len(valid), 'distinct_observed_buyers': len({e['user'] for e in valid if e['side'] == 'buy'}),
            'net_executed_sol_flow': sum(e['sol_amount_lamports']*(1 if e['side'] == 'buy' else -1) for e in valid)/1e9,
            'failed_mint_account_mentions_NOT_flow': sum(start < t <= end for t in failed.get(mint, []))}


def describe(values):
    return {'n': len(values), 'mean': statistics.mean(values) if values else None,
            'median': statistics.median(values) if values else None,
            'positive_fraction': sum(x>0 for x in values)/len(values) if values else None}


def summarize(rows):
    result = {'seeds': len(rows), 'unique_mints': len({r['mint'] for r in rows}),
              'without_control': sum(r['control_mint'] is None for r in rows),
              'confirmation': dict(Counter(r['confirmation'] for r in rows)), 'arms': {},
              'stale_burst_exit_mechanism': dict(Counter(r['stale_burst_exit_mechanism'] for r in rows if r['stale_burst_exit_mechanism'])),
              'meaning': 'Observable Pump activity continuation only; not price continuation, fills or net PnL'}
    for arm in ('burst', 'persistence'):
        admitted = [r for r in rows if arm == 'burst' or r['confirmation'] == 'CONFIRMED']
        complete = [r for r in admitted if all(r['arms'][arm][leg]['status'] == 'OBSERVED_INTERVAL' for leg in ('target', 'control'))]
        deltas = [r['arms'][arm]['target']['net_executed_sol_flow']-r['arms'][arm]['control']['net_executed_sol_flow'] for r in complete]
        counts = [r['arms'][arm]['target']['observed_events']-r['arms'][arm]['control']['observed_events'] for r in complete]
        ci = uncertainty([(r['seed_at_ns'], d) for r,d in zip(complete, deltas)])
        ci['bootstrap_95_percent_SOL_flow'] = ci.pop('bootstrap_95_percent_bps')
        result['arms'][arm] = {'admitted': len(admitted), 'complete_pairs': len(complete),
            'missing_pairs': len(admitted)-len(complete), 'paired_net_SOL_flow_difference': describe(deltas),
            'paired_event_count_difference': describe(counts), 'uncertainty': ci,
            'all_seed_target_statuses': dict(Counter(r['arms'][arm]['target']['status'] for r in rows)),
            'all_seed_control_statuses': dict(Counter(r['arms'][arm]['control']['status'] for r in rows)),
            'observed_zero_event_target_intervals': sum(r['arms'][arm]['target']['status'] == 'OBSERVED_INTERVAL' and r['arms'][arm]['target']['observed_events'] == 0 for r in admitted)}
    return result


def run(batch, output):
    output.mkdir(parents=True, exist_ok=False)
    rows = []; seen = set(); repeated = 0; inputs = []
    for session in sorted(batch.glob('session-*')):
        if not (session/'experiment/report.json').exists(): continue
        quality = json.loads((session/'quality/report.json').read_text())
        if admissible_quality(quality): raise ValueError('quality failure')
        primary = json.loads((session/'experiment/report.json').read_text())
        s, ambiguous, failed, h = load(session/'quality/transactions.jsonl', primary['population'])
        if h != primary['input_sha256']: raise ValueError('input changed')
        original = [json.loads(x) for x in (session/'experiment/opportunities.jsonl').read_text().splitlines()]
        if [r['seed_id'] for r in original] != [r['seed_id'] for r in s.seeds]: raise ValueError('seed population changed')
        inputs.append({'session': session.name, 'input_sha256': h, 'original_seeds': len(original)})
        for seed in original:
            if seed['mint'] in seen: repeated += 1; continue
            seen.add(seed['mint']); t = seed['seed_at_ns']
            row = {k: seed[k] for k in ('seed_id','mint','seed_at_ns','control_mint','confirmation')}
            row['arms'] = {}
            for arm, offset in (('burst',1), ('persistence',3)):
                row['arms'][arm] = {leg: interval(s, mint, t+offset*NS, t+(offset+2)*NS, ambiguous, failed)
                    for leg,mint in (('target',seed['mint']), ('control',seed['control_mint']))}
            row['stale_burst_exit_mechanism'] = None
            if seed['arms']['burst']['target']['exit_mark']['reason'] == 'stale_state':
                window = interval(s, seed['mint'], t+NS, t+17*NS, ambiguous, failed)
                row['stale_burst_exit_mechanism'] = window['status'] if window['status'] != 'OBSERVED_INTERVAL' else (
                    'no_new_decoded_Pump_SOL_events' if window['observed_events'] == 0 else 'some_events_but_no_fresh_exit_mark')
            rows.append(row)
    with (output/'outcomes.jsonl').open('x') as out:
        for r in rows: out.write(json.dumps(r, sort_keys=True)+'\n')
    result = summarize(rows)
    result.update(inputs=inputs, repeat_mint_seeds_preserved_in_primary=repeated,
        contract=json.loads(CONTRACT.read_text()), contract_sha256=hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        outcomes_sha256=hashlib.sha256((output/'outcomes.jsonl').read_bytes()).hexdigest())
    (output/'report.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--batch', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); print(json.dumps(run(a.batch, a.output), indent=2))
