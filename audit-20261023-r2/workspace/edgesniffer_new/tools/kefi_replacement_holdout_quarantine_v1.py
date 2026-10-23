#!/usr/bin/env python3
"""Write immutable audit-only quarantine receipts; never removes evidence."""
from __future__ import annotations
import argparse,hashlib,json,os
from pathlib import Path
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--reason',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if not a.root.is_dir():raise SystemExit('FAIL_CLOSED: root missing')
 files=[{'path':str(x),'sha256':sha(x),'bytes':x.stat().st_size} for x in sorted(a.root.rglob('*')) if x.is_file()]
 v={'schema':'KEFI_REPLACEMENT_HOLDOUT_QUARANTINE_V1','root':str(a.root),'reason':a.reason,'file_count':len(files),'files':files,'eligibility':'PERMANENTLY_AUDIT_ONLY_NEVER_SCIENTIFIC_HOLDOUT','outcomes_or_pnl_accessed':False}
 a.output.parent.mkdir(parents=True,exist_ok=True);fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)
 with os.fdopen(fd,'wb') as f:f.write(json.dumps(v,sort_keys=True,separators=(',',':')).encode()+b'\n');f.flush();os.fsync(f.fileno())
 print(json.dumps({'receipt':str(a.output),'sha256':sha(a.output)},sort_keys=True))
if __name__=='__main__':main()
