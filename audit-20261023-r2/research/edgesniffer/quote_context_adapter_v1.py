"""Offline A3h3: independent arithmetic reconstruction != causal quote admission.

No RPC, signer, collector changes, forward fills, rematching, or net PnL.
Pinned IDLs describe layouts, not proof of deployed-program equivalence.
"""
import argparse
import base64
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from account_boundary_audit_v1 import derive_curves
from quote_context_v1 import jsonl, sha
from transaction_evidence_v1 import PUMP, SOL_QUOTES, b58decode, b58encode, normalize, digest
from ledger_v1 import SIZES
from burst_persistence_v1 import Study, NS

ROOT = Path(__file__).parent
CONTRACT = ROOT/'quote-adapter-contract-v1.json'
PUMP_IDL = ROOT/'reference/pump-639b87f.json'
FEE_IDL = ROOT/'reference/pump-fees-639b87f.json'
TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN22 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
FEE_PROGRAM = 'pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ'
GLOBAL = '4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf'
FEE = '8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt'
CLOCK = 'SysvarC1ock11111111111111111111111111111111'
RESERVES = ('virtual_quote_reserves', 'virtual_token_reserves', 'real_quote_reserves', 'real_token_reserves')


class Unsupported(ValueError):
    pass


class Reader:
    def __init__(self, data): self.data, self.at = data, 0
    def take(self, n):
        if self.at+n > len(self.data): raise Unsupported('TRUNCATED_LAYOUT')
        out=self.data[self.at:self.at+n]; self.at+=n; return out
    def uint(self,n): return int.from_bytes(self.take(n),'little')
    def value(self, typ, definitions):
        if isinstance(typ,str):
            if typ in ('u8','u16','u32','u64','u128'): return self.uint(int(typ[1:])//8)
            if typ == 'pubkey': return b58encode(self.take(32))
            if typ == 'bool':
                val=self.uint(1)
                if val not in (0,1): raise Unsupported('INVALID_BOOL')
                return bool(val)
        if isinstance(typ,dict):
            if 'defined' in typ:
                name=typ['defined']['name']; return self.struct(definitions[name],definitions)
            if 'array' in typ: return [self.value(typ['array'][0],definitions) for _ in range(typ['array'][1])]
            if 'vec' in typ:
                n=self.uint(4)
                if n>256: raise Unsupported('UNBOUNDED_VECTOR')
                return [self.value(typ['vec'],definitions) for _ in range(n)]
        raise Unsupported('UNSUPPORTED_IDL_TYPE')
    def struct(self, definition, definitions):
        if definition['kind']!='struct': raise Unsupported('UNSUPPORTED_IDL_DEFINITION')
        return {f['name']:self.value(f['type'],definitions) for f in definition['fields']}


def idl_account(account, name, idl):
    if account['owner'] != idl['address']: raise Unsupported('WRONG_OWNER')
    data=base64.b64decode(account['data_base64'],validate=True)
    discriminator=bytes(next(a['discriminator'] for a in idl['accounts'] if a['name']==name))
    r=Reader(data)
    if r.take(8)!=discriminator: raise Unsupported('WRONG_DISCRIMINATOR')
    defs={t['name']:t['type'] for t in idl['types']}
    value=r.struct(defs[name],defs)
    if any(data[r.at:]): raise Unsupported('UNKNOWN_NONZERO_LAYOUT_TAIL')
    return value


def mint_state(account):
    if account['owner'] not in (TOKEN,TOKEN22): raise Unsupported('WRONG_MINT_OWNER')
    data=base64.b64decode(account['data_base64'],validate=True)
    if len(data)<82 or data[45]!=1: raise Unsupported('UNINITIALIZED_OR_SHORT_MINT')
    for at in (0,46):
        if int.from_bytes(data[at:at+4],'little') not in (0,1): raise Unsupported('BAD_MINT_OPTION')
    out={'supply':int.from_bytes(data[36:44],'little'),'decimals':data[44],
         'freeze_authority_present':bool(int.from_bytes(data[46:50],'little')), 'extensions':[], 'transfer_fee':None}
    if account['owner']==TOKEN:
        if len(data)!=82: raise Unsupported('LEGACY_MINT_LENGTH')
        return out
    if len(data)==82: return out
    if len(data)<166 or any(data[82:165]) or data[165]!=1: raise Unsupported('BAD_MINT_TLV_HEADER')
    r=Reader(data);r.at=166
    while r.at<len(data):
        if not any(data[r.at:]): break
        kind=r.uint(2);n=r.uint(2);payload=r.take(n)
        if kind==0 or kind in out['extensions']: raise Unsupported('DUPLICATE_OR_INVALID_TLV')
        out['extensions'].append(kind)
        if kind==1:
            if n!=108: raise Unsupported('BAD_TRANSFER_FEE_LENGTH')
            def schedule(at):
                q=Reader(payload[at:]); return {'epoch':q.uint(8),'maximum_fee':q.uint(8),'bps':q.uint(2)}
            out['transfer_fee']={'older':schedule(72),'newer':schedule(90)}
            if any(v['bps']>10000 for v in out['transfer_fee'].values()): raise Unsupported('INVALID_TRANSFER_FEE_BPS')
        elif kind==18 and n!=64:raise Unsupported('BAD_METADATA_POINTER_LENGTH')
        elif kind==19:
            metadata=Reader(payload);metadata.take(32);metadata_mint=b58encode(metadata.take(32))
            if account.get('pubkey') and metadata_mint!=account['pubkey']:raise Unsupported('METADATA_MINT_IDENTITY_MISMATCH')
            def string():
                try:return metadata.take(metadata.uint(4)).decode('utf8')
                except UnicodeError:raise Unsupported('INVALID_METADATA_UTF8')
            for _ in range(3):string()
            extra=metadata.uint(4)
            if extra>1024:raise Unsupported('OVERSIZE_METADATA_VECTOR')
            for _ in range(extra):string();string()
            if metadata.at!=len(payload):raise Unsupported('UNKNOWN_METADATA_TAIL')
    # Only fee and metadata extensions supported for arithmetic. Other extensions
    # may affect transfers; retaining their IDs is not an execution certification.
    out['unsupported_extensions']=[k for k in out['extensions'] if k not in (1,18,19)]
    return out


def transfer_fee(amount, mint, epoch):
    config=mint.get('transfer_fee')
    if not config: return 0
    if epoch is None: raise Unsupported('MISSING_EPOCH')
    rule=config['newer'] if epoch>=config['newer']['epoch'] else config['older']
    return min(rule['maximum_fee'],ceil_div(amount*rule['bps'],10000))


def ceil_div(n,d):
    if n<0 or d<=0: raise Unsupported('INVALID_INTEGER_DIVISION')
    return (n+d-1)//d


def quote(state, kind, amount, limit, protocol_bps, creator_bps):
    """Narrow candidate formula. On-chain reconstruction report certifies scope.

    Uses only pre-state, raw requested arguments and separately supplied fees.
    Event amounts and post-state never enter this calculation.
    """
    if any(type(state.get(k)) is not int or state[k]<0 for k in RESERVES): raise Unsupported('INVALID_RESERVES')
    q,t,rq,rt=(state[k] for k in RESERVES)
    if not q or not t or rq>q or rt>t: raise Unsupported('INVALID_RESERVE_INVARIANTS')
    if not 0<=protocol_bps+creator_bps<=10000 or min(protocol_bps,creator_bps)<0: raise Unsupported('INVALID_FEES')
    if not 0<amount<2**64 or not 0<=limit<2**64: raise Unsupported('INVALID_INSTRUCTION_AMOUNT')
    sell=kind in ('sell','sell_v2')
    exact=kind in ('buy_exact_sol_in','buy_exact_quote_in_v2')
    if kind not in ('buy','buy_v2','sell','sell_v2','buy_exact_sol_in','buy_exact_quote_in_v2'):
        raise Unsupported('UNVALIDATED_INSTRUCTION_VARIANT')
    if sell:
        token=amount; curve=amount*q//(t+amount)
        if curve>rq or not curve: raise Unsupported('INSUFFICIENT_REAL_QUOTE')
    elif exact:
        curve=amount*10000//(10000+protocol_bps+creator_bps)
        for _ in range(4):
            overflow=curve+ceil_div(curve*protocol_bps,10000)+ceil_div(curve*creator_bps,10000)-amount
            if overflow<=0: break
            curve-=overflow
        if curve<=1: raise Unsupported('ZERO_OUTPUT')
        token=(curve-1)*t//(q+curve-1)
    else:
        token=amount
        if token>=t: raise Unsupported('INSUFFICIENT_VIRTUAL_TOKEN')
        curve=ceil_div(token*q,t-token)
    protocol=ceil_div(curve*protocol_bps,10000);creator=ceil_div(curve*creator_bps,10000)
    if sell:
        net=curve-protocol-creator
        if net<limit: raise Unsupported('MIN_OUTPUT_VIOLATION')
        post=dict(zip(RESERVES,(q-curve,t+token,rq-curve,rt+token)))
    else:
        if token<=0 or token>rt: raise Unsupported('INSUFFICIENT_REAL_TOKEN_OR_ZERO')
        net=curve+protocol+creator
        if (exact and (token<limit or net>amount)) or (not exact and net>limit): raise Unsupported('BUY_LIMIT_VIOLATION')
        post=dict(zip(RESERVES,(q+curve,t-token,rq+curve,rt-token)))
    if any(x>=2**64 for x in post.values()): raise Unsupported('U64_OVERFLOW')
    return {'curve_amount':curve,'token_amount':token,'protocol_fee':protocol,'creator_fee':creator,
            'user_quote_amount_before_network_costs':net,'post':post}


def decode_instruction(ix, idl):
    raw=b58decode(ix['data_base58'])
    spec=next((i for i in idl['instructions'] if bytes(i['discriminator'])==raw[:8]),None)
    if not spec or not spec['name'].startswith(('buy','sell')): raise Unsupported('NOT_TRADE_VARIANT')
    if len(raw)<24: raise Unsupported('SHORT_TRADE_INSTRUCTION')
    accounts=ix.get('accounts') or []
    if len(accounts)<len(spec['accounts']): raise Unsupported('MISSING_INSTRUCTION_ACCOUNTS')
    roles={a['name']:accounts[i] for i,a in enumerate(spec['accounts'])}
    # Absent optional trailing flags are legacy encodings; nonzero partial-fill
    # flags and unknown tails are not simulated as full fills.
    flags=raw[24:]
    max_flags=2 if spec['name'] in ('buy','buy_exact_sol_in') else 1 if spec['name'].startswith('buy') else 0
    if len(flags)>max_flags or any(x not in (0,1) for x in flags): raise Unsupported('UNKNOWN_INSTRUCTION_TAIL')
    partial=(flags[1] if len(flags)>1 else 0) if max_flags==2 else (flags[0] if flags else 0)
    if partial: raise Unsupported('PARTIAL_FILL_UNSUPPORTED')
    return {'kind':spec['name'],'amount':int.from_bytes(raw[8:16],'little'),
            'limit':int.from_bytes(raw[16:24],'little'),'roles':roles,
            'outer_index':ix['outer_index'],'inner_index':ix['inner_index']}


def available(history, at):
    """No future bootstrap, response or native decode may enter a past boundary."""
    rows=[r for r in history if r['available_at_ns']<=at]
    return max(rows,key=lambda r:(r['available_at_ns'],r['source_line'])) if rows else None


def snapshot_available_at(row):
    response=row['response_available_at_ns'];published=row.get('observer_published_at_ns',response)
    if published<response:raise ValueError('observer publication precedes RPC response')
    return published


def causal_context(histories, mint, curve, at, capture_end):
    """Presence/semantic gate only. Never equates recent bytes with continuity."""
    reasons=[];selected={}
    if at>capture_end:reasons.append('RIGHT_CENSORED_CAPTURE')
    for role,key in (('curve',curve),('mint',mint),('fee',FEE),('global',GLOBAL),('clock',CLOCK)):
        r=available(histories.get(key,[]),at);selected[role]=r
        if not r:reasons.append('NO_PRIOR_'+role.upper())
        elif r['decoded'] is None:reasons.append('INVALID_'+role.upper()+':'+str(r['reason']))
    if selected['curve']:
        row=selected['curve'];account=row['account']
        if at-row['available_at_ns']>NS:reasons.append('ORIGINAL_1S_STATE_AGE_FAIL')
        known=[r for r in histories.get(curve,[]) if r['available_at_ns']<=at and r['origin']=='native']
        if any(r['account']['slot']>account['slot'] or
               (r['account']['slot']==account['slot'] and type(account.get('write_version')) is int
                and r['account']['write_version']>account['write_version']) for r in known):
            reasons.append('NATIVE_WRITE_ORDER_REGRESSION')
        if account.get('write_version') is None and any(r['account']['slot']==account['slot'] for r in known):
            reasons.append('RPC_NATIVE_SAME_SLOT_ORDER_UNRESOLVED')
    if all(selected[k] and selected[k]['decoded'] is not None for k in selected):
        try:fee_rates(selected['curve']['decoded'],selected['mint']['decoded'],selected['fee']['decoded'])
        except Unsupported as e:reasons.append(str(e))
    return {'available_input_reasons':reasons,'inputs_present_and_semantically_supported':not reasons,
        'sources':{k:None if not r else {'origin':r['origin'],'source_line':r['source_line'],
            'available_at_ns':r['available_at_ns'],'slot':r['account']['slot'],'reason':r['reason']} for k,r in selected.items()},
        'continuity_status':'UNRESOLVED_WRITE_FORK_AND_DYNAMIC_SUBSCRIPTION_COVERAGE',
        'causal_quote_certified':False}


def canary_boundaries(histories,transactions,mappings,capture,output):
    study=Study('A3h2_60s_engineering_canary_not_original78')
    for tx in transactions:study.consume(tx)
    rows=[];counts=defaultdict(Counter)
    for seed in study.outcomes():
        for arm in ('burst','persistence'):
            for leg,at in (('decision',seed['arms'][arm]['entry_at_ns']-NS),
                           ('entry',seed['arms'][arm]['entry_at_ns']),('exit',seed['arms'][arm]['exit_at_ns'])):
                for role,mint in (('target',seed['mint']),('control',seed['control_mint'])):
                    label=f'{arm}:{leg}:{role}';bucket=counts[label];bucket['denominator']+=1
                    if mint:
                        context=causal_context(histories,mint,mappings[mint]['curve'],at,capture['end_ns'])
                    else:context={'available_input_reasons':['NO_ORIGINAL_ELIGIBLE_CONTROL'],
                        'inputs_present_and_semantically_supported':False,'causal_quote_certified':False}
                    rows.append({'seed_id':seed['seed_id'],'mint':mint,'boundary':label,'deadline_ns':at,**context})
                    bucket['present_supported' if context['inputs_present_and_semantically_supported'] else 'missing_or_unsupported']+=1
                    bucket.update(context['available_input_reasons'])
    write_jsonl(output/'separate-canary-boundaries.jsonl',rows)
    return {'separate_population':study.population,'opportunities':len(study.seeds),'intentions':len(study.seeds)*6,
        'causal_quotes_certified':0,'by_boundary':{k:dict(v) for k,v in counts.items()},
        'interpretation':'Existing frozen signal/control rules applied only for context coverage; no new return endpoint or strategy selection.'}


def decode_capture(source):
    pump=json.loads(PUMP_IDL.read_text());fee=json.loads(FEE_IDL.read_text())
    histories=defaultdict(list);counts=Counter();failures=Counter();times=[];slot_counts=Counter()
    continuity=Counter();last_seq=0;last_clock=None;orders={};transaction_digest=hashlib.sha256();tx_count=0
    def add(account, at, line, origin):
        key=account['pubkey']; data=base64.b64decode(account['data_base64'],validate=True)
        if account.get('data_sha256') and hashlib.sha256(data).hexdigest()!=account['data_sha256']: raise ValueError('raw account hash changed')
        role='other';decoded=None;reason=None
        try:
            if key==FEE:
                role='fee';decoded=idl_account(account,'FeeConfig',fee)
                if decoded['bump']!=253:raise Unsupported('FEE_PDA_BUMP_MISMATCH')
            elif key==GLOBAL: role='global';decoded=idl_account(account,'Global',pump)
            elif key==CLOCK:
                role='clock'
                if account['owner']!='Sysvar1111111111111111111111111111111111111' or len(data)!=40: raise Unsupported('INVALID_CLOCK')
                decoded={'slot':int.from_bytes(data[:8],'little'),'epoch':int.from_bytes(data[16:24],'little')}
            elif account['owner'] in (TOKEN,TOKEN22): role='mint';decoded=mint_state(account)
            elif account['owner']==PUMP:
                curve_disc=bytes(next(a['discriminator'] for a in pump['accounts'] if a['name']=='BondingCurve'))
                if data[:8]==curve_disc: role='curve';decoded=idl_account(account,'BondingCurve',pump)
        except (Unsupported,ValueError) as e: reason=str(e);failures[role+':'+reason]+=1
        counts[role]+=1
        if role!='other':histories[key].append({'account':account,'available_at_ns':at,'source_line':line,
            'origin':origin,'role':role,'decoded':decoded,'reason':reason})
    for line,row in enumerate(jsonl(source/'capture.jsonl'),1):
        kind=row.get('kind')
        if kind in ('GAP','CONNECTED','CONTEXT_SUBSCRIPTION_REQUESTED','SCOPE_LIMIT'):continuity[kind]+=1
        if 'received_at_unix_ns' in row:
            now=row['received_at_unix_ns'];times.append(now)
            if last_clock is not None and now<last_clock:continuity['CLOCK_REGRESSION']+=1
            last_clock=now
            if row['normalized_at_unix_ns']<now:raise ValueError('native decode before receipt')
        if 'sequence' in row:
            if row['sequence']!=last_seq+1:continuity['SEQUENCE_DISCONTINUITY']+=1
            last_seq=row['sequence']
        if kind=='TRANSACTION':
            view=normalize(row['transaction'])
            view.update(capture_line=line,received_at_unix_ns=row['received_at_unix_ns'],
                metadata_available_at_ns=row['normalized_at_unix_ns'],session_id=row['session_id'],
                source_commitment='processed',finality_known_at_decision=False)
            transaction_digest.update(digest(view).encode());tx_count+=1
        if kind=='ACCOUNT':
            a=row['account'];order=(a['slot'],a['write_version']);key=a['pubkey']
            if key in orders and order<orders[key]:continuity['ACCOUNT_ORDER_REGRESSION']+=1
            if key in orders and order==orders[key]:continuity['REPEATED_ACCOUNT_ORDER']+=1
            orders[key]=max(order,orders.get(key,order))
        if row.get('kind')=='ACCOUNT':add(row['account'],max(row['received_at_unix_ns'],row['normalized_at_unix_ns']),line,'native')
        if row.get('kind')=='SLOT':slot_counts[str(row.get('slot_state',{}).get('status'))]+=1
    for line,row in enumerate(jsonl(source/'bootstrap.jsonl'),1):
        if row.get('error'): continue
        if row['response_available_at_ns']<row['request_at_ns']: raise ValueError('bootstrap clock regression')
        result=row['raw_rpc_result'];values=result['value']
        if len(values)!=len(row['addresses']):raise ValueError('bootstrap shape')
        for key,val in zip(row['addresses'],values):
            if val is None: continue
            if val['data'][1]!='base64':raise ValueError('bootstrap encoding')
            add({'pubkey':key,'owner':val['owner'],'data_base64':val['data'][0],
                 'slot':result['context']['slot'],'write_version':None,'transaction_signature':None},
                snapshot_available_at(row),line,'rpc_response')
    return histories,{'counts':dict(counts),'layout_failures':dict(failures),
        'start_ns':min(times),'end_ns':max(times),'slot_status_counts':dict(slot_counts),
        'observed_discontinuities_and_requests':dict(continuity),
        'server_subscription_activation_acknowledged':False,'full_write_and_fork_coverage_certified':False,
        'raw_to_normalized_transactions':tx_count,'normalized_transaction_digest':transaction_digest.hexdigest(),
        'mint_extension_types':dict(Counter(str(k) for rows in histories.values() for r in rows if r['role']=='mint' and r['decoded'] for k in r['decoded']['extensions']))}


def write_jsonl(path,rows):
    with path.open('w',encoding='utf8',newline='\n') as out:
        for row in rows:out.write(json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')


def fee_rates(state, mint, config):
    if state.get('is_mayhem_mode') or state.get('is_cashback_coin') or state.get('is_holder_reward'):
        raise Unsupported('UNSUPPORTED_SPECIAL_FEE_MODE')
    if state.get('complete') or state.get('depth') or state.get('post_complete_base_out') or state.get('post_complete_quote_in'):
        raise Unsupported('UNSUPPORTED_CURVE_PHASE')
    if state.get('quote_mint') not in SOL_QUOTES: raise Unsupported('NON_SOL_QUOTE')
    if mint.get('unsupported_extensions'): raise Unsupported('UNSUPPORTED_MINT_EXTENSIONS')
    if mint.get('transfer_fee'): raise Unsupported('TRANSFER_FEE_QUOTE_NOT_ONCHAIN_VALIDATED')
    if state['token_total_supply']!=mint['supply']: raise Unsupported('SUPPLY_MISMATCH')
    tiers=config['fee_tiers']
    thresholds=[t['market_cap_lamports_threshold'] for t in tiers]
    if not tiers or thresholds!=sorted(set(thresholds)):raise Unsupported('INVALID_FEE_TIERS')
    market_cap=state['virtual_quote_reserves']*mint['supply']//state['virtual_token_reserves']
    selected=tiers[0]
    for tier in tiers:
        if market_cap>=tier['market_cap_lamports_threshold']:selected=tier
    fee=selected['fees']
    if fee['lp_fee_bps']: raise Unsupported('UNSUPPORTED_CURVE_LP_FEE')
    # Configurable creator-fee semantics must be separately reconstructed.
    if state.get('creator_fee_bps') or state.get('can_edit_creator_fee'):raise Unsupported('CONFIGURABLE_CREATOR_FEE_UNVALIDATED')
    creator=fee['creator_fee_bps'] if state['creator']!='11111111111111111111111111111111' else 0
    return fee['protocol_fee_bps'],creator


def original_coverage(canonical, prior_context, histories, capture, output):
    canonical_rows=list(jsonl(canonical/'boundaries.jsonl'))
    prior=list(jsonl(prior_context/'boundary-context.jsonl'))
    attempts=list(jsonl(prior_context/'attempts.jsonl'))
    if len(canonical_rows)!=78 or len({r['seed_id'] for r in canonical_rows})!=78:raise ValueError('original78 denominator changed')
    expected={(r['seed_id'],arm,size) for r in canonical_rows for arm in ('burst','persistence') for size in SIZES}
    if len(attempts)!=468 or {(r['seed_id'],r['arm'],r['size_lamports']) for r in attempts}!=expected:raise ValueError('original468 intentions changed')
    old_context={(r['seed_id'],r['boundary']):r for r in prior}
    output_rows=[];groups=defaultdict(Counter);deadline_all=[];decisions=[]
    for row in canonical_rows:
        for label,mark in sorted(row['boundaries'].items()):
            arm,leg,role=label.split(':');deadline=mark['deadline_ns'];deadline_all.append(deadline)
            mint=row['mint'] if role=='target' else row['control_mint']
            reasons=[];raw={}
            if not mint:reasons.append('NO_ORIGINAL_ELIGIBLE_CONTROL')
            else:
                context=old_context[(row['seed_id'],label)]
                # Old normalized observations remain observations, not certified raw inputs.
                for name,key in (('curve',mark.get('curve')),('mint',mint),('fee',FEE),('global',GLOBAL),('clock',CLOCK)):
                    found=available(histories.get(key,[]),deadline)
                    raw[name]=None if not found else {'source':found['origin'],'source_line':found['source_line'],
                        'available_at_ns':found['available_at_ns'],'layout_valid':found['decoded'] is not None}
                    if not found: reasons.append('NO_CAUSAL_VALIDATED_RAW_'+name.upper())
                    elif found['decoded'] is None:reasons.append('INVALID_RAW_'+name.upper())
                if not context['state'].get('complete_write_coverage_certified'):reasons.append('STATE_CONTINUITY_UNCERTIFIED')
                if not context['state'].get('original_1s_age_pass'):reasons.append('ORIGINAL_1S_STATE_AGE_FAIL')
                reasons.extend(('FEE_UPDATE_CONTINUITY_UNCERTIFIED','MINT_UPDATE_CONTINUITY_UNCERTIFIED'))
            result={'seed_id':row['seed_id'],'mint':mint,'boundary':label,'deadline_ns':deadline,
                'observed_native_reserve_state':mark.get('status')=='OBSERVED_UNCERTIFIED_ACCOUNT_STATE',
                'old_retained_mint_row_prior':bool(old_context.get((row['seed_id'],label),{}).get('retained_mint_context',{}).get('prior_row_available_at_ns')),
                'new_capture_raw_inputs':raw,'causal_input_complete':not reasons,'unresolved_reasons':reasons}
            output_rows.append(result);c=groups[label];c['denominator']+=1;c['complete' if not reasons else 'unresolved']+=1
            c.update(reasons)
            if leg=='entry_at_ns':
                decision_at=deadline-NS
                decisions.append({'seed_id':row['seed_id'],'arm':arm,'role':role,'mint':mint,'deadline_ns':decision_at,
                    'new_raw_context':causal_context(histories,mint,mark.get('curve'),decision_at,capture['end_ns']) if mint else None,
                    'complete_causal_inputs':False,'reason':'Original retained quote-context provenance remains uncertified; no backdating'})
    bykey={(r['seed_id'],r['boundary']):r for r in output_rows}
    intent_rows=[]
    for row in attempts:
        legs=[bykey[(row['seed_id'],f"{row['arm']}:{leg}_at_ns:target")] for leg in ('entry','exit')]
        intent_rows.append({**row,'causal_entry_complete':legs[0]['causal_input_complete'],
            'causal_exit_complete':legs[1]['causal_input_complete'],
            'unresolved_reasons':sorted(set(r for leg in legs for r in leg['unresolved_reasons'])),
            'quote_status':'UNRESOLVED_CONTEXT','net_pnl_sol':None})
    write_jsonl(output/'original-boundary-coverage.jsonl',output_rows)
    write_jsonl(output/'original-intentions.jsonl',intent_rows)
    write_jsonl(output/'original-decisions.jsonl',decisions)
    start,end=min(deadline_all),max(deadline_all)
    utc=lambda ns:datetime.fromtimestamp(ns/1e9,timezone.utc).isoformat()
    return {'opportunities':78,'intentions':468,'complete_causal_intentions':sum(r['causal_entry_complete'] and r['causal_exit_complete'] for r in intent_rows),
        'valid_quotes':0,'known_nonexecutable_quotes':0,'unresolved_economics':468,'submitted_orders':0,
        'by_boundary':{k:dict(v) for k,v in sorted(groups.items())},
        'original_boundary_range_ns':[start,end],'original_boundary_range_utc':[utc(start),utc(end)],
        'capture_range_utc':[utc(capture['start_ns']),utc(capture['end_ns'])],
        'capture_overlap_ns':max(0,min(end,capture['end_ns'])-max(start,capture['start_ns'])),
        'original_boundaries_in_capture':sum(capture['start_ns']<=t<=capture['end_ns'] for t in deadline_all),
        'capture_after_last_boundary_seconds':(capture['start_ns']-end)/1e9,
        'reason_counts_overlap':True,'historical_recovery_backdated':False,
        'original_decision_boundaries':len(decisions),'original_decisions_complete':0,
        'retained_context_note':'Old normalized reserves and8 prior decoded mint rows are preserved; old raw fee snapshot lacks update continuity. No claim those records never existed.'}


def reconstruction(histories, transactions, references, mappings, output):
    idl=json.loads(PUMP_IDL.read_text());by_sig={t['signature']:t for t in transactions}
    refs={};block_txs={}
    for ref in (r for path in references for r in jsonl(path)):
        if not ref.get('raw_block'):continue
        rows=[]
        for i,tx in enumerate(ref['raw_block']['transactions']):
            v=normalize({**tx,'slot':ref['slot']});v['transaction_index']=i;rows.append(v)
            refs[v['signature']]=(ref['slot'],i,v,ref['reference_available_at_ns'])
        block_txs[ref['slot']]=rows
    cases=[];synthetic=[];fixtures=[];joins=Counter()
    for tx in transactions:
        if tx['execution_status']!='SUCCESS':continue
        for e in tx['pump_trade_event_prefixes']:
            joins['successful_events']+=1
            curve=mappings[e['mint']]['curve']
            posts=[r for r in histories.get(curve,[]) if r['account'].get('transaction_signature')==tx['signature'] and r['account']['slot']==tx['slot']]
            if len(posts)!=1 or not posts[0]['decoded']:joins['no_unique_decoded_canonical_post']+=1;continue
            if all(e.get(k)==posts[0]['decoded'][k] for k in RESERVES):joins['four_reserves_match']+=1
            else:
                same=[event for event in tx['pump_trade_event_prefixes'] if event['mint']==e['mint']]
                if len(same)>1 and all(same[-1].get(k)==posts[0]['decoded'][k] for k in RESERVES):
                    joins['intermediate_event_before_matching_final_same_mint_event']+=1
                else:joins['unexplained_event_post_reserve_difference']+=1
    for tx in transactions:
        if tx['execution_status']!='SUCCESS':continue
        events=tx['pump_trade_event_prefixes'];case={'signature':tx['signature'],'slot':tx['slot'],
            'available_at_ns':tx['metadata_available_at_ns'],'status':'UNRESOLVED','reason':None,
            'independent_finalized_reconstruction':False,'causal_quote_certified':False}
        cases.append(case)
        try:
            if len(events)!=1:raise Unsupported('NOT_SINGLE_EVENT_TRANSACTION')
            e=events[0];case['mint']=e['mint']
            if not e.get('signed_sol_flow_eligible'):raise Unsupported('NOT_DECODED_SOL_EVENT')
            curve=mappings[e['mint']]['curve'];case['curve']=curve
            trades=[]
            for ix in tx['instructions']:
                if ix['program_id']!=PUMP:continue
                try: trades.append(decode_instruction(ix,idl))
                except Unsupported as err:
                    if str(err)!='NOT_TRADE_VARIANT':raise
            if len(trades)!=1:raise Unsupported('NOT_SINGLE_TRADE_INSTRUCTION')
            ix=trades[0];case['variant']=ix['kind'];case['instruction']=ix
            roles=ix['roles'];mint=roles.get('mint',roles.get('base_mint'))
            if mint!=e['mint'] or roles.get('bonding_curve')!=curve:raise Unsupported('INSTRUCTION_CANONICAL_IDENTITY_MISMATCH')
            if roles.get('global')!=GLOBAL or roles.get('fee_config')!=FEE:raise Unsupported('INSTRUCTION_CONFIG_IDENTITY_MISMATCH')
            if roles.get('fee_program',FEE_PROGRAM)!=FEE_PROGRAM:raise Unsupported('FEE_PROGRAM_MISMATCH')
            history=histories.get(curve,[])
            posts=[r for r in history if r['account'].get('transaction_signature')==tx['signature'] and r['account']['slot']==tx['slot']]
            if len(posts)!=1 or not posts[0]['decoded']:raise Unsupported('NO_UNIQUE_DECODED_RAW_POST')
            post=posts[0];index=tx.get('transaction_index')
            if type(index) is not int:raise Unsupported('UNKNOWN_TRANSACTION_ORDER')
            predecessors=[]
            for r in history:
                prev=by_sig.get(r['account'].get('transaction_signature'))
                if prev and type(prev.get('transaction_index')) is int and (prev['slot'],prev['transaction_index'])<(tx['slot'],index):
                    predecessors.append((prev['slot'],prev['transaction_index'],r,prev))
            if not predecessors:raise Unsupported('NO_INDEPENDENT_PRIOR_RAW_STATE')
            _,_,pre,prev=max(predecessors,key=lambda x:x[:2])
            if sum(r['account'].get('transaction_signature')==prev['signature'] and r['account']['slot']==prev['slot'] for r in history)!=1:
                raise Unsupported('AMBIGUOUS_PRIOR_RAW_WRITE')
            if not pre['decoded']:raise Unsupported('INVALID_PRIOR_RAW_STATE')
            state=pre['decoded'];case['pre_source']={'signature':prev['signature'],'slot':prev['slot'],
                'source_line':pre['source_line'],'available_at_ns':pre['available_at_ns'],'state':state}
            case['post_source_line']=post['source_line']
            # Auxiliary state selection is causal at target receipt. No later RPC
            # recovery can make this true; continuity remains a separate gate.
            at=tx['metadata_available_at_ns']
            aux={name:available(histories.get(key,[]),at) for name,key in [('fee',FEE),('global',GLOBAL),('mint',mint),('clock',CLOCK)]}
            for name,r in aux.items():
                if not r or r['decoded'] is None:raise Unsupported('NO_PRIOR_VALID_'+name.upper())
                if r['account']['slot']>tx['slot']:raise Unsupported('CONTEXT_SLOT_AHEAD_OF_TRANSACTION')
            case['context_sources']={k:{'source_line':v['source_line'],'origin':v['origin'],
                'available_at_ns':v['available_at_ns'],'slot':v['account']['slot']} for k,v in aux.items()}
            pf,cf=fee_rates(state,aux['mint']['decoded'],aux['fee']['decoded'])
            case['computed_fee_bps']={'protocol':pf,'creator':cf}
            predicted=quote(state,ix['kind'],ix['amount'],ix['limit'],pf,cf)
            actual={'curve_amount':e['sol_amount_lamports'],'token_amount':e['token_amount_raw'],
                'protocol_fee':e.get('protocol_fee_amount'),'creator_fee':e.get('creator_fee_amount'),
                'post':{k:post['decoded'][k] for k in RESERVES}}
            case['predicted']=predicted;case['actual']=actual
            differences=[k for k in actual if predicted[k]!=actual[k]]
            if e.get('protocol_fee_bps')!=pf or e.get('creator_fee_bps')!=cf:differences.append('fee_bps')
            if any(e[k]!=post['decoded'][k] for k in RESERVES):differences.append('event_vs_raw_post')
            case['differences']=differences;case['status']='ARITHMETIC_MATCH' if not differences else 'ARITHMETIC_MISMATCH'
            # Both signatures must be in the SAME preserved finalized full block;
            # every intervening successful transaction is checked for curve touches.
            # This is reconstruction learned later, NOT original receipt knowledge.
            target_ref=refs.get(tx['signature']);prior_ref=refs.get(prev['signature'])
            proof=[]
            if not target_ref or not prior_ref or target_ref[0]!=prior_ref[0]:proof.append('NO_SAME_BLOCK_FINALIZED_BRACKET')
            else:
                slot,j,final,known=target_ref;_,i,prior_final,_=prior_ref
                if i>=j:proof.append('FINALIZED_ORDER_CONFLICT')
                for x,y in ((tx,final),(prev,prior_final)):
                    proof.extend(metadata_differences(x,y))
                if intervening_uncertainty(block_txs[slot][i+1:j],curve):
                    proof.append('INTERVENING_SUCCESSFUL_OR_UNKNOWN_CURVE_TOUCH')
                # Includes curve balance rather than user's net delta (which can
                # include rent, tips and unrelated transfers). Event is not sole oracle.
                balances=[d for d in final['lamport_balance_deltas'] if d.get('account')==curve]
                case['curve_balance_deltas']=balances
                sign=-1 if ix['kind'].startswith('sell') else 1
                if len(balances)!=1 or balances[0]['net_delta_lamports']!=sign*predicted['curve_amount']:
                    proof.append('CURVE_LAMPORT_DELTA_MISMATCH_OR_NONISOLATED')
                base_account=roles.get('associated_bonding_curve',roles.get('associated_base_bonding_curve'))
                base_deltas=[d for d in final['token_balance_deltas'] if d['mint']==mint and
                    final['resolved_account_keys'][d['account_index']]==base_account]
                if len(base_deltas)!=1 or base_deltas[0]['net_delta_raw']!=-sign*predicted['token_amount']:
                    proof.append('CURVE_TOKEN_DELTA_MISMATCH_OR_NONISOLATED')
                # Creator/protocol cash credits are checked independently of logs.
                global_state=aux['global']['decoded']
                fee_destinations=set([global_state['fee_recipient'],*global_state['fee_recipients'],*global_state['buyback_fee_recipients']])
                fee_deltas=[d for d in final['lamport_balance_deltas'] if d['account'] in fee_destinations]
                creator_deltas=[d for d in final['lamport_balance_deltas'] if d['account']==roles.get('creator_vault')]
                case['protocol_cash_credit']=sum(d['net_delta_lamports'] for d in fee_deltas)
                case['creator_cash_credit']=sum(d['net_delta_lamports'] for d in creator_deltas)
                if case['protocol_cash_credit']!=predicted['protocol_fee']:proof.append('PROTOCOL_CASH_DELTA_MISMATCH_OR_NONISOLATED')
                if case['creator_cash_credit']!=predicted['creator_fee']:proof.append('CREATOR_CASH_DELTA_MISMATCH_OR_NONISOLATED')
                prior_events=prior_final['pump_trade_event_prefixes']
                if len(prior_events)!=1 or any(prior_events[0].get(k)!=state[k] for k in RESERVES):proof.append('PRIOR_FINALIZED_EVENT_STATE_MISMATCH')
                case['finalized_reference_available_at_ns']=known
            case['independent_reconstruction_blockers']=sorted(set(proof))
            case['independent_finalized_reconstruction']=not proof and not differences
            if case['independent_finalized_reconstruction']:
                fixtures.append({'signature':tx['signature'],'slot':tx['slot'],'variant':ix['kind'],
                    'instruction':ix,'raw_pre_account':pre['account'],'raw_post_account':post['account'],
                    'raw_context':{k:v['account'] for k,v in aux.items()},'actual_event':e,
                    'finalized_lamport_deltas':final['lamport_balance_deltas'],
                    'finalized_token_deltas':final['token_balance_deltas'],
                    'native_pre_source_line':pre['source_line'],'native_post_source_line':post['source_line'],
                    'reference_available_at_ns':known,'reference_slot':slot,
                    'prior_finalized_signature':prev['signature'],'no_intervening_successful_curve_touch':True})
            case['pre_state_known_before_target_receipt']=pre['available_at_ns']<=at
            # Size scenarios are mathematical stress fixtures, NOT executed trades.
            if not differences:
                for size in SIZES:
                    fixture={'signature':tx['signature'],'size_lamports':size,'status':'CONDITIONAL_QUOTE','onchain_at_this_size':False}
                    try:fixture['quote']=quote(state,'buy_exact_sol_in',size,0,pf,cf)
                    except Unsupported as err:fixture.update(status='NON_EXECUTABLE_UNDER_GIVEN_STATE',reason=str(err))
                    synthetic.append(fixture)
        except Unsupported as err:case['reason']=str(err)
    write_jsonl(output/'transaction-reconstructions.jsonl',cases)
    write_jsonl(output/'size-fixtures.jsonl',synthetic)
    write_jsonl(output/'onchain-fixtures.jsonl',fixtures)
    return {'successful_transaction_denominator':len(cases),'statuses':dict(Counter(c['status'] for c in cases)),
        'unresolved_reasons':dict(Counter(c['reason'] for c in cases if c['reason'])),
        'independently_reconstructed_finalized_matches':sum(c['independent_finalized_reconstruction'] for c in cases),
        'by_variant':{v:dict(Counter(c['status'] for c in cases if c.get('variant')==v)) for v in sorted({c['variant'] for c in cases if 'variant'in c})},
        'finalized_by_variant':dict(Counter(c['variant'] for c in cases if c['independent_finalized_reconstruction'])),
        'reconstruction_blockers':dict(Counter(r for c in cases for r in c.get('independent_reconstruction_blockers',[]))),
        'causal_quotes_certified':0,'size_fixture_count':len(synthetic),
        'size_fixtures_are_not_executed_orders':True,
        'event_account_join_counts':dict(joins),
        'independent_fixture_unique_mints':len({f['actual_event']['mint'] for f in fixtures}),
        'independent_fixture_curve_amount_range_lamports':([min(f['actual_event']['sol_amount_lamports'] for f in fixtures),max(f['actual_event']['sol_amount_lamports'] for f in fixtures)] if fixtures else None)}


def metadata_differences(native,final):
    def canonical(value):
        if isinstance(value,list):return [canonical(x) for x in value]
        if not isinstance(value,dict):return value
        result={k:canonical(v) for k,v in value.items() if k not in ('uiAmount','uiAmountString')}
        # RPC supplies stackHeight=1 for top-level instructions; protobuf does
        # not. Never erase inner CPI depth or parent/index identity differences.
        if 'outer_index' in result and result.get('inner_index') is None and result.get('stack_height') in (None,1):result['stack_height']=1
        return result
    return ['FINALIZED_METADATA_DIFFERENCE:'+key for key in ('execution_status','resolved_account_keys','instructions',
        'pump_trade_event_prefixes','lamport_balance_deltas','token_balance_deltas','fee_lamports') if canonical(native[key])!=canonical(final[key])]


def intervening_uncertainty(rows,curve):
    return any(mid['execution_status']!='FAILED' and (curve in mid['resolved_account_keys'] or
        not mid['resolved_account_keys'] or any(g in mid['gaps'] for g in ('alt_resolution_count_mismatch','unsupported_parsed_accounts')))
        for mid in rows)


def run(source,quality,canonical,prior_context,output,web3,extra_reference=None):
    output.mkdir(parents=True,exist_ok=False)
    contract=json.loads(CONTRACT.read_text())
    for reference in contract['sources']:
        if 'files' in reference:
            for name,expected in reference['files'].items():
                if sha(ROOT/name)!=expected:raise ValueError('pinned IDL changed')
        elif sha(ROOT/reference['file'])!=reference['sha256']:raise ValueError('pinned token reference changed')
    inputs={str(p):sha(p) for p in (source/'capture.jsonl',source/'bootstrap.jsonl',quality/'transactions.jsonl',
        quality/'finalized-reference.jsonl',canonical/'boundaries.jsonl',prior_context/'boundary-context.jsonl',prior_context/'attempts.jsonl')}
    # Verify the original provenance reports, not merely hashes computed today.
    old_report=json.loads((prior_context/'report.json').read_text())
    for path,key in ((canonical/'boundaries.jsonl','boundary_sha256'),
                     (prior_context/'boundary-context.jsonl','boundary-context_sha256'),
                     (prior_context/'attempts.jsonl','attempts_sha256')):
        if sha(path)!=old_report[key]:raise ValueError('original sealed artifact changed')
    quality_report=json.loads((quality/'report.json').read_text())
    if sha(source/'capture.jsonl')!=quality_report['input']['sha256']:raise ValueError('sealed native capture changed')
    inputs[str(prior_context/'report.json')]=sha(prior_context/'report.json')
    inputs[str(quality/'report.json')]=sha(quality/'report.json')
    if extra_reference:inputs[str(extra_reference)]=sha(extra_reference)
    histories,capture=decode_capture(source)
    identities=json.loads(subprocess.run(['node',str(ROOT/'derive_quote_identity_v1.cjs'),str(web3),str(PUMP_IDL)],capture_output=True,text=True,check=True).stdout)
    if identities['fee']['address']!=FEE or identities['global']['address']!=GLOBAL:raise ValueError('config canonical identity mismatch')
    transactions=list(jsonl(quality/'transactions.jsonl'))
    view_digest=hashlib.sha256()
    for tx in transactions:view_digest.update(digest(tx).encode())
    if len(transactions)!=capture['raw_to_normalized_transactions'] or view_digest.hexdigest()!=capture['normalized_transaction_digest']:
        raise ValueError('raw-to-normalized transaction lineage mismatch')
    mints={e['mint'] for t in transactions for e in t['pump_trade_event_prefixes']}
    mappings=derive_curves(mints,web3)['mappings']
    original=original_coverage(canonical,prior_context,histories,capture,output)
    references=[quality/'finalized-reference.jsonl']+([extra_reference] if extra_reference else [])
    arithmetic=reconstruction(histories,transactions,references,mappings,output)
    canary=canary_boundaries(histories,transactions,mappings,capture,output)
    report={'schema':'CAUSAL_QUOTE_ADAPTER_A3H3_V1','inputs':inputs,'capture':capture,'canonical_config_identity':identities,
        'original_coverage':original,'quote_reconstruction':arithmetic,'separate_canary_coverage':canary,'implementation_sha256':sha(Path(__file__)),
        'pinned_idls':{p.name:sha(p) for p in (PUMP_IDL,FEE_IDL)},
        'frozen_signal_contract_sha256':sha(ROOT/'burst-persistence-contract-v1.json'),
        'adapter_contract_sha256':sha(CONTRACT),
        'artifacts':{p.name:sha(p) for p in sorted(output.glob('*.jsonl'))},
        'limitations':['Arithmetic matches do not prove continuous state/fee/mint availability or execution fills.',
            'Native subscription activation and global full-write/fork reconciliation remain uncertified.',
            'Finalized reference is later knowledge; it validates a historical reconstruction, not an original decision input.',
            'Sizes are nonlinear quote fixtures, not linearly scaled observed PnL. No own-position replay or net economics here.',
            'Unsupported layouts, instruction variants, extensions, partial fills and modes stay unresolved.']}
    # Guard sealed inputs against mutation while replaying.
    if any(sha(Path(p))!=h for p,h in inputs.items()):raise ValueError('input changed during replay')
    (output/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n',encoding='utf8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('source','quality','canonical','prior-context','output','web3'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--extra-reference',type=Path)
    a=p.parse_args();print(json.dumps(run(a.source,a.quality,a.canonical,a.prior_context,a.output,a.web3,a.extra_reference),indent=2))
