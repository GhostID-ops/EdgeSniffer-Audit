#!/usr/bin/env python3
"""Produce a read-only, machine-verifiable precise live dependency snapshot.

This has no authority to release or unpin data.  It is designed to run often
and capture the moving set that a later atomic pin transition must cover.
"""
from __future__ import annotations
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path

def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def canonical(value): return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()
def write_bytes(path, raw):
 path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw); return hashlib.sha256(raw).hexdigest()
def json_file(p): return json.loads(p.read_text())
def main():
 ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--source-root',required=True); ap.add_argument('--work-root',required=True); ap.add_argument('--output',required=True); ap.add_argument('--history-dir'); args=ap.parse_args()
 src=Path(args.source_root); work=Path(args.work_root); parts=src/'source-spool/partitions'
 manifests=[]; open_paths=[]
 for manifest in sorted(parts.rglob('manifest.json')):
  try:
   row=json_file(manifest); key=str(manifest.parent.relative_to(parts))
   if row.get('temporalHourClosed') is True: manifests.append(Path(key).as_posix())
  except Exception: pass
 for pattern in ('evidence.frames.partial','evidence.frames.zst.tmp','*.partial'):
  for item in sorted(parts.rglob(pattern)): open_paths.append(str(item.parent))
 states=[]; processed=set()
 for p in [work/'out/kefi-market-controls-v1/state.json',work/'out/kefi-market-controls-catchup-v1/state.json']:
  row=json_file(p); states.append({'path':str(p),'sha256':digest(p),'processed_count':len(row.get('processed',[]))}); processed.update(row.get('processed',[]))
 pending=[]
 for a,b in zip(manifests,manifests[1:]):
  pair=f'{a}__{b}'
  if pair not in processed: pending.append({'pair':pair,'partitions':[a,b]})
 immediate=[] if not pending else pending[0]['partitions']
 snapshot={'schema':'evidence-lake-live-dependency-snapshot-v1','observed_utc':now(),
  'open_or_rotating_partition_paths':sorted(set(open_paths)),
  'next_control_raw_pair':pending[0] if pending else None,
  'all_closed_manifest_paths':len(manifests),
  'control_states':states,
  'precise_active_raw_paths':[str(parts/p) for p in immediate],
  'precise_active_metadata_paths':[str(src/'source-spool/state'),str(src/'kefi-wallet-pump-candidates.jsonl'),str(src/'finalizer.sqlite3'),str(src/'source-spool-results'),str(src/'canonical-proofs'),str(src/'promotion-results'),str(work/'out/kefi-prospective-shadow-v1'),str(work/'out/kefi-market-controls-v1'),str(work/'out/kefi-market-controls-catchup-v1')],
  'broad_root_pin_removal_authorized':False,
  'reason':'snapshot-only; requires independent coverage history and atomic catalog transition'}
 out=Path(args.output); raw=canonical(snapshot); sha=write_bytes(out,raw)
 history=None
 if args.history_dir:
  history=Path(args.history_dir)/f"{snapshot['observed_utc'].replace(':','').replace('-','')}-{sha[:16]}.json"
  write_bytes(history,raw)
 print(json.dumps({'status':'SNAPSHOT_WRITTEN','output':str(out),'history_output':str(history) if history else None,'sha256':sha,'next_pair':snapshot['next_control_raw_pair']},sort_keys=True))
if __name__=='__main__': main()
