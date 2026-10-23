#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, shutil
from pathlib import Path

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--census',type=Path,required=True); p.add_argument('--events',type=Path,required=True); p.add_argument('--gate',type=Path,required=True); p.add_argument('--out',type=Path,required=True); p.add_argument('--events-out',type=Path,required=True)
    a=p.parse_args(); gate=json.loads(a.gate.read_text())
    if not gate.get('eligible_for_canonical_census') or gate.get('events_failed'):
        raise SystemExit('BLOCKED_CANONICAL_PROMOTION')
    census=json.loads(a.census.read_text()); census['schema']='WALLET_ONLY_CANONICAL_ROUTE_CENSUS_V1'; census['canonical']=True; census['validation_gate_sha256']=sha(a.gate); census['canonical_input_sha256']=sha(a.events); census['policy_frozen']=False; census['trading_enabled']=False
    a.out.parent.mkdir(parents=True,exist_ok=True); a.events_out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(census,indent=2,sort_keys=True)+'\n')
    shutil.copyfile(a.events,a.events_out)
    print(json.dumps({'status':'CANONICAL_CENSUS_PROMOTED','events':gate['events_passed'],'sol_economics_events':gate['sol_economics_eligible_events']}))
if __name__=='__main__': main()
