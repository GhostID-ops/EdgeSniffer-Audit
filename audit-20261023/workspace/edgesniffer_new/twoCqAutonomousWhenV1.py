#!/usr/bin/env python3
"""Bounded, teacher-independent prospective AUTONOMOUS_WHEN_V1 observation lane."""
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
FINALIZE_BATCH_SIZE = 64
FINALIZE_MAX_SIGNATURES = 256
FINALIZE_MIN_INTERVAL_SECONDS = 2
FINALIZER_OWNED = os.environ.get('TWO_CQ_AUTONOMOUS_WHEN_FINALIZER_OWNED') == '1'

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
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
 last=None
 for attempt in range(3):
  try:
   q=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
   with urllib.request.urlopen(q,timeout=50) as r: x=json.load(r)
   if x.get('error'): raise RuntimeError('RPC:'+str(x['error']))
   return x.get('result')
  except OSError as exc:
   last=exc
   if attempt==2: raise
   time.sleep(.25*(attempt+1))
 raise last

def open_db(p):
 c=sqlite3.connect(p,timeout=60); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=FULL'); c.execute('PRAGMA busy_timeout=60000')
 c.executescript('''CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS events(sig TEXT PRIMARY KEY,mint TEXT,receipt_ns INTEGER,slot INTEGER,observed_ix INTEGER,ix INTEGER,is_buy INTEGER,user TEXT,creator TEXT,quote INTEGER,token INTEGER,pre_json TEXT,post_json TEXT,raw_sha TEXT,finality TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS events_receipt ON events(receipt_ns,finality);
 CREATE TABLE IF NOT EXISTS creates(mint TEXT PRIMARY KEY,sig TEXT,receipt_ns INTEGER,slot INTEGER,raw_sha TEXT,finality TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS teacher_labels(label_key TEXT PRIMARY KEY,sig TEXT,mint TEXT,receipt_ns INTEGER,slot INTEGER,observed_ix INTEGER,ix INTEGER,raw_sha TEXT,finality TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS teacher_labels_receipt ON teacher_labels(receipt_ns,finality);
 CREATE TABLE IF NOT EXISTS decoder_rejections(raw_sha TEXT PRIMARY KEY,sig TEXT,receipt_ns INTEGER,slot INTEGER,source_path TEXT,source_offset INTEGER,error TEXT NOT NULL,candidate_mints_json TEXT NOT NULL,created_utc TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS tainted_mints(mint TEXT PRIMARY KEY,raw_sha TEXT NOT NULL,receipt_ns INTEGER NOT NULL,reason TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS slot_queue(slot INTEGER PRIMARY KEY,receipt_ns INTEGER,finality TEXT NOT NULL DEFAULT 'PENDING');
 CREATE TABLE IF NOT EXISTS decisions(tick_ns INTEGER PRIMARY KEY,top_mint TEXT,top_score REAL,second_score REAL,margin REAL,universe_n INTEGER,persistence INTEGER,features_json TEXT,provenance_sha TEXT,finality TEXT NOT NULL,created_utc TEXT);
 CREATE INDEX IF NOT EXISTS events_slot ON events(slot);
 CREATE INDEX IF NOT EXISTS creates_slot ON creates(slot);
 CREATE INDEX IF NOT EXISTS teacher_labels_slot ON teacher_labels(slot);
 CREATE INDEX IF NOT EXISTS slot_queue_finality_slot ON slot_queue(finality,slot);
 CREATE INDEX IF NOT EXISTS decisions_finality_tick ON decisions(finality,tick_ns);
 '''); c.commit(); return c
def meta_get(db,k):
 r=db.execute('SELECT v FROM meta WHERE k=?',(k,)).fetchone()
 return r[0] if r else None
def meta_set(db,k,v):
 db.execute('INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,str(v)))

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
   db.execute('INSERT OR IGNORE INTO decoder_rejections VALUES(?,?,?,?,?,?,?,?,?)',(meta['rawSha256'],sig,receipt,slot,str(meta.get('_laneSourcePath','')),int(meta.get('_laneOffset',0)),f'{type(exc).__name__}:{exc}',json.dumps(sorted(set(candidate_mints))),now()))
   for mint in candidate_mints:
    db.execute('INSERT OR IGNORE INTO tainted_mints VALUES(?,?,?,?)',(mint,meta['rawSha256'],receipt,'DECODER_REJECTION'))
   db.commit()
   continue
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

def emit(db, model, tick_ns, last_top):
 if db.execute('SELECT 1 FROM decisions WHERE tick_ns=?',(tick_ns,)).fetchone(): return last_top
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
 if not ranked: return last_top
 ranked.sort(reverse=True,key=lambda x:(x[0],x[1])); top=ranked[0]; second=ranked[1][0] if len(ranked)>1 else None
 persist=(last_top[1]+1 if last_top and last_top[0]==top[1] else 1)
 prov=hashlib.sha256(json.dumps({'tick':tick_ns,'top':top[1],'event':top[3][-1].signature,'create':top[4][1]},sort_keys=True).encode()).hexdigest()
 db.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(tick_ns,top[1],top[0],second,(top[0]-second if second is not None else None),len(ranked),persist,json.dumps({k:float(top[2][k]) for k in model['featureSet']},sort_keys=True),prov,'PENDING',now())); db.commit()
 return (top[1],persist)

def finalize(db,url,current_slot):
 slots=[r[0] for r in db.execute('SELECT slot FROM slot_queue WHERE finality="PENDING" AND slot<? ORDER BY slot LIMIT ?',(current_slot-64,FINALIZE_BATCH_SIZE))]
 if not slots: return 0
 finalized=0
 expected={}
 for slot in slots:
  sigs={r[0].split(':',1)[0] for r in db.execute('SELECT sig FROM events WHERE slot=?',(slot,))}
  sigs.update(r[0] for r in db.execute('SELECT sig FROM creates WHERE slot=?',(slot,)))
  sigs.update(r[0] for r in db.execute('SELECT sig FROM teacher_labels WHERE slot=?',(slot,)))
  expected[slot]=sorted(sigs)
 groups=[]; group=[]; count=0
 for slot in slots:
  if group and count+len(expected[slot])>FINALIZE_MAX_SIGNATURES:
   groups.append(group); group=[]; count=0
  group.append(slot); count+=len(expected[slot])
 if group: groups.append(group)
 for group in groups:
  sigs=[sig for slot in group for sig in expected[slot]]
  statuses=rpc(url,'getSignatureStatuses',[sigs,{'searchTransactionHistory':True}])
  proofs=dict(zip(sigs,statuses.get('value') or []))
  for slot in group:
   slot_proofs=[proofs.get(sig) for sig in expected[slot]]
   if any(proof is None or proof.get('confirmationStatus')!='finalized' for proof in slot_proofs): continue
   if any(proof.get('err') is not None or int(proof.get('slot',-1))!=slot for proof in slot_proofs): raise RuntimeError('FINALITY_PROOF_FAILED_SLOT:'+str(slot))
   db.execute('UPDATE events SET finality="FINAL" WHERE slot=?',(slot,)); db.execute('UPDATE creates SET finality="FINAL" WHERE slot=?',(slot,)); db.execute('UPDATE teacher_labels SET finality="FINAL" WHERE slot=?',(slot,)); db.execute('UPDATE slot_queue SET finality="FINAL" WHERE slot=?',(slot,)); db.commit()
   finalized+=1
 pending=db.execute('SELECT min(receipt_ns) FROM slot_queue WHERE finality="PENDING"').fetchone()[0]
 if pending is not None: db.execute('UPDATE decisions SET finality="FINAL" WHERE finality="PENDING" AND tick_ns<?',(pending,)); db.commit()
 return finalized

def main():
 p=argparse.ArgumentParser(); p.add_argument('--root',type=pathlib.Path,required=True); p.add_argument('--normalizer',type=pathlib.Path,required=True); p.add_argument('--fingerprint',type=pathlib.Path,required=True); p.add_argument('--env',type=pathlib.Path,required=True); p.add_argument('--spec',type=pathlib.Path,required=True); p.add_argument('--boundary',type=pathlib.Path,required=True); a=p.parse_args()
 model=json.loads(a.fingerprint.read_text());
 if sha(a.fingerprint)!='7606a3a138591516f503e7f7b56501f1828b92947a2b38707bff166fbb233b64': raise RuntimeError('FINGERPRINT_HASH_MISMATCH')
 if not a.spec.exists(): raise RuntimeError('FROZEN_SPEC_MISSING')
 a.root.mkdir(parents=True,exist_ok=True)
 db=open_db(a.root/'autonomous-when-v1.sqlite3'); health=a.root.parent/'health.json'; fd=None; current=None; offset=0; last_top=None; url=env(a.env,'SECURE_RPC_URL'); next_finalize=0.0
 try:
  while True:
   h=json.loads(health.read_text())
   if int(h['reconnects']) or int(h['continuityGaps']): raise RuntimeError('SOURCE_CONTINUITY_UNCERTAIN')
   parts=sorted(a.root.parent.glob('partitions/*/*/*/*/evidence.frames.partial'))
   path=parts[-1] if parts else None
   if path and path!=current:
    if fd: fd.close()
    saved_path=meta_get(db,'source_path'); saved_offset=meta_get(db,'source_offset')
    fd=path.open('rb'); current=path
    offset=int(saved_offset) if saved_path==str(path) and saved_offset is not None else (fd.seek(0,2) if not a.boundary.exists() else 0)
    if offset>fd.seek(0,2): raise RuntimeError('CURSOR_BEYOND_SOURCE')
    if not a.boundary.exists(): write_json(a.boundary,{'schema':'2CQ_AUTONOMOUS_WHEN_V1_START_V1','boundaryUtc':now(),'sourceEpoch':h['currentEpoch'],'fingerprintSha256':sha(a.fingerprint),'specSha256':sha(a.spec),'laneSourceSha256':sha(pathlib.Path(__file__)),'dependencySha256':{'decisionSystem':sha(pathlib.Path(decomposition.__file__)),'choiceCoverage':sha(pathlib.Path(coverage.__file__))},'collectorBinarySha256':(a.root.parent/'collector-binary.sha256').read_text().strip(),'outcomeBlind':True,'pnlRead':0},True)
   batch=[]; ticks=set()
   if fd:
     for meta,raw,offset in frames(fd,offset):
      if meta.get('kind')=='transaction' and meta.get('rawFormat')=='yellowstone_subscribe_update_protobuf_full_meta':
       meta=dict(meta); meta['_laneSourcePath']=str(current); meta['_laneOffset']=offset; batch.append((meta,raw))
       if len(batch)>=128:
        apply_rows(db,model,normalize(a.normalizer,batch),ticks); batch=[]
        for tick in sorted(ticks): last_top=emit(db,model,tick,last_top)
        ticks.clear()
        # A live partition may never reach EOF. Bound finality work by ingested
        # frames so the independent finalized-slot frontier cannot starve.
        if not FINALIZER_OWNED and time.monotonic()>=next_finalize:
         live_h=json.loads(health.read_text())
         if int(live_h['reconnects']) or int(live_h['continuityGaps']): raise RuntimeError('SOURCE_CONTINUITY_UNCERTAIN')
         finalize(db,url,int(live_h['latestSlot']))
         next_finalize=time.monotonic()+FINALIZE_MIN_INTERVAL_SECONDS
   if batch: apply_rows(db,model,normalize(a.normalizer,batch),ticks)
   for tick in sorted(ticks): last_top=emit(db,model,tick,last_top)
   if not FINALIZER_OWNED: finalize(db,url,int(h['latestSlot']))
   if current: meta_set(db,'source_path',current); meta_set(db,'source_offset',offset); db.commit()
   write_json(a.root/'health.json',{'schema':'2CQ_AUTONOMOUS_WHEN_V1_HEALTH','updatedAtUtc':now(),'sourcePath':str(current) if current else None,'cursorOffset':offset,'sourceFramesSeen':int(db.execute('select count(*) from events').fetchone()[0]),'ticks':int(db.execute('select count(*) from decisions').fetchone()[0]),'teacherLabelsFinal':int(db.execute('select count(*) from teacher_labels where finality="FINAL"').fetchone()[0]),'teacherLabelsPending':int(db.execute('select count(*) from teacher_labels where finality="PENDING"').fetchone()[0]),'decoderRejections':int(db.execute('select count(*) from decoder_rejections').fetchone()[0]),'taintedMints':int(db.execute('select count(*) from tainted_mints').fetchone()[0]),'pendingSlots':int(db.execute('select count(*) from slot_queue where finality="PENDING"').fetchone()[0]),'sourceEpoch':h['currentEpoch'],'continuityGaps':h['continuityGaps'],'outcomeBlind':True})
   time.sleep(.2)
 finally:
  if fd: fd.close()
  db.close()
if __name__=='__main__': main()
