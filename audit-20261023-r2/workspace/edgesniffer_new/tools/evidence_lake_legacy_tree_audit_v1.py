#!/usr/bin/env python3
"""Read-only audit for an inactive historical lab/research tree."""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def canon(v): return (json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def snapshot(root):
 n=b=latest=0
 for base,dirs,files in os.walk(root):
  for f in files:
   p=Path(base)/f
   try:s=p.stat();n+=1;b+=s.st_size;latest=max(latest,s.st_mtime_ns)
   except FileNotFoundError:pass
 return n,b,latest
def fds(root):
 out=[]
 if not Path('/proc').is_dir():return out
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit():continue
  try:
   for fd in (proc/'fd').iterdir():
    try:
     t=Path(os.readlink(fd));t.relative_to(root);out.append({'pid':proc.name,'target':str(t)})
    except (OSError,ValueError):pass
  except OSError:pass
 return out
def unit_refs(root):
 needle=str(root).encode();found=[]
 for p in Path('/etc/systemd/system').rglob('*'):
  if not p.is_file():continue
  try:
   if needle in p.read_bytes():found.append(str(p))
  except OSError:pass
 return found
def proc_refs(root, ignored_pids):
 out=[];ignored=[];needle=str(root).encode()
 if not Path('/proc').is_dir():return out,ignored
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit():continue
  if proc.name == str(os.getpid()):continue
  try:
   cmd=(proc/'cmdline').read_bytes()
   if needle in cmd:
    item={'pid':proc.name,'cmdline':cmd.replace(b'\0',b' ').decode(errors='replace')}
    if proc.name in ignored_pids: ignored.append(item)
    else: out.append(item)
  except OSError:pass
 return out,ignored
def catalog_refs(root,catalog):
 if not catalog:return {'error':'catalog-not-provided','pins':[]}
 p=Path(catalog)
 if not p.is_file():return {'error':'catalog-missing','pins':[]}
 try:
  with sqlite3.connect(p) as db: rows=db.execute('SELECT experiment_id,path,pin_type,reason FROM pins').fetchall()
  pins=[]
  for experiment,path,pin_type,reason in rows:
   candidate=Path(path)
   try:
    candidate.resolve().relative_to(root.resolve()); overlap=True
   except ValueError:
    try: root.resolve().relative_to(candidate.resolve()); overlap=True
    except ValueError: overlap=False
   if overlap:pins.append({'experiment':experiment,'path':path,'pin_type':pin_type,'reason':reason})
  return {'error':None,'pins':pins}
 except sqlite3.Error as exc:return {'error':str(exc),'pins':[]}
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--path',required=True);ap.add_argument('--output',required=True);ap.add_argument('--catalog');ap.add_argument('--settle-seconds',type=float,default=1.0);ap.add_argument('--ignore-process-pid',action='append',default=[]);args=ap.parse_args()
 root=Path(args.path).absolute()
 if not root.is_dir() or root.is_symlink():raise SystemExit('FAIL_CLOSED: target is not a real directory')
 ignored_pids={str(int(pid)) for pid in args.ignore_process_pid}
 before=snapshot(root);handles=fds(root);units=unit_refs(root);processes,ignored_processes=proc_refs(root,ignored_pids);catalog=catalog_refs(root,args.catalog);time.sleep(args.settle_seconds);after=snapshot(root)
 changed=before!=after
 if handles or units or processes or catalog['pins']: outcome='DEPENDENCY_PRESENT'
 elif changed or catalog['error']: outcome='DEPENDENCY_UNKNOWN'
 else: outcome='DEPENDENCY_NONE_PROVEN'
 state='INACTIVE_ARCHIVE_CANDIDATE' if outcome=='DEPENDENCY_NONE_PROVEN' else 'PROTECTED_OR_UNKNOWN'
 report={'schema':'legacy-tree-dependency-audit-v3','timestamp_utc':now(),'path':str(root),'file_count':before[0],'bytes':before[1],'latest_mtime_ns':before[2],'changed_during_audit':changed,'open_handles':handles,'systemd_references':units,'process_references':processes,'ignored_processes':ignored_processes,'ignored_process_pids':sorted(ignored_pids),'catalog_references':catalog,'outcome':outcome,'classification':state,'archive_only_recommended':state=='INACTIVE_ARCHIVE_CANDIDATE','local_release_permitted':False}
 out=Path(args.output).absolute();out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(canon(report));print(json.dumps({'outcome':outcome,'classification':state,'audit':str(out),'audit_sha256':sha(out),'bytes':before[1]},sort_keys=True))
if __name__=='__main__':main()
