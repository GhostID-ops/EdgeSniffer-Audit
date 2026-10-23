"""Original-denominator context diagnostic. No RPC, trading, rematching or fills."""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics

from burst_persistence_v1 import NS, WINDOW, Study
from ledger_v1 import SIZES
from transaction_evidence_v1 import digest

CONTRACT = Path(__file__).with_name('quote-context-contract-v1.json')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def jsonl(path):
    with path.open() as source:
        for line in source: yield json.loads(line)


def control_reason(study, mint, now, bucket):
    """Mirror the frozen feature gate; assert against the real implementation."""
    hist = study.history[mint]
    recent = [p for p in hist if now-WINDOW <= p['at'] <= now]
    before = [p for p in hist if p['at'] < now-WINDOW]
    if not hist: reason = 'NO_HISTORY'
    elif now-hist[0]['at'] < NS: reason = 'WARMUP_UNDER_1S'
    elif hist[-1]['event'] is None: reason = 'LATEST_EVENT_INVALID'
    elif not recent: reason = 'NO_EVENT_IN_LAST_250MS'
    elif not before: reason = 'NO_PRE_WINDOW_BASELINE'
    elif any(p['event'] is None for p in recent): reason = 'INVALID_RECENT_EVENT'
    elif before[-1]['event'] is None: reason = 'INVALID_BASELINE'
    elif now-before[-1]['at'] > NS: reason = 'BASELINE_OLDER_THAN_1S'
    elif any(p['event'] is None for p in hist if p['at'] >= now-NS): reason = 'INVALID_EVENT_IN_LAST_1S'
    else:
        f = study.features(mint, now)
        reason = 'ALSO_BURST' if f['burst'] else 'DIFFERENT_LIQUIDITY_BUCKET' if f['bucket'] != bucket else 'ELIGIBLE'
    f = study.features(mint, now)
    expected = bool(f and not f['burst'] and f['bucket'] == bucket)
    if (reason == 'ELIGIBLE') != expected: raise ValueError('control diagnostic diverged from frozen gate')
    return reason


def control_snapshot(study, seed):
    now = seed['seed_at_ns']; bucket = seed['seed_state']['event']['virtual_quote_reserves'].bit_length()-1
    decisions = [{'mint': mint, 'reason': control_reason(study, mint, now, bucket)}
                 for mint in sorted(study.history) if mint != seed['mint']]
    eligible = [r['mint'] for r in decisions if r['reason'] == 'ELIGIBLE']
    selected = min(eligible, key=lambda m: digest([seed['seed_id'], m])) if eligible else None
    if selected != seed['control_mint'] or len(eligible) != seed['control_candidates']:
        raise ValueError('original control identity/count changed')
    return {'seed_id': seed['seed_id'], 'mint': seed['mint'], 'seed_at_ns': now,
            'time_block_20s': now//(20*NS), 'liquidity_bucket': bucket,
            'selected_control': selected, 'candidate_decisions': decisions,
            'exclusion_counts': dict(Counter(x['reason'] for x in decisions)),
            'invalid_latest_reasons':dict(Counter(study.history[x['mint']][-1]['reason'] for x in decisions if x['reason']=='LATEST_EVENT_INVALID')),
            'no_match_stage': None if eligible else 'NO_FEATURE_ELIGIBLE_NONBURST_MINT' if not any(x['reason']=='DIFFERENT_LIQUIDITY_BUCKET' for x in decisions) else 'NONBURST_MINTS_ONLY_IN_OTHER_BUCKETS'}


def mint_available_ns(row):
    state = row.get('mintState') or {}
    clocks = [row.get('capturedAtMs'), state.get('mintAccountFetchedAtMs'),
              (row.get('repair') or {}).get('repairedAtMs')]
    clocks = [x for x in clocks if type(x) is int]
    return max(clocks)*1_000_000 if clocks else None


def prior(items, at, key):
    times = [key(x) for x in items]
    i = bisect_right(times, at)-1
    return items[i] if i >= 0 else None


def freshness(mark, history, account_history, session_start, session_end):
    if 'available_at_ns' not in mark: return {'status': 'NO_OBSERVED_STATE'}
    start, end = mark['available_at_ns'], mark['deadline_ns']
    events = [p for p in history if start < p['at'] <= end]
    next_writes = [r for r in account_history if r['decodeCompleteUnixNs'] > end]
    next_write = min(next_writes, key=lambda r:r['decodeCompleteUnixNs']) if next_writes else None
    fields = ('virtualSolReserves','virtualTokenReserves','realSolReserves','realTokenReserves')
    equal = all(next_write.get(k) == mark['reserves'].get(k) for k in fields) if next_write else None
    mapping = dict(zip(fields, ('virtual_quote_reserves','virtual_token_reserves','real_quote_reserves','real_token_reserves')))
    contradictions = [p for p in events if p['event'] and any(p['event'].get(v) != mark['reserves'].get(k) for k,v in mapping.items())]
    return {'age_ns': end-start, 'original_1s_age_pass': end-start <= NS,
            'transaction_session_covers_interval': session_start <= start <= end <= session_end,
            'events_observed_after_state_before_boundary': len(events),
            'differing_event_reserves_after_state': len(contradictions),
            'next_observed_write_same_four_reserves': equal,
            'next_write_available_at_ns': next_write['decodeCompleteUnixNs'] if next_write else None,
            'status': 'OBSERVED_DIFFERING_EVENT_STATE' if contradictions else 'NO_DIFFERING_EVENT_OBSERVED_NOT_CONTINUITY_PROOF',
            'complete_write_coverage_certified': False,
            'reason': 'Normalized account source has no independently verified full write/session/fork ledger; sampled tx parity is not full continuity'}


def concentration(rows, seeds):
    result = {}
    for arm in ('burst','persistence'):
        pairs = []
        for row in rows:
            if arm == 'persistence' and row['confirmation'] != 'CONFIRMED': continue
            legs = row['arms'][arm]
            if not all(legs[k]['status'] == 'OBSERVED_INTERVAL' for k in ('target','control')): continue
            seed = seeds[row['seed_id']]
            pairs.append({'mint': row['mint'], 'control': row['control_mint'],
                'bucket': seed['seed_state']['event']['virtual_quote_reserves'].bit_length()-1,
                'excess_sol_flow': legs['target']['net_executed_sol_flow']-legs['control']['net_executed_sol_flow']})
        def mean(rs): return statistics.mean(r['excess_sol_flow'] for r in rs) if rs else None
        controls = sorted({p['control'] for p in pairs}); mints = sorted({p[k] for p in pairs for k in ('mint','control')})
        positive = sorted((p['excess_sol_flow'] for p in pairs if p['excess_sol_flow'] > 0), reverse=True)
        result[arm] = {'pairs': len(pairs), 'mean_excess_sol_flow': mean(pairs),
            'unique_controls': len(controls), 'max_control_reuse': max(Counter(p['control'] for p in pairs).values(), default=0),
            'equal_control_weight_mean': statistics.mean(mean([p for p in pairs if p['control']==c]) for c in controls) if controls else None,
            'leave_mint_out_either_role': {m:{'remaining_pairs':sum(m not in (p['mint'],p['control']) for p in pairs),
                'mean':mean([p for p in pairs if m not in (p['mint'],p['control'])])} for m in mints},
            'leave_control_group_out': {c:mean([p for p in pairs if p['control']!=c]) for c in controls},
            'liquidity_buckets': {str(b):{'pairs':sum(p['bucket']==b for p in pairs), 'mean':mean([p for p in pairs if p['bucket']==b]),
                'virtual_sol_range': [2**b/NS,2**(b+1)/NS]} for b in sorted({p['bucket'] for p in pairs})},
            'largest_positive_contributions': {str(n):{'sum':sum(positive[:n]),
                'share_of_all_positive':sum(positive[:n])/sum(positive) if positive else None,
                'mean_after_removal': (sum(p['excess_sol_flow'] for p in pairs)-sum(positive[:n]))/(len(pairs)-min(n,len(positive)))
                    if len(pairs)>min(n,len(positive)) else None} for n in (1,3)},
            'pairs_detail': pairs, 'interpretation': 'Retrospective sensitivity only, correlated controls and one market period; not net profit or validation'}
    return result


def run(batch, canonical, account_source, flow_source, mint_source, fee_source, output):
    output.mkdir(parents=True, exist_ok=False)
    boundary = {r['seed_id']:r for r in jsonl(canonical/'boundaries.jsonl')}
    canonical_report = json.loads((canonical/'report.json').read_text())
    expected = canonical_report['artifacts']['boundaries.jsonl']
    if sha(canonical/'boundaries.jsonl') != expected: raise ValueError('boundary input changed')
    if sha(account_source) != canonical_report['snapshot']['sha256']: raise ValueError('account snapshot changed')
    mappings = canonical_report['canonical_derivation']['mappings']
    curves = {v['curve'] for v in mappings.values()}; accounts = defaultdict(list)
    for row in jsonl(account_source):
        if row.get('bondingCurve') in curves: accounts[row['bondingCurve']].append(row)
    for history in accounts.values(): history.sort(key=lambda r:r['decodeCompleteUnixNs'])
    # Seal append-only auxiliary evidence at a fixed byte prefix. Do not read a growing EOF.
    aux = {}; mint_rows = defaultdict(list); fee_rows=[]
    for name, source in (('mint',mint_source),('fee',fee_source)):
        size = source.stat().st_size; h = hashlib.sha256(); count = 0
        with source.open('rb') as incoming, (output/(name+'-retained-prefix.jsonl')).open('xb') as sink:
            while incoming.tell() < size:
                raw = incoming.readline()
                if incoming.tell()>size or not raw.endswith(b'\n'): break
                h.update(raw); sink.write(raw); row=json.loads(raw); count+=1
                if name=='mint' and row.get('mint') in mappings and mint_available_ns(row) is not None:
                    mint_rows[row['mint']].append(row)
                if name=='fee': fee_rows.append({k:v for k,v in row.items() if k!='accountDataBase64'})
        aux[name]={'source':str(source),'sealed_bytes':(output/(name+'-retained-prefix.jsonl')).stat().st_size,'sha256':h.hexdigest(),'rows':count}
    for rows in mint_rows.values(): rows.sort(key=mint_available_ns)
    all_seeds={}; coverage=[]; control_rows=[]; attempts=[]; inputs=[]
    for session in sorted(batch.glob('session-*')):
        original_path=session/'experiment/opportunities.jsonl'
        if not original_path.exists(): continue
        original=list(jsonl(original_path)); primary=json.loads((session/'experiment/report.json').read_text())
        source=session/'quality/transactions.jsonl'; h=hashlib.sha256(); s=Study(primary['population']); snapshots={}
        with source.open('rb') as incoming:
            for raw in incoming:
                h.update(raw); tx=json.loads(raw); before=len(s.seeds); s.consume(tx)
                for seed in s.seeds[before:]: snapshots[seed['seed_id']]=control_snapshot(s,seed)
        if h.hexdigest()!=primary['input_sha256']: raise ValueError('native input changed')
        if [r['seed_id'] for r in original]!=[r['seed_id'] for r in s.seeds]: raise ValueError('signal population changed')
        for old,new in zip(original,s.seeds):
            if any(old[k]!=v for k,v in new.items()): raise ValueError('frozen seed/control content changed')
        inputs.append({'session':session.name,'sha256':h.hexdigest(),'opportunities_sha256':sha(original_path)})
        for seed in original:
            sid=seed['seed_id']
            if sid not in boundary: continue
            all_seeds[sid]=seed; diag=snapshots[sid]; diag['session']=session.name; control_rows.append(diag)
            for label,mark in boundary[sid]['boundaries'].items():
                arm,clock,leg=label.split(':'); mint=seed['mint'] if leg=='target' else seed['control_mint']
                if not mint:
                    coverage.append({'seed_id':sid,'boundary':label,'status':'NO_ORIGINAL_CONTROL'}); continue
                at=seed['arms'][arm][clock]; hist=s.history.get(mint,[])
                point=prior(hist,at,lambda p:p['at']); e=point['event'] if point else None
                mr=prior(mint_rows[mint],at,mint_available_ns)
                coverage.append({'seed_id':sid,'mint':mint,'boundary':label,'deadline_ns':at,
                    'state':freshness(mark,hist,accounts[mark.get('curve')],s.start,s.end),
                    'native_event_fee_observation': None if not e else {'available_at_ns':point['at'],'signature':point['signature'],
                        'fields':{k:e.get(k) for k in ('protocol_fee_bps','protocol_fee_amount','creator_fee_bps','creator_fee_amount','cashback_bps','buyback_bps')},
                        'certified_schedule_for_our_size':False},
                    'retained_mint_context': {'prior_row_available_at_ns':mint_available_ns(mr) if mr else None,
                        'rows_for_mint':len(mint_rows[mint]),'future_rows':sum(mint_available_ns(r)>at for r in mint_rows[mint]),
                        'owner':mr.get('mintAccountOwner') if mr else None,
                        'state_source':(mr.get('mintState') or {}).get('stateSource') if mr else None,
                        'raw_account_bytes_retained':bool(mr and any(k in mr for k in ('accountDataBase64','rawAccountDataBase64'))),
                        'complete_update_coverage_certified':False},
                    'quote_status':'UNRESOLVED_CONTEXT', 'net_pnl_sol':None})
            for arm in ('burst','persistence'):
                for size in SIZES:
                    attempts.append({'seed_id':sid,'mint':seed['mint'],'control_mint':seed['control_mint'],'arm':arm,'size_lamports':size,
                        'policy_admission': 'ADMITTED' if seed['arms'][arm]['admitted'] else seed['confirmation'],
                        'submission_status':'NOT_SUBMITTED_RESEARCH_ONLY','quote_status':'UNRESOLVED_CONTEXT',
                        'execution_status':'NOT_ATTEMPTED','net_pnl_sol':None,
                        'missing_requirements':['verified_fee_schedule_at_boundary','verified_mint_extension_context',
                            'complete_state_continuity','validated_own_state_and_integer_quote_model']})
    if set(all_seeds)!=set(boundary): raise ValueError('canonical signal denominator mismatch')
    report={'schema':'QUOTE_CONTEXT_DIAGNOSTIC_V1','seeds':len(all_seeds),'size_scenarios_per_arm':len(all_seeds)*len(SIZES),
        'attempt_rows_two_arms':len(attempts),'valid_quotes':0,'non_executable_quotes_proven':0,'unresolved_attempts':len(attempts),
        'actual_submissions':0,'missing_controls':sum(r['selected_control'] is None for r in control_rows),
        'unmatched_candidate_exclusions':dict(sum((Counter(r['exclusion_counts']) for r in control_rows if r['selected_control'] is None),Counter())),
        'unmatched_seed_stage':dict(Counter(r['no_match_stage'] for r in control_rows if r['selected_control'] is None)),
        'unmatched_invalid_latest_event_reasons':dict(sum((Counter(r['invalid_latest_reasons']) for r in control_rows if r['selected_control'] is None),Counter())),
        'control_distribution_by_session':{name:{'seeds':sum(r['session']==name for r in control_rows),
            'unmatched':sum(r['session']==name and r['selected_control'] is None for r in control_rows)} for name in sorted({r['session'] for r in control_rows})},
        'control_distribution_by_20s_block':{str(t):{'seeds':sum(r['time_block_20s']==t for r in control_rows),
            'unmatched':sum(r['time_block_20s']==t and r['selected_control'] is None for r in control_rows)} for t in sorted({r['time_block_20s'] for r in control_rows})},
        'flow_concentration':concentration(list(jsonl(flow_source)),all_seeds),'inputs':inputs,'auxiliary_evidence':aux,
        'retained_fee_snapshots':fee_rows,
        'contract':json.loads(CONTRACT.read_text()),'contract_sha256':sha(CONTRACT),'implementation_sha256':sha(Path(__file__)),
        'canonical_report_sha256':sha(canonical/'report.json'),'boundary_sha256':sha(canonical/'boundaries.jsonl'),
        'account_snapshot_sha256':sha(account_source),'flow_outcomes_sha256':sha(flow_source)}
    exits=[r for r in coverage if r['boundary']=='burst:exit_at_ns:target']
    stale=[r for r in exits if not r['state'].get('original_1s_age_pass',False)]
    report['burst_exit_state']={'observed':len(exits),'older_than_1s':len(stale),
        'stale_interval_crosses_transaction_session_boundary':sum(not r['state']['transaction_session_covers_interval'] for r in stale),
        'stale_with_observed_different_event_state':sum(r['state']['differing_event_reserves_after_state']>0 for r in stale),
        'stale_next_observed_write_equal':sum(r['state']['next_observed_write_same_four_reserves'] is True for r in stale),
        'independently_certified_unchanged':0}
    present=[r for r in coverage if 'retained_mint_context' in r]
    report['boundary_context_counts']={'target_and_existing_control_boundaries':len(present),
        'prior_retained_mint_rows':sum(r['retained_mint_context']['prior_row_available_at_ns'] is not None for r in present),
        'native_event_fee_observations':sum(r['native_event_fee_observation'] is not None for r in present),
        'verified_applicable_fee_schedules':0}
    for name,rows in (('control-diagnostics',control_rows),('boundary-context',coverage),('attempts',attempts)):
        path=output/(name+'.jsonl')
        with path.open('x') as sink:
            for row in rows:sink.write(json.dumps(row,sort_keys=True)+'\n')
        report[name+'_sha256']=sha(path)
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('batch','canonical','account-source','flow-source','mint-source','fee-source','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(); r=run(a.batch,a.canonical,a.account_source,a.flow_source,a.mint_source,a.fee_source,a.output)
    print(json.dumps({k:v for k,v in r.items() if k not in ('flow_concentration','contract','inputs','control_distribution_by_20s_block')},indent=2))
