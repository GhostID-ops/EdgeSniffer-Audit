#!/usr/bin/env python3
"""Bounded, outcome-blind market-wide kEFi control cohort builder."""
from __future__ import annotations
import hashlib, json, os, shutil, subprocess, time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/root/kefi-yellowstone-prospective-v1')
OUT=Path(os.environ.get('KEFI_CONTROL_OUT','/root/kefi-work/out/kefi-market-controls-v1'))
BIN=Path('/root/kefi-work/bin/kefi_prospective_discovery_v5')
MODEL=Path('/root/kefi-work/out/independent-v1/fingerprint-discovery-v1.json')
MAX_CONTROLS=500
MIN_SOURCE_PARTITION_UTC=os.environ.get('KEFI_MIN_SOURCE_PARTITION_UTC')

def loadl(p): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def partition_epoch(value):
 return datetime.strptime(value,'%Y/%m/%d/%H').replace(tzinfo=timezone.utc).timestamp()
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def closed():
 out=[]
 for p in (ROOT/'source-spool/partitions').rglob('manifest.json'):
  try:
   x=json.loads(p.read_text())
   if x.get('temporalHourClosed') is True: out.append((p.parent.relative_to(ROOT/'source-spool/partitions').as_posix(),x))
  except Exception: pass
 return sorted(out)
def transform(r):
 base=int(r['base_amount']); q=int(r['pool_quote_raw']); dq=int(r['pool_quote_delta'])
 return {'signature':r['candidate_signature'],'event_index':int(r['event_index']),'side':'BUY','selected_mint':r['mint'],'pool':r['pool'],'source_partition':r['source_partition'],'received_at_unix_ns':int(r['received_at_unix_ns']),'base_amount':base,'pre_pool_base':int(r['pool_base_raw'])+base,'pre_pool_quote':q-dq,'pool_quote_delta':dq,'user_quote_amount':int(r['user_quote_amount']),'amm_fee_bps':int(r['lp_fee_bps'])+int(r['protocol_fee_bps'])+int(r['coin_creator_bps'])}
def run_pair(a,b):
 key=f'{a[0].replace("/","_")}__{b[0].replace("/","_")}'; work=OUT/'tmp'; work.mkdir(parents=True,exist_ok=True)
 journal=ROOT/'kefi-wallet-pump-candidates.jsonl'; excluded={x['signature'] for x in loadl(journal) if x.get('signature')}
 empty=work/'empty.jsonl'; empty.write_text('')
 decoded=work/f'{key}.decoded.jsonl'; ds=work/f'{key}.decoded-summary.json'
 parts=a[0].split('/')
 cached=Path('/root/kefi-work/out/prospective-v1')/('decoded-'+''.join(parts[:3])+'-'+parts[3]+'.jsonl')
 if cached.exists() and cached.stat().st_size > 0:
  decoded=cached
 else:
  subprocess.run([str(BIN),'--candidates',str(ROOT),str(empty),str(decoded),str(ds),a[0]],check=True)
 picked={}
 for r in loadl(decoded):
  sig=r.get('candidate_signature'); ek=(sig,int(r.get('event_index',0)))
  if not sig or sig in excluded: continue
  if r.get('source_partition')!=a[0]: continue
  if ek in picked: picked[ek]=None
  elif all(r.get(k) is not None for k in ('base_amount','pool_base_raw','pool_quote_raw','pool_quote_delta','user_quote_amount')) and int(r['base_amount'])>0 and int(r['pool_base_raw'])+int(r['base_amount'])>0 and int(r['pool_quote_raw'])-int(r['pool_quote_delta'])>0: picked[ek]=r
 valid=[r for r in picked.values() if r]
 valid=sorted(valid,key=lambda r:hashlib.sha256((r['candidate_signature']+'|'+str(r['event_index'])).encode()).hexdigest())[:MAX_CONTROLS]
 fresh=OUT/f'{key}.candidates.jsonl'; targets=work/f'{key}.targets.jsonl'
 with fresh.open('w') as f, targets.open('w') as t:
  for r in valid:
   c=transform(r); f.write(json.dumps(c,sort_keys=True)+'\n')
   t.write(json.dumps({**r,'signature':c['signature'],'selected_mint':c['selected_mint'],'event_index':c['event_index'],'slot':int(r['slot']),'lp_fee_bps':int(r['lp_fee_bps']),'lp_fee':int(r['lp_fee']),'protocol_fee_bps':int(r['protocol_fee_bps']),'protocol_fee':int(r['protocol_fee']),'coin_creator_bps':int(r['coin_creator_bps']),'coin_creator_fee':int(r['coin_creator_fee']),'cashback_bps':int(r['cashback_bps']),'cashback':int(r['cashback']),'buyback_bps':int(r['buyback_bps']),'buyback_fee':int(r['buyback_fee']),'virtual_quote_reserves':int(r['virtual_quote_reserves']),'pre_pool_base':c['pre_pool_base'],'pre_pool_quote':c['pre_pool_quote']},sort_keys=True)+'\n')
 replay=work/f'{key}.replay.jsonl'; rs=work/f'{key}.replay-summary.json'
 subprocess.run([str(BIN),'--replay',str(ROOT),str(targets),str(replay),str(rs),a[0],b[0]],check=True)
 counts={}
 for r in loadl(replay): counts[r['target_signature']]=counts.get(r['target_signature'],0)+1
 windows=OUT/f'{key}.windows.jsonl'
 with windows.open('w') as f:
  for r in valid:
   if r['candidate_signature'] in counts:
    f.write(json.dumps({'window_id':r['candidate_signature'],'target_signature':r['candidate_signature'],'target_event_index':int(r['event_index']),'window_complete':True,'fail_closed':False,'replay_event_count':counts[r['candidate_signature']],'source_partitions':[a[0],b[0]]},sort_keys=True)+'\n')
 signals=OUT/f'{key}.signals.jsonl'; summary=OUT/f'{key}.summary.json'
 subprocess.run(['python3','/root/kefi-work/bin/kefi_prospective_paper_gate_v1.py','--model',str(MODEL),'--expected-model-sha256','e13ceacd57462addf03ee2c1c7ad40ca0db1aa9936c64ceaa44e2cfca06eead4','--fresh-candidates',str(fresh),'--replay-windows',str(windows),'--output-signals',str(signals),'--output-summary',str(summary)],check=True)
 receipt={'schema':'KEFI_MARKET_CONTROL_COHORT_V1','status':'COMPLETE','selection_rule':'exclude tracked-wallet signatures; deterministic SHA-256 order; cap 500 per closed partition','partitions':[a[0],b[0]],'controls_selected':len(valid),'complete_windows':len(loadl(windows)),'model_sha256':sha(MODEL),'outcome_blind':True,'outcomes_accessed':False,'pnl_accessed':False,'summary_sha256':sha(summary),'signals_sha256':sha(signals)}
 rp=OUT/f'{key}.receipt.json'; rp.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
 shutil.rmtree(work,ignore_errors=True)
 return key
def main():
 OUT.mkdir(parents=True,exist_ok=True); statep=OUT/'state.json'; state=json.loads(statep.read_text()) if statep.exists() else {'processed':[]}
 while True:
  parts=closed()
  if MIN_SOURCE_PARTITION_UTC:
   floor=partition_epoch(MIN_SOURCE_PARTITION_UTC)
   parts=[p for p in parts if partition_epoch(p[0]) >= floor]
  done=False
  for i in range(len(parts)-1):
   a,b=parts[i],parts[i+1]; key=f'{a[0]}__{b[0]}'
   if key in state['processed']: continue
   try:
    run_pair(a,b); state['processed'].append(key); statep.write_text(json.dumps(state,indent=2,sort_keys=True)+'\n'); done=True
   except Exception as e:
    (OUT/'last-error.json').write_text(json.dumps({'pair':key,'error':repr(e),'utc':time.time()},indent=2)+'\n'); time.sleep(300)
   break
  if not done: time.sleep(300)
if __name__=='__main__': main()
