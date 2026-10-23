#!/usr/bin/env python3
"""Metadata/receipt-only certification of the frozen kEfi development forty."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
EXPECTED_SPLIT="1c2ace37897ae076ab76d192a4641fdd0a25360c89abb48c52db4996e6e45526"
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--split',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 if sha(a.split)!=EXPECTED_SPLIT or a.out.exists():raise SystemExit('FAIL_CLOSED: unexpected split or existing certification')
 s=json.loads(a.split.read_text()); d=s.get('development_snapshots',[])
 if len(d)!=40 or s.get('holdout_opened') is not False:raise SystemExit('FAIL_CLOSED: invalid 40/20 split')
 roots={x['cohort_path'] for x in d}; instances={x['source_instance_id'] for x in d}; epochs={x['continuity_epoch'] for x in d}
 if len(roots)!=1 or len(instances)!=1 or epochs!={1}:raise SystemExit('FAIL_CLOSED: development crosses cohort/source epoch')
 for x in d:
  if '/live/validation-choice-state-v1-1791915517686940846-pid939965' not in x['cohort_path']:raise SystemExit('FAIL_CLOSED: not Cohort A')
  if sha(Path(x['snapshot_path']))!=x['snapshot_sha256'] or sha(Path(x['receipt_path']))!=x['receipt_sha256']:raise SystemExit('FAIL_CLOSED: artifact hash mismatch')
  r=json.loads(Path(x['receipt_path']).read_text())
  if not all(r.get(k) is True for k in ('warmup_complete','pre_action_only','teacher_event_excluded_from_state','authoritative_pool_identity','chosen_pool_previously_eligible')):raise SystemExit('FAIL_CLOSED: receipt contract mismatch')
 result={'schema':'KEFI_DEVELOPMENT_40_VALIDITY_V1','status':'DEVELOPMENT_40_VALID_AND_FROZEN','split_sha256':EXPECTED_SPLIT,'development_decisions':40,'cohort':'A','source_instance_id':next(iter(instances)),'continuity_epoch':1,'single_epoch':True,'warmup_preaction_receipts_valid':True,'snapshot_and_receipt_hashes_valid':True,'quarantined_rows_included':False,'wallet_newness_accounting':'observer-local from original Cohort A start; no observer restart occurred within development','outcomes_or_pnl_accessed':False}
 tmp=a.out.with_suffix('.tmp');tmp.write_text(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n');os.replace(tmp,a.out);print(sha(a.out))
if __name__=='__main__':main()
