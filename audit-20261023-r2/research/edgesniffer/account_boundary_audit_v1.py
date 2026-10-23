"""Bounded read-only account/event comparison. Never certifies quotes from field names."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess

from processed_evidence_join_v1 import snapshot_tail

FIELDS = {'virtualSolReserves': 'virtual_quote_reserves', 'virtualTokenReserves': 'virtual_token_reserves',
          'realSolReserves': 'real_quote_reserves', 'realTokenReserves': 'real_token_reserves'}


def invariants(row):
    if any(type(row.get(k)) is not int or row[k] < 0 for k in FIELDS): return 'missing_or_invalid_reserves'
    if not row['virtualSolReserves'] or not row['virtualTokenReserves']: return 'zero_virtual_reserve'
    if row['realSolReserves'] > row['virtualSolReserves'] or row['realTokenReserves'] > row['virtualTokenReserves']:
        return 'real_exceeds_virtual'
    return None


def select(mint, at, mappings, states):
    curves = {curve for available,curve in mappings.get(mint, []) if available <= at}
    if len(curves) != 1: return {'status': 'NO_CAUSAL_UNIQUE_CURVE_MAPPING', 'deadline_ns': at}
    curve = next(iter(curves)); history = states.get(curve, [])
    known = [r for r in history if r['decodeCompleteUnixNs'] <= at]
    if not known: return {'status': 'NO_PRIOR_ACCOUNT_STATE', 'deadline_ns': at, 'curve': curve}
    latest = max(known, key=lambda r: r['decodeCompleteUnixNs'])
    reason = invariants(latest)
    # A later-arriving older write cannot stand in for current state.
    if any(type(r.get(k)) is not int for r in known for k in ('slot','writeVersion')):
        reason = 'unknown_canonical_write_identity'
    elif (latest['slot'], latest['writeVersion']) < max((r['slot'], r['writeVersion']) for r in known):
        reason = 'canonical_write_regression'
    return {'status': reason or 'OBSERVED_UNCERTIFIED_ACCOUNT_STATE', 'deadline_ns': at,
            'available_at_ns': latest['decodeCompleteUnixNs'], 'age_ns': at-latest['decodeCompleteUnixNs'],
            'curve': curve, 'source_line': latest['_line'], 'signature': latest.get('accountTxnSignature'),
            'slot': latest['slot'], 'reserves': {k:latest.get(k) for k in FIELDS},
            'complete': latest.get('complete'), 'economic_admission': False}


def derive_curves(mints, module):
    script = Path(__file__).with_name('derive_pump_curves_v1.cjs')
    completed = subprocess.run(['node', str(script), str(module)], input=json.dumps(sorted(mints)),
                               text=True, capture_output=True, check=True, timeout=60)
    result = json.loads(completed.stdout)
    if set(result['mappings']) != set(mints): raise ValueError('PDA derivation coverage mismatch')
    result['implementation_sha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
    result['idl_reference'] = 'https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/pump.json'
    result['idl_rule'] = 'bonding_curve PDA seeds: constant bonding-curve bytes, mint pubkey; Pump program'
    return result


def run(batch, source, output, web3_module=None):
    output.mkdir(parents=True, exist_ok=False)
    seeds = []; seen = set(); transactions = {}; input_hashes = {}; first_known = {}
    for session in sorted(batch.glob('session-*')):
        p = session/'experiment/opportunities.jsonl'
        if not p.exists(): continue
        for line in p.read_text().splitlines():
            row = json.loads(line)
            if row['mint'] in seen: continue
            seen.add(row['mint']); seeds.append(row)
    mints = {r['mint'] for r in seeds} | {r['control_mint'] for r in seeds if r['control_mint']}
    for session in sorted(batch.glob('session-*')):
        path = session/'quality/transactions.jsonl'
        if not path.exists(): continue
        sha = hashlib.sha256()
        with path.open('rb') as incoming:
            for raw in incoming:
                sha.update(raw); row = json.loads(raw)
                events = [e for e in row['pump_trade_event_prefixes'] if e['signed_sol_flow_eligible']]
                for event in events:
                    if event['mint'] in mints and row['execution_status'] == 'SUCCESS':
                        first_known[event['mint']] = min(row['metadata_available_at_ns'], first_known.get(event['mint'], row['metadata_available_at_ns']))
                if row['execution_status'] == 'SUCCESS' and len(events) == 1 and events[0]['mint'] in mints:
                    transactions[(row['signature'],row['slot'])] = (row['metadata_available_at_ns'],set(row['resolved_account_keys']),events[0])
        expected = json.loads((session/'experiment/report.json').read_text())['input_sha256']
        if sha.hexdigest() != expected: raise ValueError('transaction evidence changed')
        input_hashes[session.name] = expected
    manifest = snapshot_tail(source, output/'account-snapshot.jsonl', 256*1024*1024)
    counts = Counter(); comparisons = Counter(); keys = Counter(); mappings = defaultdict(list)
    first = last = None; relevant_curves = set(); comparison_rows = []; matched_transactions = set()
    canonical = derive_curves(mints, web3_module) if web3_module else None
    if canonical:
        for mint, identity in canonical['mappings'].items():
            if mint in first_known:
                mappings[mint].append((first_known[mint], identity['curve']))
                relevant_curves.add(identity['curve'])
    with (output/'account-snapshot.jsonl').open() as incoming:
        for line_no,line in enumerate(incoming, 1):
            row = json.loads(line)
            if row.get('kind') != 'rustNativePumpCurveAccountState': continue
            counts['account_rows'] += 1; keys.update(row.keys())
            at = row.get('decodeCompleteUnixNs')
            if type(at) is not int: counts['missing_native_clock'] += 1; continue
            first = at if first is None else min(first,at); last = at if last is None else max(last,at)
            reason = invariants(row); counts[reason or 'reserve_inequalities_pass'] += 1
            tx = transactions.get((row.get('accountTxnSignature'),row.get('slot')))
            curve = row.get('bondingCurve')
            if tx is None or curve not in tx[1]: continue
            if canonical and canonical['mappings'][tx[2]['mint']]['curve'] != curve:
                counts['loose_matches_excluded_noncanonical_account'] += 1; continue
            counts['identity_matched_account_rows'] += 1
            matched_transactions.add((row['accountTxnSignature'],row['slot']))
            event = tx[2]; available = max(at,tx[0])
            if not canonical: mappings[event['mint']].append((available,curve))
            relevant_curves.add(curve)
            equal = {k:row.get(k) == event.get(v) for k,v in FIELDS.items()}
            comparisons['all_four_equal' if all(equal.values()) else 'reserve_mismatch'] += 1
            comparisons.update(k+('_equal' if value else '_mismatch') for k,value in equal.items())
            comparison_rows.append({'line':line_no,'signature':row['accountTxnSignature'],'mint':event['mint'],
                                    'curve':curve,'mapping_available_at_ns':available,'equal':equal,
                                    'account_reserves':{k:row.get(k) for k in FIELDS},
                                    'event_reserves':{k:event.get(v) for k,v in FIELDS.items()}})
    states = defaultdict(list)
    with (output/'account-snapshot.jsonl').open() as incoming:
        for line_no,line in enumerate(incoming,1):
            row = json.loads(line)
            if row.get('kind') == 'rustNativePumpCurveAccountState' and row.get('bondingCurve') in relevant_curves and type(row.get('decodeCompleteUnixNs')) is int:
                row['_line'] = line_no; states[row['bondingCurve']].append(row)
    statuses = Counter(); rows = []; age_inventory = defaultdict(Counter)
    for seed in seeds:
        row = {'seed_id':seed['seed_id'],'mint':seed['mint'],'control_mint':seed['control_mint'],'boundaries':{}}
        for arm in ('burst','persistence'):
            for boundary in ('entry_at_ns','exit_at_ns'):
                for leg,mint in (('target',seed['mint']),('control',seed['control_mint'])):
                    mark = select(mint,seed['arms'][arm][boundary],mappings,states)
                    key = arm+':'+boundary+':'+leg
                    row['boundaries'][key] = mark; statuses[key+':'+mark['status']] += 1
                    if mark['status'] == 'OBSERVED_UNCERTIFIED_ACCOUNT_STATE':
                        age_inventory[key]['observed'] += 1
                        age_inventory[key]['older_than_one_second'] += mark['age_ns'] > 1_000_000_000
                        age_inventory[key]['curve_complete_true'] += mark['complete'] is True
                        age_inventory[key]['max_age_ns'] = max(mark['age_ns'],age_inventory[key]['max_age_ns'])
        rows.append(row)
    for name, items in (('comparisons.jsonl',comparison_rows),('boundaries.jsonl',rows)):
        (output/name).write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in items))
    result = {'schema':'NATIVE_ACCOUNT_BOUNDARY_AUDIT_V1','seeds':len(seeds),'selected_mints_including_controls':len(mints),
        'canonical_derivation': canonical,
        'snapshot':manifest,'native_clock_min_ns':first,'native_clock_max_ns':last,'counts':dict(counts),
        'selected_event_transaction_coverage': {'expected_unique':len(transactions),'matched_unique':len(matched_transactions),
                                              'missing_unique':len(set(transactions)-matched_transactions)},
        'boundary_age_and_completion_inventory': {k:dict(v) for k,v in age_inventory.items()},
        'event_account_comparisons':dict(comparisons),'account_field_inventory':dict(keys),'boundary_statuses':dict(statuses),
        'input_transaction_hashes':input_hashes,'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'artifacts':{n:hashlib.sha256((output/n).read_bytes()).hexdigest() for n in ('boundaries.jsonl','comparisons.jsonl')},
        'limits':['Bounded tail snapshot, not complete account-stream coverage or gap audit.',
                  'Signature/slot/key joins establish identity, not correctness of a named reserve field.',
                  'Current native source lacks certified full fee/mint-extension schedule; no price or PnL recovered here.',
                  'Observed state is not certified unchanged/current/executable; no new quotes or trading enabled.']}
    (output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    for n in ('batch','source','output'): p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--web3-module',type=Path)
    a=p.parse_args(); print(json.dumps(run(a.batch,a.source,a.output,a.web3_module),indent=2))
