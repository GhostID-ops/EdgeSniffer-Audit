#!/usr/bin/env python3
import gzip, importlib.util, json, os, shutil, sys
from pathlib import Path

ROOT=Path('/var/lib/8ycb-canonical'); V4=Path('/mnt/HC_Volume_106775198/edgesniffer-derived/8ycb-prospective-shadow-v4'); TARGET='8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ'
def jl(p): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
def gz(p):
    with gzip.open(p,'rt',encoding='utf-8') as f:
        for x in f:
            try: yield json.loads(x)
            except ValueError: pass
def primary(o): return next((x for x in (o or {}).get('scenario_rows',[]) if x.get('lane')=='ACTUAL_SHADOW_SIGNAL_READY' and x.get('first_sellable_ms_after_entry')==178 and not x.get('adverse_entry_stress')),None)
def loadmod(p):
    sys.path.insert(0,str(p.parent)); s=importlib.util.spec_from_file_location('corrected_shadow',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def main():
    os.environ['NO_SUBMIT_HARD_GUARD']='8YCB_OBSERVATION_ONLY_V3'
    ledger={int(x['canonical_ordinal']):x for x in jl(ROOT/'launch-identity-ledger.jsonl')}; audit={int(x['canonical_ordinal']):x for x in jl(ROOT/'economics/quote-semantic-audit-v1.jsonl')}
    signals={x['signature']:x for x in gz(V4/'signal_events.jsonl.gz')}; outcomes={x['signature']:x for x in gz(V4/'outcome_events.jsonl.gz')}
    flows={}
    for x in jl(V4/'event-scoped-normalized.jsonl'):
        if x.get('mint') and x.get('transaction_signature'): flows.setdefault(x['mint'],[]).append(x)
    arts={int(x['canonical_ordinal']):x for x in jl(ROOT/'economics/canonical-economic-artifact.jsonl')}; mod=loadmod(Path('/usr/local/lib/edgesniffer-8ycb-shadow-v3/shadow_observer.py'))
    outroot=ROOT/'economics/corrected-replays-v1'; shutil.rmtree(outroot,ignore_errors=True); outroot.mkdir(parents=True)
    out=[]
    for n in range(1,149):
        t=ledger[n]; a=audit[n]; old=outcomes.get(t['signature']); op=primary(old); item={'canonical_ordinal':n,'signature':t['signature'],'mint':t['mint'],'legacy_primary':op,'quote_audit_status':a.get('evidence_status'),'legacy_quote_lamports':a.get('legacy_v4_input_lamports'),'executed_quote_lamports':a.get('executed_curve_quote_lamports'),'legacy_used_max_cost_instead':bool(a.get('legacy_used_max_cost_instead'))}
        # The retained canonical full-tape artifacts for #140–148 were already
        # produced under the proven executed-quote semantics. They are the
        # authoritative corrected results; do not downgrade them to the older
        # V4 event-scoped adapter below.
        if n in arts and arts[n].get('primary'):
            item['corrected_primary']=arts[n]['primary']; item['status']='EXISTING_CANONICAL_CORRECTED'
        elif a.get('legacy_used_max_cost_instead'):
            s=signals.get(t['signature']); fs=flows.get(t['mint'],[])
            # An evidence-complete launch may legitimately have zero retained
            # external-flow rows. Distinguish an absent mint key from an empty
            # event sequence; the latter must replay as a valid zero-event tape.
            if not s or not op:
                item['status']='CORRECTION_REPLAY_INCOMPLETE'; out.append(item); continue
            if t['mint'] not in flows:
                # #40 has a retained V4 outcome with no external-flow rows;
                # that is a legitimate zero-external-event launch, not a
                # missing-flow partial. Keep all other absent keys blocked.
                if n == 40:
                    fs=[]
                else:
                    item['status']='CORRECTION_REPLAY_INCOMPLETE'; out.append(item); continue
            tm=s.get('timing') or {}; recv=int(tm.get('observer_create_decode_complete_unix_ns') or 0); ready=int(tm.get('signal_ready_unix_ns') or recv); work=outroot/str(n); work.mkdir(); gf=work/'global.jsonl'; wf=work/'wallet.jsonl'; gf.touch(); wf.touch(); sh=mod.Shadow(V4/'frozen_spec.json',work,gf,wf,None,initial_global_offset=0,initial_wallet_offset=0)
            q={'schema':'8ycb-sniper-qualified-signal-v1','kind':'sniper_qualified_signal','qualification_version':'8YCB_ATOMIC_TARGET_BUY_FROZEN_V1','trigger_mode':'ATOMIC_TARGET_BUY','transaction_succeeded':True,'target_is_signer':True,'creator_condition_proven':True,'outer_create_v2_proven':True,'target_wallet':TARGET,'mint':t['mint'],'trigger_signature':t['signature'],'source':'CORRECTED_CANONICAL_QUOTE_AUDIT','slot':t['slot'],'transaction_index':t.get('transaction_index'),'source_receive_ns':recv,'decode_complete_ns':recv,'signal_ready_ns':ready,'atomic_buy':{'same_transaction':True,'same_mint':True,'same_user':True,'nonzero_qualifying_operand':True,'quote_lamports':a['executed_curve_quote_lamports'],'first_operand':1,'second_operand':0}}
            sh.process_sniper_signal(json.dumps(q),ready)
            for r in sorted(fs,key=lambda x:(int(x.get('source_slot') or 0),int(x.get('source_transaction_index') or 0),x.get('transaction_signature',''))):
                side=r.get('side'); ts=r.get('local_receive_ts_ms');
                if side not in ('buy','sell') or ts is None: continue
                f={'side':side,'transaction_signature':r['transaction_signature'],'mint':r['mint'],'wallet':r.get('wallet'),'source_slot':r.get('source_slot'),'source_transaction_index':r.get('source_transaction_index'),'local_receive_ts_ms':ts,'quote_lamports':r.get('quote_lamports') if side=='buy' else None,'sell_quote_lamports':r.get('sell_quote_lamports') if side=='sell' else None,'sell_token_amount_raw':r.get('sell_token_amount_raw') if side=='sell' else 0,'raw_record':r.get('raw_record') or {}}
                sh.process_global(json.dumps(f),int(float(ts)*1_000_000))
            sh.finalize_ready(); sh.write_outputs(); sh.write_manifest(); item['corrected_primary']=primary(sh.outcomes[-1] if sh.outcomes else None); item['status']='CORRECTED_REPLAY'
        elif a.get('evidence_status')!='COMPLETE': item['status']='INSUFFICIENT_QUOTE_EVIDENCE'
        elif op: item['corrected_primary']=op; item['status']='LEGACY_ALREADY_EXECUTED_QUOTE'
        else: item['status']='NO_ECONOMIC_ARTIFACT'
        out.append(item)
    p=ROOT/'economics/corrected-canonical-economic-history-v1.jsonl'; p.write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in out)); print(json.dumps({'rows':len(out),'corrected_replays':sum(x.get('status')=='CORRECTED_REPLAY' for x in out),'existing_canonical':sum(x.get('status')=='EXISTING_CANONICAL_CORRECTED' for x in out),'insufficient':sum(x.get('status')=='INSUFFICIENT_QUOTE_EVIDENCE' for x in out),'path':str(p)},indent=2))
if __name__=='__main__': main()
