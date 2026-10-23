"""A3h4 bounded native continuity and synchronized-bank observation audit.

No old78 recovery, no retrospective rematching, no fills or net-PnL calculation.
The independent signal/control population is reconstructed exclusively from native
transactions. Later slot/account evidence is reported separately, never admitted.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from account_boundary_audit_v1 import derive_curves
from burst_persistence_v1 import Study, NS
from ledger_v1 import SIZES
from quote_context_adapter_v1 import (FEE, GLOBAL, CLOCK, PUMP_IDL, FEE_IDL,
    Unsupported, available, decode_capture, causal_context, idl_account,
    mint_state, fee_rates, quote, write_jsonl)
from quote_context_adapter_v1 import snapshot_available_at
from quote_context_v1 import jsonl, sha
from synchronized_context_v1 import native_view, Scheduler
from transaction_evidence_v1 import digest

PUMP_LAYOUT = json.loads(PUMP_IDL.read_text())
FEE_LAYOUT = json.loads(FEE_IDL.read_text())


def writable_keys(raw):
    """Privileges, not account mentions. Unknown layout must never mean no touch."""
    try:
        message = raw['transaction']['message']; meta = raw['meta']
        if not isinstance(meta,dict):return None
        keys = message['accountKeys']; h = message['header']
        n, rs, ru = (h[k] for k in ('numRequiredSignatures', 'numReadonlySignedAccounts', 'numReadonlyUnsignedAccounts'))
        if not all(type(x) is int for x in (n,rs,ru)) or not 0 <= rs <= n <= len(keys) or not 0 <= ru <= len(keys)-n:
            return None
        loaded = meta.get('loadedAddresses') or {}
        lookups = message.get('addressTableLookups') or []
        w, r = loaded.get('writable') or [], loaded.get('readonly') or []
        if sum(len(x['writableIndexes']) for x in lookups) != len(w) or sum(len(x['readonlyIndexes']) for x in lookups) != len(r):
            return None
        if not all(isinstance(k,str) for k in keys+w+r): return None
        return set(keys[:n-rs]+keys[n:len(keys)-ru]+w)
    except (KeyError, TypeError, AttributeError): return None


def native_index(rows):
    touches = defaultdict(list); writes = defaultdict(list); slots = []; faults = []
    last_sequence = 0; last_time = None; unknown = []
    for line, row in enumerate(rows,1):
        at = row.get('normalized_at_unix_ns', row.get('available_at_ns',0))
        if 'sequence' in row:
            if row['sequence'] != last_sequence+1: faults.append({'at':at,'reason':'SEQUENCE_GAP','line':line})
            last_sequence = row['sequence']
        if 'received_at_unix_ns' in row:
            if last_time is not None and row['received_at_unix_ns'] < last_time: faults.append({'at':at,'reason':'CLOCK_REGRESSION','line':line})
            last_time = row['received_at_unix_ns']
        if row.get('kind') in ('GAP','CONTEXT_SCOPE_LIMIT'):
            faults.append({'at':at,'reason':row['kind'],'line':line})
        if row.get('kind') == 'SLOT':
            slots.append(dict(row['slot_state'],at=at))
        if row.get('kind') == 'ACCOUNT':
            writes[row['account']['pubkey']].append({'at':at,'line':line,**row['account']})
        if row.get('kind') == 'TRANSACTION':
            raw = row['transaction']; meta = raw.get('meta'); keys = writable_keys(raw)
            status = 'UNKNOWN' if not isinstance(meta,dict) or 'err' not in meta else 'FAILED' if meta['err'] is not None else 'SUCCESS'
            # Failed transactions may charge fees, but cannot commit mint/curve
            # program state. UNKNOWN must remain a possible state transition.
            if status == 'FAILED': continue
            item = {'at':at,'slot':raw['slot'],'signature':raw['signature'],
                    'index':raw.get('transactionIndex'),'status':status,'line':line}
            if keys is None: unknown.append(item)
            else:
                for key in keys: touches[key].append(item)
    return {'touches':touches,'writes':writes,'slots':slots,'faults':faults,'unknown':unknown}


def audit_role(index, key, at, source=None):
    """Observed-touch accountability, not a global absence-of-writes claim."""
    rows = index['writes'].get(key,[])
    known_touches = [r for r in index['touches'].get(key,[]) if r['at'] <= at]
    pending = []
    # Once a complete account snapshot is observed, earlier touches need not be
    # individually replayed to describe that snapshot. Same-slot RPC ordering is
    # unknown and therefore not used to dismiss a known touch.
    slot = source['account']['slot'] if source else -1
    sig = source['account'].get('transaction_signature') if source else None
    for touch in known_touches:
        if touch['slot'] < slot or touch['signature'] == sig: continue
        matches = [r for r in rows if r['transaction_signature']==touch['signature'] and r['slot']==touch['slot']]
        timely = [r for r in matches if r['at'] <= at]
        if not timely:
            pending.append({'signature':touch['signature'],'slot':touch['slot'],'status':touch['status'],
                            'reason':'WRITE_OBSERVED_LATER' if matches else 'NO_RETAINED_WRITE_FOR_WRITABLE_TOUCH',
                            'later_write_at_ns':min((r['at'] for r in matches),default=None)})
    # These later rows are diagnostics only, not additions to the chosen state.
    known_closed = [s['slot'] for s in index['slots'] if s['at']<=at and s['status'] in (0,1,2,4)]
    watermark = max(known_closed,default=-1)
    late = [r for r in rows if r['at']>at and r['slot']<=watermark]
    return {'known_writable_touches':len(known_touches),'unaccounted_touches':pending,
            'later_writes_at_or_before_observed_slot':len(late),
            'observed_slot_watermark':watermark,
            'complete_write_coverage_certified':False,
            'warning':'Writable privilege is not proof of an actual data mutation; slot messages are not delivery barriers.'}


def checkpoint_context(checkpoints, mint, curve, at, index):
    needed = (curve,mint,FEE,GLOBAL,CLOCK)
    candidates = [(line,r) for line,r in checkpoints if r.get('purpose')=='SYNCHRONIZED_SIGNAL_CONTROL_READ_SET'
                  and snapshot_available_at(r)<=at and set(needed)<=set(r.get('addresses',[]))]
    if not candidates:return {'reasons':['NO_PRIOR_SYNCHRONIZED_READ_SET'],'observation_quote_ready':False}
    line,r = max(candidates,key=lambda p:(snapshot_available_at(p[1]),p[0]))
    reasons=[]; decoded={}; accounts={}
    if r.get('error'): reasons.append('CHECKPOINT_RPC_ERROR')
    if not 0 <= r['request_at_ns'] <= r['response_available_at_ns'] <= at: reasons.append('INVALID_CHECKPOINT_CLOCK')
    if at-r['request_at_ns']>NS: reasons.append('STRICT_1S_REQUEST_AGE_FAIL')
    result=r.get('raw_rpc_result') or {}; slot=(result.get('context') or {}).get('slot')
    values=result.get('value') or []
    if len(values)!=len(r['addresses']): reasons.append('INCOMPLETE_READ_SET')
    if type(slot) is not int or slot < r.get('request_plan',{}).get('minimum_native_slot',2**64):
        reasons.append('RPC_BANK_BEHIND_REQUESTED_NATIVE_SLOT')
    for key,value in zip(r['addresses'],values):
        if key not in needed:continue
        if value is None:reasons.append('NULL_ACCOUNT:'+key);continue
        try:
            if value['data'][1]!='base64':raise Unsupported('ENCODING')
            accounts[key]={'pubkey':key,'owner':value['owner'],'slot':slot,'data_base64':value['data'][0]}
        except (KeyError,TypeError,IndexError,Unsupported):reasons.append('MALFORMED_ACCOUNT:'+key)
    for key in needed:
        if key not in accounts: reasons.append('MISSING_ACCOUNT:'+key)
    try:
        if all(k in accounts for k in needed):
            decoded={'curve':idl_account(accounts[curve],'BondingCurve',PUMP_LAYOUT),
                     'mint':mint_state(accounts[mint]),'fee':idl_account(accounts[FEE],'FeeConfig',FEE_LAYOUT),
                     'global':idl_account(accounts[GLOBAL],'Global',PUMP_LAYOUT)}
            import base64
            clock=base64.b64decode(accounts[CLOCK]['data_base64'],validate=True)
            if accounts[CLOCK]['owner']!='Sysvar1111111111111111111111111111111111111' or len(clock)!=40:
                raise Unsupported('INVALID_CLOCK')
            if int.from_bytes(clock[:8],'little')!=slot:raise Unsupported('CLOCK_BANK_SLOT_MISMATCH')
            if decoded['fee']['bump']!=253:raise Unsupported('FEE_PDA_BUMP_MISMATCH')
            rates=fee_rates(decoded['curve'],decoded['mint'],decoded['fee'])
            decoded['rates']=rates
    except (Unsupported,ValueError,KeyError,TypeError) as e:reasons.append('UNSUPPORTED_CONTEXT:'+str(e))
    # Never splice a newer account into an older bank-wide read set. A known
    # conflicting write/touch after its request makes that observation uncertain.
    conflicts=[]
    for key in needed[:-1]:
        for w in index['writes'].get(key,[]):
            if w['at']<=at and key in accounts and type(slot) is int and (
                    w['slot']>slot or (r['request_at_ns']<w['at'] and w['data_base64']!=accounts[key]['data_base64'])):
                conflicts.append({'key':key,'slot':w['slot'],'at':w['at'],'reason':'DIFFERENT_NATIVE_WRITE_DURING_OR_AFTER_READ'})
        for t in index['touches'].get(key,[]):
            if t['at']<=at and type(slot) is int and (t['slot']>slot or t['at']>r['request_at_ns']):
                conflicts.append({'key':key,'slot':t['slot'],'at':t['at'],'reason':'WRITABLE_TOUCH_AFTER_READ'})
    if conflicts:reasons.append('KNOWN_TRANSITION_CONFLICT')
    if any(r['request_at_ns']<=g['at']<=at for g in index['faults']):reasons.append('CAPTURE_DISCONTINUITY')
    if any(t['at']<=at and (r['request_at_ns']<=t['at'] or (type(slot) is int and t['slot']>slot)) for t in index['unknown']):
        reasons.append('UNRESOLVED_TRANSACTION_PRIVILEGES')
    if type(slot) is int and any(s['slot']==slot and s['status']==6 and s['at']<=at for s in index['slots']):reasons.append('KNOWN_DEAD_BANK')
    return {'source_line':line,'request_at_ns':r['request_at_ns'],'available_at_ns':snapshot_available_at(r),
            'bank_slot':slot,'reasons':sorted(set(reasons)),'observation_quote_ready':not reasons,
            'known_conflicts':conflicts,'decoded':decoded,
            'continuous_state_certified':False,'execution_fill_certified':False,
            'interpretation':'Causally available single-bank observation under strict age/known-conflict checks; not a complete execution path.'}


def replay_study(source, manifest):
    sync=manifest.get('synchronized_context')
    observer=Scheduler(sync['population'] if sync else 'A3h2_60s_engineering_canary_not_original78',
                       sync['signal_seconds'] if sync else 3600,None)
    for line,row in enumerate(jsonl(source/'capture.jsonl'),1):
        observer.consume(row,line,row.get('normalized_at_unix_ns',row.get('available_at_ns',0)))
    if sync:
        recorded=list(jsonl(source/'scheduler.jsonl'))
        fields=('seed_id','mint','control_mint','seed_at_ns','eligible_signal_period')
        if [{k:r[k] for k in fields} for r in recorded] != [{k:r[k] for k in fields} for r in observer.seed_logs]:
            raise ValueError('native replay does not reproduce actually scheduled signals/controls')
    return observer.study


def descendant_asof(index, child, ancestor, at):
    """Known native parent links only; no later finality/ancestry laundering."""
    parents=defaultdict(set)
    dead={s['slot'] for s in index['slots'] if s['at']<=at and s['status']==6}
    for s in index['slots']:
        if s['at']<=at and s.get('parent') is not None:parents[s['slot']].add(s['parent'])
    seen=set()
    while child!=ancestor:
        if child in dead or child in seen or child<ancestor or len(parents.get(child,set()))!=1:return False
        seen.add(child);child=next(iter(parents[child]))
    return ancestor not in dead


def native_advanced_context(checkpoints,mint,curve,at,index):
    """Separate engineering observation specification, not certified economics.

    Advance whole raw accounts, not inferred reserve deltas or stale prices.
    Every observed successful/unknown writable touch must be resolved, native
    parent lineage must be available, same-slot RPC ordering stays ambiguous.
    Global completeness/finality is deliberately a different, still-open check.
    """
    base=checkpoint_context(checkpoints,mint,curve,at,index)
    result={**base,'specification':'READSET_PLUS_NATIVE_TRANSITIONS_V1'}
    if 'source_line' not in base:return result
    # Known changes are addressed below, not ignored. Other base failures stand.
    reasons=[r for r in base['reasons'] if r!='KNOWN_TRANSITION_CONFLICT']
    snapshot=next(r for line,r in checkpoints if line==base['source_line'])
    bank=base['bank_slot'];selected={};evidence=[]
    if type(bank) is not int:return result
    raw={k:v for k,v in zip(snapshot['addresses'],(snapshot.get('raw_rpc_result') or {}).get('value') or [])}
    for role,key in (('curve',curve),('mint',mint),('fee',FEE),('global',GLOBAL)):
        value=raw.get(key)
        if not value:continue
        current={'pubkey':key,'owner':value['owner'],'data_base64':value['data'][0],'slot':bank}
        known=[w for w in index['writes'].get(key,[]) if w['at']<=at]
        newer=[w for w in known if w['slot']>bank]
        if newer:
            latest=max(newer,key=lambda w:(w['slot'],w['write_version']))
            # Receipt regression must not silently resurrect an older account.
            received_last=max(newer,key=lambda w:w['at'])
            if (received_last['slot'],received_last['write_version']) != (latest['slot'],latest['write_version']):
                reasons.append('NATIVE_ORDER_REGRESSION:'+role)
            if not descendant_asof(index,latest['slot'],bank,at):reasons.append('NO_CAUSAL_PARENT_PATH:'+role)
            if any(s['slot']==latest['slot'] and s['status']==6 and s['at']<=at for s in index['slots']):
                reasons.append('KNOWN_DEAD_NATIVE_STATE:'+role)
            current=latest
            if current.get('is_startup') is not False or not current.get('transaction_signature'):
                reasons.append('UNPROVEN_NATIVE_TRANSITION_IDENTITY:'+role)
        else:
            # Without a later-slot account, differing same-slot data cannot be
            # ordered against an RPC bank observation merely by receipt time.
            same=[w for w in known if w['slot']==bank]
            if same and max(same,key=lambda w:w['write_version'])['data_base64']!=current['data_base64']:
                reasons.append('SAME_SLOT_RPC_ORDER_UNRESOLVED:'+role)
        for touch in index['touches'].get(key,[]):
            if not (touch['at']<=at and (touch['at']>base['request_at_ns'] or touch['slot']>bank)):continue
            if touch['status']!='SUCCESS':
                reasons.append('UNKNOWN_EXECUTION_TOUCH:'+role);continue
            matches=[w for w in known if w.get('transaction_signature')==touch['signature'] and w['slot']==touch['slot']]
            if not matches:reasons.append('MISSING_NATIVE_WRITE_FOR_TOUCH:'+role)
            elif touch['slot']>current['slot']:reasons.append('UNAPPLIED_LATER_TOUCH:'+role)
            elif touch['slot']==bank and current['slot']==bank and matches[-1]['data_base64']!=current['data_base64']:
                reasons.append('SAME_SLOT_RPC_TOUCH_UNRESOLVED:'+role)
        selected[role]=current
        evidence.append({'role':role,'slot':current['slot'],'origin':'native' if 'at' in current else 'rpc_read_set',
                         'available_at_ns':current.get('at',base['available_at_ns']),
                         'write_version':current.get('write_version'),'signature':current.get('transaction_signature')})
    tip=max((r['slot'] for r in selected.values()),default=bank)
    for role,state in selected.items():
        if not descendant_asof(index,tip,state['slot'],at):reasons.append('INCOMPATIBLE_SELECTED_BRANCH:'+role)
    for role,key in (('curve',curve),('mint',mint),('fee',FEE),('global',GLOBAL)):
        for touch in index['touches'].get(key,[]):
            if touch['at']<=at and touch['slot']>bank and not descendant_asof(index,tip,touch['slot'],at):
                reasons.append('TOUCH_OUTSIDE_SELECTED_BRANCH:'+role)
    decoded={}
    try:
        if len(selected)!=4:raise Unsupported('INCOMPLETE_SELECTED_NATIVE_CONTEXT')
        decoded={'curve':idl_account(selected['curve'],'BondingCurve',PUMP_LAYOUT),
                 'mint':mint_state(selected['mint']),'fee':idl_account(selected['fee'],'FeeConfig',FEE_LAYOUT),
                 'global':idl_account(selected['global'],'Global',PUMP_LAYOUT)}
        decoded['rates']=fee_rates(decoded['curve'],decoded['mint'],decoded['fee'])
        if decoded['fee']['bump']!=253:raise Unsupported('FEE_PDA_BUMP_MISMATCH')
        curve_at=selected['curve'].get('at',base['request_at_ns'])
        if at-curve_at>NS:reasons.append('STRICT_1S_NATIVE_CURVE_AGE_FAIL')
    except (Unsupported,ValueError,KeyError,TypeError) as e:reasons.append('UNSUPPORTED_ADVANCED_CONTEXT:'+str(e))
    result.update(reasons=sorted(set(reasons)),observation_quote_ready=not reasons,
                  decoded=decoded,native_transition_sources=evidence,
                  interpretation='Causal bank read set plus observed, accounted descendant native writes; global completeness/fork validity and counterfactual economics still unverified.')
    return result


def run(source, output, web3_module, native_advance=False):
    output.mkdir(parents=True,exist_ok=False)
    inputs={name:sha(source/name) for name in ('capture.jsonl','bootstrap.jsonl','manifest.json','completion.json')}
    if (source/'scheduler.jsonl').exists():inputs['scheduler.jsonl']=sha(source/'scheduler.jsonl')
    histories,capture=decode_capture(source)
    index=native_index(jsonl(source/'capture.jsonl'))
    manifest=json.loads((source/'manifest.json').read_text()); sync=manifest.get('synchronized_context')
    study=replay_study(source,manifest)
    seeds=study.outcomes(); cutoff=(study.start or 0)+sync['signal_seconds']*NS if sync else study.end
    eligible=[s for s in seeds if s['seed_at_ns']<=cutoff]
    mints={m for s in seeds for m in (s['mint'],s['control_mint']) if m}
    mappings=derive_curves(mints,web3_module)['mappings'] if mints else {}
    checkpoints=list(enumerate(jsonl(source/'bootstrap.jsonl'),1))
    boundaries=[]; intentions=[]; quote_rows=[]; groups=defaultdict(Counter)
    # Teacher is annotation only. Never read these labels in Study or Scheduler.
    teacher='9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw';labels=[]
    for line,row in enumerate(jsonl(source/'capture.jsonl'),1):
        if row.get('kind')!='TRANSACTION':continue
        view=native_view(row,line)
        for e in view.get('pump_trade_event_prefixes',[]):
            if e.get('user')==teacher:
                labels.append({'signature':view['signature'],'mint':e['mint'],'side':e['side'],
                    'event_user':teacher,'execution_status':view['execution_status'],
                    'available_at_ns':view['metadata_available_at_ns'],
                    'executed_flow':view['execution_status']=='SUCCESS' and e['signed_sol_flow_eligible']})
    for seed in eligible:
        for arm in ('burst','persistence'):
            arm_data=seed['arms'][arm]; ready={}; selected={}
            for leg in ('decision','entry','exit'):
                at=arm_data[leg+'_at_ns']
                for role,mint in (('target',seed['mint']),('control',seed['control_mint'])):
                    label=f'{arm}:{leg}:{role}'
                    if not mint:
                        context={'observation_quote_ready':False,'reasons':['NO_FROZEN_CONTROL']};native=None;roles={}
                    else:
                        curve=mappings[mint]['curve']
                        native=causal_context(histories,mint,curve,at,capture['end_ns'])
                        roles={name:audit_role(index,key,at,available(histories.get(key,[]),at)) for name,key in
                               (('curve',curve),('mint',mint),('fee',FEE),('global',GLOBAL))}
                        context=checkpoint_context(checkpoints,mint,curve,at,index)
                        if at>capture['end_ns']:
                            context['observation_quote_ready']=False;context['reasons'].append('RIGHT_CENSORED')
                    snapshot_context={k:v for k,v in context.items() if k!='decoded'}
                    if native_advance and mint:
                        context=native_advanced_context(checkpoints,mint,mappings[mint]['curve'],at,index)
                        if at>capture['end_ns']:
                            context['observation_quote_ready']=False;context['reasons'].append('RIGHT_CENSORED')
                    selected[(leg,role)]=context
                    public={k:v for k,v in context.items() if k!='decoded'}
                    boundaries.append({'seed_id':seed['seed_id'],'mint':mint,'boundary':label,'at_ns':at,
                                       'native_context':native,'native_touch_audit':roles,'synchronized_context':public,
                                       'snapshot_only_context':snapshot_context})
                    g=groups[label];g['denominator']+=1;g['ready_observation']+=context['observation_quote_ready'];g.update(context['reasons'])
            for size in SIZES:
                status='UNRESOLVED_ECONOMICS';reason=[];buy=None
                if arm=='persistence' and seed['confirmation']=='REJECTED':
                    status='POLICY_ABSTENTION';reason=['FROZEN_PERSISTENCE_REJECTED']
                elif arm=='persistence' and seed['confirmation']=='UNKNOWN':reason=['UNKNOWN_PERSISTENCE_CONFIRMATION']
                else:
                    entry=selected[('entry','target')]
                    if entry['observation_quote_ready']:
                        d=entry['decoded']
                        try:buy=quote(d['curve'],'buy_exact_sol_in',size,0,*d['rates'])
                        except Unsupported as e:status='MODEL_NON_EXECUTABLE';reason=[str(e)]
                    else:reason=['ENTRY_CONTEXT_UNRESOLVED']
                    if buy is not None:
                        reason=['OWN_STATE_HOLD_TRANSITIONS_AND_NETWORK_COSTS_UNVERIFIED']
                        if not selected[('decision','target')]['observation_quote_ready']:reason.append('DECISION_CONTEXT_UNRESOLVED')
                        if not selected[('exit','target')]['observation_quote_ready']:reason.append('EXIT_CONTEXT_UNRESOLVED')
                        quote_rows.append({'seed_id':seed['seed_id'],'arm':arm,'size_lamports':size,
                            'boundary_ns':arm_data['entry_at_ns'],'snapshot_source_line':entry['source_line'],
                            'quote':buy,'meaning':'Nonlinear observation quote only; not a submitted/filled order or net outcome.'})
                intentions.append({'seed_id':seed['seed_id'],'arm':arm,'size_lamports':size,
                    'status':status,'reasons':reason,'entry_quote_present':buy is not None,
                    'control_mint':seed['control_mint'],'net_pnl_lamports':None,'submitted':False,
                    'target_context_chain_ready':all(selected[(leg,'target')]['observation_quote_ready'] for leg in ('decision','entry','exit')),
                    'control_context_chain_ready':all(selected[(leg,'control')]['observation_quote_ready'] for leg in ('decision','entry','exit'))})
    # Do not export forward-price returns as a new endpoint during engineering.
    seed_rows=[{k:v for k,v in s.items() if k not in ('arms','continuation')} for s in seeds]
    for s in seed_rows:s['eligible_signal_period']=s['seed_at_ns']<=cutoff
    write_jsonl(output/'opportunities.jsonl',seed_rows);write_jsonl(output/'boundaries.jsonl',boundaries)
    write_jsonl(output/'intentions.jsonl',intentions);write_jsonl(output/'observation-quotes.jsonl',quote_rows)
    write_jsonl(output/'teacher-labels.jsonl',labels)
    if len(intentions)!=len(eligible)*6:raise ValueError('denominator changed')
    if inputs!={name:sha(source/name) for name in inputs}:raise ValueError('inputs changed during audit')
    report={'schema':'NATIVE_CONTINUITY_READSET_AUDIT_V1','inputs':inputs,
        'implementation_sha256':{name:sha(Path(__file__).parent/name) for name in (
            'state_continuity_v1.py','synchronized_context_v1.py','quote_context_adapter_v1.py',
            'burst_persistence_v1.py','transaction_evidence_v1.py')},
        'population':study.population,'native_advance_specification':native_advance,
        'opportunities':len(eligible),'drain_only_seeds':len(seeds)-len(eligible),
        'intentions':len(intentions),'without_control':sum(s['control_mint'] is None for s in eligible),
        'boundary_counts':dict(groups),'intent_statuses':dict(Counter(i['status'] for i in intentions)),
        'observation_quotes':len(quote_rows),'teacher_event_labels':len(labels),
        'context_chain_intentions':{arm:{'target':sum(i['target_context_chain_ready'] for i in intentions if i['arm']==arm),
            'matched':sum(i['target_context_chain_ready'] and i['control_context_chain_ready'] for i in intentions if i['arm']==arm)} for arm in ('burst','persistence')},
        'recorded_faults':index['faults'],'unknown_transaction_privileges':len(index['unknown']),
        'native_capture':capture,'continuous_state_certified':False,'net_economics_certified':False,
        'prospective_expansion_allowed':False,
        'remaining':['Full transition/fork qualification and own-state hold replay are required; single-bank reads are not fills.',
            'Execution/network costs, intervening unsupported transactions and non-executable/unknown outcomes must be accounted.',
            'Original frozen controls and strict age remain unchanged; old78/468 never replaced.'],
        'outputs':{p.name:sha(p) for p in sorted(output.glob('*.jsonl'))}}
    def timing(values):
        values=sorted(values)
        return {'count':len(values),'median_ms':values[len(values)//2]/1e6 if values else None,
                'maximum_ms':max(values)/1e6 if values else None,
                'over_1s':sum(v>NS for v in values)}
    report['timing']={'rpc_response_latency':timing([r['response_available_at_ns']-r['request_at_ns'] for _,r in checkpoints]),
        'synchronized_response_latency':timing([r['response_available_at_ns']-r['request_at_ns'] for _,r in checkpoints if r.get('purpose')=='SYNCHRONIZED_SIGNAL_CONTROL_READ_SET']),
        'scheduler_signal_observation_lag':timing([r['observer_lag_ns'] for r in jsonl(source/'scheduler.jsonl')]) if (source/'scheduler.jsonl').exists() else None}
    (output/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('source','output','web3-module'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--native-advance',action='store_true')
    a=p.parse_args();print(json.dumps(run(a.source,a.output,a.web3_module,a.native_advance),sort_keys=True))
