#!/usr/bin/env python3
"""Teacher-independent AUTONOMOUS_WHEN_V2 rank-snapshot capture lane."""
from __future__ import annotations
import argparse, base64, hashlib, json, math, os, pathlib, sqlite3, struct, subprocess, sys, time, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import twoCqDecisionSystemDecompositionV1 as decomposition
import twoCqChoiceSetCoverageRepairV1 as coverage

PUMP_SOURCE = "shyft-yellowstone-confirmed-full-meta-prospective-v1"
WALLET = decomposition.WALLET
MAX_AGE_NS = 120_000_000_000
TICK_NS = 1_000_000_000
MAGIC = b"PEL1FRM\0"

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def parse_utc_ns(value):
 date,clock=value[:-1].split('T',1); whole,fraction=(clock.split('.',1)+[''])[:2] if '.' in clock else (clock,'')
 return int(datetime.fromisoformat(date+'T'+whole+'+00:00').timestamp())*1_000_000_000+int((fraction+'000000000')[:9])
def write_json(p,x,immutable=False):
 p.parent.mkdir(parents=True,exist_ok=True)
 if immutable and p.exists(): raise RuntimeError('IMMUTABLE_OUTPUT_EXISTS')
 t=p.with_suffix('.tmp'); t.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n'); os.replace(t,p)
 if immutable: p.chmod(0o444)
def env(path,key):
 for x in pathlib.Path(path).read_text().splitlines():
  if x.startswith(key+'='): return x.split('=',1)[1].strip().strip('"\'')
 raise RuntimeError('MISSING_ENV:'+key)
def rpc(url,method,params):
 q=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(q,timeout=50) as r: x=json.load(r)
 if x.get('error'): raise RuntimeError('RPC:'+str(x['error']))
 return x.get('result')

def open_db(p):
 c=sqlite3.connect(p,timeout=60); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=FULL'); c.execute('PRAGMA busy_timeout=60000')
 c.executescript('''CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS events(sig TEXT PRIMARY KEY,mint TEXT,receipt_ns INTEGER,slot INTEGER,observed_ix INTEGER,ix INTEGER,is_buy INTEGER,user TEXT,creator TEXT,quote INTEGER,token INTEGER,pre_json TEXT,post_json TEXT,raw_sha TEXT,finality TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS events_receipt ON events(receipt_ns,finality);
 CREATE TABLE IF NOT EXISTS creates(mint TEXT PRIMARY KEY,sig TEXT,receipt_ns INTEGER,slot INTEGER,raw_sha TEXT,finality TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS teacher_labels(label_key TEXT PRIMARY KEY,sig TEXT,mint TEXT,receipt_ns INTEGER,slot INTEGER,observed_ix INTEGER,ix INTEGER,raw_sha TEXT,finality TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS teacher_labels_receipt ON teacher_labels(receipt_ns,finality);
 CREATE TABLE IF NOT EXISTS slot_queue(slot INTEGER PRIMARY KEY,receipt_ns INTEGER,finality TEXT NOT NULL DEFAULT 'PENDING');
 CREATE TABLE IF NOT EXISTS decoder_rejections(raw_sha TEXT PRIMARY KEY,sig TEXT,receipt_ns INTEGER,slot INTEGER,error TEXT NOT NULL,candidate_mints_json TEXT NOT NULL,created_utc TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS tainted_mints(mint TEXT PRIMARY KEY,raw_sha TEXT NOT NULL,receipt_ns INTEGER NOT NULL,reason TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS decisions(tick_ns INTEGER PRIMARY KEY,top_mint TEXT,top_score REAL,second_score REAL,margin REAL,universe_n INTEGER,persistence INTEGER,features_json TEXT,provenance_sha TEXT,finality TEXT NOT NULL,created_utc TEXT);
 CREATE TABLE IF NOT EXISTS decision_ticks(tick_ns INTEGER PRIMARY KEY,source_epoch INTEGER NOT NULL,tick_provenance_sha TEXT NOT NULL,candidate_set_sha TEXT NOT NULL,top_mint TEXT,top_score REAL,second_score REAL,margin REAL,universe_n INTEGER NOT NULL,finality TEXT NOT NULL,created_utc TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS decision_candidates(tick_ns INTEGER NOT NULL,mint TEXT NOT NULL,rank INTEGER NOT NULL,score REAL NOT NULL,margin_to_top REAL NOT NULL,persistence INTEGER NOT NULL,create_sig TEXT NOT NULL,create_receipt_ns INTEGER NOT NULL,last_event_sig TEXT NOT NULL,last_event_receipt_ns INTEGER NOT NULL,features_json TEXT NOT NULL,candidate_provenance_sha TEXT NOT NULL,finality TEXT NOT NULL,PRIMARY KEY(tick_ns,mint));
 CREATE INDEX IF NOT EXISTS decision_candidates_mint_tick ON decision_candidates(mint,tick_ns);
 '''); c.commit(); return c
def meta_get(db,k):
 r=db.execute('SELECT v FROM meta WHERE k=?',(k,)).fetchone()
 return r[0] if r else None
def meta_set(db,k,v):
 db.execute('INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,str(v)))

def source_continuity_changed(h,b):
 baseline=b.get('sourceContinuityBaseline')
 if not isinstance(baseline,dict): raise RuntimeError('SOURCE_CONTINUITY_BASELINE_MISSING')
 if int(h.get('currentEpoch',-1))!=int(baseline.get('sourceEpoch',-2)): return True
 return any(int(h.get(k,-1))!=int(baseline.get(k,-2)) for k in ('reconnects','continuityGaps'))

def score(model, features):
 z=float(model['model']['intercept'])
 for k in model['featureSet']:
  x=float(features[k]); z+=float(model['model']['coefficients'][k])*(x-float(model['model']['means'][k]))/float(model['model']['scales'][k])
 return 1/(1+math.exp(-max(-40,min(40,z))))

def normalize(path, rows):
 # A bounded process batch terminates after every request group, guaranteeing
 # Rust's explicit BufWriter flushes without accumulating a whole partition.
 payload=''.join(json.dumps({'signature':m['signature'],'rawBase64':base64.b64encode(r).decode()})+'\n' for m,r in rows)
 done=subprocess.run([str(path),'--all'],input=payload,text=True,capture_output=True,timeout=45)
 if done.returncode: raise RuntimeError('NORMALIZER_FAILED:'+done.stderr[-500:])
 decoded=[json.loads(x) for x in done.stdout.splitlines() if x.strip()]
 if len(decoded)!=len(rows): raise RuntimeError(f'NORMALIZER_CARDINALITY:{len(decoded)}:{len(rows)}')
 return [(m,x['normalized']) for (m,_),x in zip(rows,decoded) if x.get('ok')]

def frames(f,offset):
 f.seek(offset)
 while True:
  at=f.tell(); head=f.read(8)
  if not head: return
  if head==MAGIC: offset=f.tell(); continue
  if len(head)!=8: f.seek(at); return
  ml,rl=struct.unpack('<II',head)
  if ml+rl>128*1024*1024: raise RuntimeError('UNSAFE_FRAME_LENGTH')
  body=f.read(ml+rl); digest=f.read(32)
  if len(body)!=ml+rl or len(digest)!=32: f.seek(at); return
  if hashlib.sha256(body).digest()!=digest: raise RuntimeError('FRAME_HASH_MISMATCH')
  offset=f.tell(); yield json.loads(body[:ml]),body[ml:],offset

def apply_rows(db, model, rawrows, ticks):
 """Only market frames cause this function; no wallet journal is read."""
 updates=[]
 for meta,tx in rawrows:
  if not tx.get('successfulAtReceipt'): continue
  sig=str(meta['signature']); receipt=int(meta['receivedAtUnixNs']); slot=int(meta['slot']); ix=int(tx.get('transactionIndex') or 0)
  candidate_mints=[]
  try:
   candidate_mints=list(coverage.canonical_create_mints(tx))
   events,err=coverage.decode_causal_events(tx,sig,receipt,slot,ix)
  except Exception as exc:
   db.execute('INSERT OR IGNORE INTO decoder_rejections VALUES(?,?,?,?,?,?,?)',(str(meta['rawSha256']),sig,receipt,slot,f'{type(exc).__name__}:{exc}',json.dumps(sorted(set(candidate_mints))),now()))
   for mint in candidate_mints: db.execute('INSERT OR IGNORE INTO tainted_mints VALUES(?,?,?,?)',(mint,str(meta['rawSha256']),receipt,'DECODER_REJECTION'))
   db.commit(); continue
  for mint in candidate_mints:
   db.execute('INSERT OR IGNORE INTO creates VALUES(?,?,?,?,?,?)',(mint,sig,receipt,slot,meta['rawSha256'],'PENDING')); db.execute('INSERT OR IGNORE INTO slot_queue(slot,receipt_ns) VALUES(?,?)',(slot,receipt))
  if err: continue
  for e in events:
   # Teacher activity is stored only as a later evaluation label. It neither
   # updates autonomous state nor generates a decision tick.
   if e.user==WALLET:
    if e.is_buy:
     label_key=f'{sig}:{e.mint}'
     db.execute('INSERT OR IGNORE INTO teacher_labels VALUES(?,?,?,?,?,?,?,?,?)',(label_key,sig,e.mint,receipt,slot,ix,e.instruction_index,meta['rawSha256'],'PENDING'))
     db.execute('INSERT OR IGNORE INTO slot_queue(slot,receipt_ns) VALUES(?,?)',(slot,receipt))
    continue
   key=f'{sig}:{e.instruction_index}:{e.mint}'
   db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,e.mint,receipt,slot,ix,e.instruction_index,int(e.is_buy),e.user,e.creator,e.quote_lamports,e.token_raw,json.dumps(e.pre_state.__dict__),json.dumps(e.post_state.__dict__),meta['rawSha256'],'PENDING'))
   db.execute('INSERT OR IGNORE INTO slot_queue(slot,receipt_ns) VALUES(?,?)',(slot,receipt)); updates.append(receipt)
 db.commit()
 for receipt in updates: ticks.add(receipt//TICK_NS*TICK_NS)

def emit(db, model, tick_ns, source_epoch):
 if db.execute('SELECT 1 FROM decision_ticks WHERE tick_ns=?',(tick_ns,)).fetchone(): return
 lower=tick_ns-MAX_AGE_NS
 creates={r[0]:r for r in db.execute('SELECT mint,sig,receipt_ns,slot,raw_sha,finality FROM creates WHERE receipt_ns>? AND receipt_ns<=?',(lower,tick_ns))}
 ranked=[]
 for mint,create in creates.items():
  if db.execute('SELECT 1 FROM tainted_mints WHERE mint=?',(mint,)).fetchone(): continue
  rows=list(db.execute('SELECT * FROM events WHERE mint=? AND receipt_ns>? AND receipt_ns<=? ORDER BY receipt_ns,slot,observed_ix,ix,sig',(mint,lower,tick_ns)))
  if not rows: continue
  events=[]
  for r in rows:
   from exactCurveEconomicsV1 import CurveState
   events.append(decomposition.CausalEvent(mint=mint,signature=r[0].split(':',1)[0],slot=r[3],transaction_index=r[4],instruction_index=r[5],received_ns=r[2],is_buy=bool(r[6]),user=r[7],creator=r[8],quote_lamports=r[9],token_raw=r[10],pre_state=CurveState(**json.loads(r[11])),post_state=CurveState(**json.loads(r[12]))))
  e=events[-1]
  try: features=decomposition.state_features(e.post_state,e.received_ns,mint,e.creator,events,int(create[2]))
  except Exception: continue
  if any(k not in features or not math.isfinite(float(features[k])) for k in model['featureSet']): continue
  ranked.append((score(model,features),mint,features,events,create))
 ranked.sort(reverse=True,key=lambda x:(x[0],x[1])); top=ranked[0] if ranked else None; second=ranked[1][0] if len(ranked)>1 else None
 prior_tick=db.execute('SELECT tick_ns FROM decision_ticks WHERE tick_ns<? ORDER BY tick_ns DESC LIMIT 1',(tick_ns,)).fetchone()
 prior={} if not prior_tick else {r[0]:int(r[1]) for r in db.execute('SELECT mint,persistence FROM decision_candidates WHERE tick_ns=?',(prior_tick[0],))}
 snapshot=[]
 for rank,(value,mint,features,events,create) in enumerate(ranked,1):
  cprov=hashlib.sha256(json.dumps({'createRawSha':create[4],'eventRawShas':[r[0] for r in db.execute('SELECT raw_sha FROM events WHERE mint=? AND receipt_ns>? AND receipt_ns<=? ORDER BY receipt_ns,slot,observed_ix,ix,sig',(mint,lower,tick_ns))]},sort_keys=True,separators=(',',':')).encode()).hexdigest()
  snapshot.append((rank,value,mint,features,events,create,cprov))
 set_sha=hashlib.sha256(json.dumps([{'mint':x[2],'rank':x[0],'score':x[1],'create':x[5][1],'event':x[4][-1].signature} for x in snapshot],sort_keys=True,separators=(',',':')).encode()).hexdigest()
 prov=hashlib.sha256(json.dumps({'tick':tick_ns,'rawShas':[r[0] for r in db.execute('SELECT raw_sha FROM events WHERE receipt_ns>=? AND receipt_ns<? ORDER BY raw_sha',(tick_ns,tick_ns+TICK_NS))]},sort_keys=True,separators=(',',':')).encode()).hexdigest()
 db.execute('INSERT INTO decision_ticks VALUES(?,?,?,?,?,?,?,?,?,?,?)',(tick_ns,source_epoch,prov,set_sha,None if not top else top[1],None if not top else top[0],second,None if not top else (top[0]-second if second is not None else None),len(snapshot),'PENDING',now()))
 for rank,value,mint,features,events,create,cprov in snapshot:
  db.execute('INSERT INTO decision_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(tick_ns,mint,rank,float(value),0.0 if not top else float(top[0]-value),prior.get(mint,0)+1,str(create[1]),int(create[2]),events[-1].signature,events[-1].received_ns,json.dumps({k:float(features[k]) for k in model['featureSet']},sort_keys=True),cprov,'PENDING'))
 db.commit()

def finalize(db,url,current_slot):
 slots=[r[0] for r in db.execute('SELECT slot FROM slot_queue WHERE finality="PENDING" AND slot<? ORDER BY slot LIMIT 8',(current_slot-64,))]
 for slot in slots:
  block=rpc(url,'getBlock',[slot,{'commitment':'finalized','encoding':'json','transactionDetails':'full','rewards':False,'maxSupportedTransactionVersion':1}])
  if not block: continue
  good={str((t.get('transaction') or {}).get('signatures',[None])[0]) for t in block.get('transactions') or [] if (t.get('meta') or {}).get('err') is None}
  bad=db.execute('SELECT count(*) FROM events WHERE slot=? AND substr(sig,1,instr(sig,\':\')-1) NOT IN (%s)'%(','.join('?'*len(good)) or "''"),(slot,*good)).fetchone()[0] if good else db.execute('SELECT count(*) FROM events WHERE slot=?',(slot,)).fetchone()[0]
  bad_labels=db.execute('SELECT count(*) FROM teacher_labels WHERE slot=? AND sig NOT IN (%s)'%(','.join('?'*len(good)) or "''"),(slot,*good)).fetchone()[0] if good else db.execute('SELECT count(*) FROM teacher_labels WHERE slot=?',(slot,)).fetchone()[0]
  if bad or bad_labels: raise RuntimeError('FINALITY_PROOF_FAILED_SLOT:'+str(slot))
  db.execute('UPDATE events SET finality="FINAL" WHERE slot=?',(slot,)); db.execute('UPDATE creates SET finality="FINAL" WHERE slot=?',(slot,)); db.execute('UPDATE teacher_labels SET finality="FINAL" WHERE slot=?',(slot,)); db.execute('UPDATE slot_queue SET finality="FINAL" WHERE slot=?',(slot,)); db.commit()
 pending=db.execute('SELECT min(receipt_ns) FROM slot_queue WHERE finality="PENDING"').fetchone()[0]
 if pending is not None:
  db.execute('UPDATE decision_ticks SET finality="FINAL" WHERE finality="PENDING" AND tick_ns<?',(pending,)); db.execute('UPDATE decision_candidates SET finality="FINAL" WHERE finality="PENDING" AND tick_ns<?',(pending,)); db.commit()

def main():
 p=argparse.ArgumentParser(); p.add_argument('--root',type=pathlib.Path,required=True); p.add_argument('--normalizer',type=pathlib.Path,required=True); p.add_argument('--fingerprint',type=pathlib.Path,required=True); p.add_argument('--env',type=pathlib.Path,required=True); p.add_argument('--spec',type=pathlib.Path,required=True); p.add_argument('--boundary',type=pathlib.Path,required=True); a=p.parse_args()
 model=json.loads(a.fingerprint.read_text());
 if sha(a.fingerprint)!='7606a3a138591516f503e7f7b56501f1828b92947a2b38707bff166fbb233b64': raise RuntimeError('FINGERPRINT_HASH_MISMATCH')
 if not a.spec.exists(): raise RuntimeError('FROZEN_SPEC_MISSING')
 a.root.mkdir(parents=True,exist_ok=True)
 boundary=json.loads(a.boundary.read_text()); boundary_ns=parse_utc_ns(boundary['boundaryUtc']); db=open_db(a.root/'autonomous-when-v2.sqlite3'); health=a.root.parent/'health.json'; fd=None; current=None; offset=0; url=env(a.env,'SECURE_RPC_URL')
 try:
  while True:
   h=json.loads(health.read_text())
   if source_continuity_changed(h,boundary): raise RuntimeError('SOURCE_CONTINUITY_UNCERTAIN')
   parts=sorted(a.root.parent.glob('partitions/*/*/*/*/evidence.frames.partial'))
   path=parts[-1] if parts else None
   if path and path!=current:
    if fd: fd.close()
    saved_path=meta_get(db,'source_path'); saved_offset=meta_get(db,'source_offset')
    fd=path.open('rb'); current=path
    offset=int(saved_offset) if saved_path==str(path) and saved_offset is not None else 0
    if offset>fd.seek(0,2): raise RuntimeError('CURSOR_BEYOND_SOURCE')
   batch=[]; ticks=set()
   if fd:
    for meta,raw,offset in frames(fd,offset):
     if int(meta.get('receivedAtUnixNs',0))>=boundary_ns and meta.get('kind')=='transaction' and meta.get('rawFormat')=='yellowstone_subscribe_update_protobuf_full_meta': batch.append((meta,raw))
     if len(batch)>=128: apply_rows(db,model,normalize(a.normalizer,batch),ticks); batch=[]
   if batch: apply_rows(db,model,normalize(a.normalizer,batch),ticks)
   for tick in sorted(ticks): emit(db,model,tick,int(h['currentEpoch']))
   finalize(db,url,int(h['latestSlot']))
   if current: meta_set(db,'source_path',current); meta_set(db,'source_offset',offset); db.commit()
   write_json(a.root/'health.json',{'schema':'2CQ_AUTONOMOUS_WHEN_V2_RANK_SNAPSHOT_HEALTH','updatedAtUtc':now(),'sourcePath':str(current) if current else None,'cursorOffset':offset,'sourceFramesSeen':int(db.execute('select count(*) from events').fetchone()[0]),'ticks':int(db.execute('select count(*) from decision_ticks').fetchone()[0]),'candidateRanks':int(db.execute('select count(*) from decision_candidates').fetchone()[0]),'teacherLabelsFinal':int(db.execute('select count(*) from teacher_labels where finality="FINAL"').fetchone()[0]),'teacherLabelsPending':int(db.execute('select count(*) from teacher_labels where finality="PENDING"').fetchone()[0]),'decoderRejections':int(db.execute('select count(*) from decoder_rejections').fetchone()[0]),'taintedMints':int(db.execute('select count(*) from tainted_mints').fetchone()[0]),'pendingSlots':int(db.execute('select count(*) from slot_queue where finality="PENDING"').fetchone()[0]),'sourceEpoch':h['currentEpoch'],'continuityGaps':h['continuityGaps'],'outcomeBlind':True})
   time.sleep(.2)
 finally:
  if fd: fd.close()
  db.close()
if __name__=='__main__': main()
