#!/usr/bin/env python3
"""Freeze a rich pre-entry discovery model before prospective shadowing."""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--feasibility',type=Path,required=True); ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ns=ap.parse_args()
    r=json.loads(ns.feasibility.read_text()); receipt=json.loads(ns.receipt.read_text())
    if r.get('status')!='EXPLORATORY_DESCRIPTIVE_NOT_VALIDATED' or receipt.get('result_sha256')!=sha(ns.feasibility): raise SystemExit('FAIL_CLOSED: feasibility receipt mismatch')
    artifact={'schema':'KEFI_RICH_PREENTRY_FINGERPRINT_V2','status':'FROZEN_FOR_PROSPECTIVE_OUTCOME_BLIND_SHADOW','frozen_at_utc':datetime.now(timezone.utc).isoformat(),'strict_preentry':True,'features':r['features'],'coefficients':r['coefficients'],'intercept':r['intercept'],'standardization_means':r['standardization_means'],'standardization_scales':r['standardization_scales'],'threshold':r['training_threshold'],'threshold_selected_from':'development training rows only','lead_windows_seconds':[5,10,20],'warmup_seconds':120,'minimum_finalized_teacher_labels':150,'minimum_fresh_complete_scored_market_events':5000,'source_feasibility_sha256':sha(ns.feasibility),'source_receipt_sha256':sha(ns.receipt),'outcomes_accessed':False,'pnl_accessed':False,'execution_enabled':False,'prohibitions':['no refitting','no threshold tuning','no outcome/PnL access','no trade submission'],'interpretation':'Frozen discovery model for fresh prospective lead-time shadow only; it cannot establish profitability or authorize execution.'}
    ns.output.parent.mkdir(parents=True,exist_ok=True); ns.output.write_text(json.dumps(artifact,indent=2,sort_keys=True)+'\n'); print(json.dumps({'output':str(ns.output),'sha256':sha(ns.output)},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
