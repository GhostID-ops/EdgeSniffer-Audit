#!/usr/bin/env python3
"""Guarded removal of a proven rebuildable artifact with an immutable receipt."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sys
from datetime import datetime, timezone
from pathlib import Path

def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def canon(v): return (json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def inventory(root):
 n=0; b=0
 for base,dirs,files in os.walk(root):
  for f in files:
   p=Path(base)/f
   try:n+=1;b+=p.stat().st_size
   except FileNotFoundError:pass
 return n,b
def handles(root):
 found=[]
 if not Path('/proc').is_dir(): return found
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit():continue
  try:
   for fd in (proc/'fd').iterdir():
    try:
     target=Path(os.readlink(fd));target.relative_to(root);found.append({'pid':proc.name,'target':str(target)})
    except (OSError,ValueError):pass
  except OSError:pass
 return found
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--path',required=True);ap.add_argument('--expected-path',required=True);ap.add_argument('--receipt-dir',required=True);ap.add_argument('--apply',action='store_true');args=ap.parse_args()
 p=Path(args.path).absolute(); expected=Path(args.expected_path).absolute()
 try:
  if p != expected:raise ValueError('path does not exactly match expected path')
  if not p.is_dir() or p.is_symlink():raise ValueError('target must be a real directory')
  # Toolchain/cache namespace guard: this tool may never remove evidence trees.
  if p.name not in {'rust','target','__pycache__','cache'} and not p.name.startswith('rust-'):
   raise ValueError('target is not an approved rebuildable-artifact namespace')
  n,b=inventory(p); open_handles=handles(p)
  if open_handles:raise ValueError('target has open file handles')
  payload={'schema':'rebuildable-reclaim-v1','classification':'REBUILDABLE_TOOLCHAIN_OR_CACHE','path':str(p),'files':n,'bytes':b,'open_handles':open_handles,'mode':'APPLY' if args.apply else 'PLAN','timestamp_utc':now()}
  if not args.apply: print(json.dumps(payload,sort_keys=True));return
  receipt_dir=Path(args.receipt_dir).absolute();receipt_dir.mkdir(parents=True,exist_ok=True)
  receipt=receipt_dir/(p.parent.name+'--'+p.name+'.reclaim.json')
  # Recheck immediately before deletion.
  if inventory(p)!=(n,b) or handles(p):raise ValueError('target changed before deletion')
  shutil.rmtree(p)
  if p.exists():raise ValueError('reclaim deletion did not complete')
  receipt.write_bytes(canon({**payload,'reclaimed_bytes':b,'completed_utc':now(),'local_path_absent':True}))
  print(json.dumps({**payload,'receipt':str(receipt),'receipt_sha256':sha(receipt),'reclaimed_bytes':b},sort_keys=True))
 except (OSError,ValueError) as e:
  print(json.dumps({'status':'FAIL_CLOSED','error':str(e)}),file=sys.stderr);raise SystemExit(2)
if __name__=='__main__':main()
