#!/usr/bin/env python3
"""Outcome-blind deterministic reconciliation of observer decisions and wallet actions."""
from __future__ import annotations
import argparse, hashlib, json, os
from collections import Counter, defaultdict
from pathlib import Path

def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def rows(p): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def main():
 p=argparse.ArgumentParser();p.add_argument('--snapshots',type=Path,required=True);p.add_argument('--wallet-actions',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 snaps=[]
 for x in sorted(a.snapshots.glob('*.json')): snaps.append((x,json.loads(x.read_text())))
 actions=rows(a.wallet_actions); by_sig=defaultdict(list)
 for x in actions: by_sig[str(x.get('signature'))].append(x)
 output=[]; reasons=Counter()
 for path,s in snaps:
  sig=str(s['teacher_signature']); mint=str(s['chosen_mint']); direct=by_sig.get(sig,[])
  same=[x for x in direct if str(x.get('mint'))==mint]
  # Canonical wallet index calls this field `side`; tolerate the older
  # action/action_type spellings only for historical audit inputs.
  buys=[x for x in same if str(x.get('side', x.get('action', x.get('action_type','')))).upper().endswith('BUY')]
  if len(buys)==1: reason='EXACT_DIRECT_BUY_MATCH'; cls='DIRECT_BUY'
  elif len(buys)>1: reason='DIRECT_ACTION_MULTI_BUY_AMBIGUITY'; cls='AMBIGUOUS'
  elif same: reason='DIRECT_ACTION_PRESENT_BUT_NOT_BUY'; cls='DIRECT_NON_BUY'
  elif direct: reason='WALLET_TX_PRESENT_NO_MATCHING_DIRECT_MINT_ACTION'; cls='DIRECT_UNDERCOUNT_OR_EVENT_SEMANTIC_MISMATCH'
  else: reason='WALLET_TRANSACTION_NOT_IN_DIRECT_INDEX'; cls='DIRECT_INDEX_COVERAGE_MISSING'
  reasons[reason]+=1
  output.append({'snapshot_path':str(path),'wallet_transaction_signature':sig,'slot':s['teacher_slot'],'event_index':s['teacher_event_index'],'mint':mint,'direct_wallet_action_classification':cls,'observer_event_classification':s['selection_kind'],'observer_events_in_same_wallet_transaction':sum(1 for _,q in snaps if q['teacher_signature']==sig),'direct_actions_in_transaction':len(direct),'matching_direct_buy_actions':len(buys),'disagreement_reason':reason})
 identities=[(x['wallet_transaction_signature'],x['mint']) for x in output]
 if len(identities)!=len(set(identities)): raise SystemExit('FAIL_CLOSED: duplicate scientific teacher identity in audited run')
 value={'schema':'KEFI_REPLACEMENT_HOLDOUT_RECONCILIATION_V1','snapshot_input_sha256':sha(a.snapshots.parent.parent/'choice-snapshots.jsonl.zst') if (a.snapshots.parent.parent/'choice-snapshots.jsonl.zst').exists() else None,'wallet_actions_sha256':sha(a.wallet_actions),'candidate_event_count':len(output),'unique_wallet_selection_identities':len(set(identities)),'multiple_observer_events_same_wallet_transaction':sum(1 for x in output if x['observer_events_in_same_wallet_transaction']>1),'disagreement_reasons':dict(sorted(reasons.items())),'mapping':output,'outcomes_or_pnl_accessed':False,'conclusion':'Each retained snapshot has one unique signature/mint identity; direct-index disagreement is explicitly classified, not assumed.'}
 a.output.parent.mkdir(parents=True,exist_ok=True);fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)
 with os.fdopen(fd,'wb') as f:f.write(json.dumps(value,sort_keys=True,separators=(',',':')).encode()+b'\n');f.flush();os.fsync(f.fileno())
 print(json.dumps({'output':str(a.output),'sha256':sha(a.output),'events':len(output),'reasons':dict(reasons)},sort_keys=True))
if __name__=='__main__':main()
