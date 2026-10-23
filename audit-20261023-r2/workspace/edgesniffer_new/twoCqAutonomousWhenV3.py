#!/usr/bin/env python3
"""Teacher-independent AUTONOMOUS_WHEN_V2 rank-snapshot capture lane."""
from __future__ import annotations
import argparse, base64, fcntl, hashlib, json, math, os, pathlib, sqlite3, struct, subprocess, sys, threading, time, traceback, urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import twoCqDecisionSystemDecompositionV1 as decomposition
import twoCqChoiceSetCoverageRepairV1 as coverage

PUMP_SOURCE = "yellowstone-v3-dual-live-canonical-source-plane"
WALLET = decomposition.WALLET
MAX_AGE_NS = 120_000_000_000
TICK_NS = 1_000_000_000
CADENCE_TOLERANCE_NS = 2_000_000_000
MAX_NORMALIZER_BATCH = 128
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
 CREATE INDEX IF NOT EXISTS events_mint_receipt_order ON events(mint,receipt_ns,slot,observed_ix,ix,sig);
 CREATE TABLE IF NOT EXISTS creates(mint TEXT PRIMARY KEY,sig TEXT,receipt_ns INTEGER,slot INTEGER,raw_sha TEXT,finality TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS teacher_labels(label_key TEXT PRIMARY KEY,sig TEXT,mint TEXT,receipt_ns INTEGER,slot INTEGER,observed_ix INTEGER,ix INTEGER,raw_sha TEXT,finality TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS teacher_labels_receipt ON teacher_labels(receipt_ns,finality);
 CREATE TABLE IF NOT EXISTS slot_queue(slot INTEGER PRIMARY KEY,receipt_ns INTEGER,finality TEXT NOT NULL DEFAULT 'PENDING');
 CREATE TABLE IF NOT EXISTS decoder_rejections(raw_sha TEXT PRIMARY KEY,sig TEXT,receipt_ns INTEGER,slot INTEGER,error TEXT NOT NULL,candidate_mints_json TEXT NOT NULL,created_utc TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS tainted_mints(mint TEXT PRIMARY KEY,raw_sha TEXT NOT NULL,receipt_ns INTEGER NOT NULL,reason TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS decisions(tick_ns INTEGER PRIMARY KEY,top_mint TEXT,top_score REAL,second_score REAL,margin REAL,universe_n INTEGER,persistence INTEGER,features_json TEXT,provenance_sha TEXT,finality TEXT NOT NULL,created_utc TEXT);
 CREATE TABLE IF NOT EXISTS decision_ticks(tick_ns INTEGER PRIMARY KEY,source_epoch INTEGER NOT NULL,tick_provenance_sha TEXT NOT NULL,candidate_set_sha TEXT NOT NULL,top_mint TEXT,top_score REAL,second_score REAL,margin REAL,universe_n INTEGER NOT NULL,finality TEXT NOT NULL,created_utc TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS decision_candidates(tick_ns INTEGER NOT NULL,mint TEXT NOT NULL,rank INTEGER NOT NULL,score REAL NOT NULL,margin_to_top REAL NOT NULL,persistence INTEGER NOT NULL,create_sig TEXT NOT NULL,create_receipt_ns INTEGER NOT NULL,last_event_sig TEXT NOT NULL,last_event_receipt_ns INTEGER NOT NULL,features_json TEXT NOT NULL,candidate_provenance_sha TEXT NOT NULL,finality TEXT NOT NULL,PRIMARY KEY(tick_ns,mint));
 CREATE TABLE IF NOT EXISTS cadence_ticks(tick_ns INTEGER PRIMARY KEY,emitted_ns INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS cadence_telemetry(tick_ns INTEGER PRIMARY KEY,wake_lateness_ns INTEGER NOT NULL,writer_wait_ns INTEGER NOT NULL,snapshot_ns INTEGER NOT NULL,commit_ns INTEGER NOT NULL,candidate_count INTEGER NOT NULL,source_watermark_ns INTEGER NOT NULL,created_utc TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS ingestion_batches(batch_id INTEGER PRIMARY KEY,source_offset_start INTEGER NOT NULL,source_offset_end INTEGER NOT NULL,raw_record_count INTEGER NOT NULL,first_receipt_ns INTEGER,last_receipt_ns INTEGER,read_start_ns INTEGER NOT NULL,read_finish_ns INTEGER NOT NULL,normalize_start_ns INTEGER,normalize_finish_ns INTEGER,materialize_start_ns INTEGER,materialize_finish_ns INTEGER,watermark_published_ns INTEGER,source_watermark_ns INTEGER NOT NULL,read_watermark_ns INTEGER NOT NULL,normalized_watermark_ns INTEGER NOT NULL,materialized_watermark_ns INTEGER NOT NULL,queue_depth INTEGER NOT NULL,batch_status TEXT NOT NULL,failure_reason TEXT,created_utc TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS decision_candidates_mint_tick ON decision_candidates(mint,tick_ns);
 '''); c.commit(); return c
def meta_get(db,k):
 r=db.execute('SELECT v FROM meta WHERE k=?',(k,)).fetchone()
 return r[0] if r else None
def meta_set(db,k,v):
 db.execute('INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,str(v)))

@contextmanager
def writer_lease(root, timeout_seconds=None):
 """Serialize SQLite writers without changing causal ordering or contents."""
 with (root/'sqlite-writer.lock').open('a+') as lock:
  started=time.perf_counter_ns()
  if timeout_seconds is None:
   fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
  else:
   deadline=time.monotonic()+timeout_seconds
   while True:
    try:
     fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB); break
    except BlockingIOError:
     if time.monotonic()>=deadline: raise TimeoutError('SQLITE_WRITER_LEASE_TIMEOUT')
     time.sleep(.005)
  acquired=time.perf_counter_ns()
  try: yield acquired-started
  finally: fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

class MaterializationFrontier:
 """Publishes only causally complete state; raw availability is never a snapshot gate.

 The scheduler takes a short immutable copy of this state.  It never holds the
 ingestion lock while constructing a snapshot, so an unrelated snapshot cannot
 block materialization of the next boundary.
 """
 def __init__(self):
  self.condition=threading.Condition()
  self.inflight=False; self.inflight_first_ns=0; self.inflight_last_ns=0
  self.source_watermark_ns=0; self.read_watermark_ns=0
  self.normalized_watermark_ns=0; self.materialized_watermark_ns=0
  self.batch_id=0
 def begin_batch(self,batch_id,first_ns=0,last_ns=0):
  with self.condition:
   self.inflight=True; self.batch_id=batch_id
   self.inflight_first_ns=int(first_ns or 0); self.inflight_last_ns=int(last_ns or 0)
   self.condition.notify_all()
 def read_complete(self,last_ns):
  with self.condition:
   self.read_watermark_ns=max(self.read_watermark_ns,int(last_ns or 0))
   self.source_watermark_ns=max(self.source_watermark_ns,int(last_ns or 0))
   self.condition.notify_all()
 def normalized(self,last_ns):
  with self.condition:
   self.normalized_watermark_ns=max(self.normalized_watermark_ns,int(last_ns or 0))
   self.condition.notify_all()
 def materialized(self,last_ns):
  """Call only after the causal-state SQLite transaction has committed."""
  with self.condition:
   self.materialized_watermark_ns=max(self.materialized_watermark_ns,int(last_ns or 0))
   self.inflight=False; self.inflight_first_ns=0; self.inflight_last_ns=0
   self.condition.notify_all()
 def fail_batch(self):
  with self.condition:
   self.inflight=False; self.inflight_first_ns=0; self.inflight_last_ns=0
   self.condition.notify_all()
 def snapshot_ready(self,tick_ns):
  with self.condition:
   # A batch that can affect T must commit before T can be emitted.  A later,
   # future-only batch is harmless because emit() also applies receipt<=T cuts.
   relevant_inflight=self.inflight and self.inflight_first_ns<=tick_ns
   if relevant_inflight or self.materialized_watermark_ns<tick_ns: return None
   return self.materialized_watermark_ns
 def wait_for_change(self,timeout_seconds):
  with self.condition: self.condition.wait(timeout_seconds)
 def state(self):
  with self.condition:
   return {'inflight':self.inflight,'inflightFirstNs':self.inflight_first_ns,'inflightLastNs':self.inflight_last_ns,'batchId':self.batch_id,'sourceWatermarkNs':self.source_watermark_ns,'readWatermarkNs':self.read_watermark_ns,'normalizedWatermarkNs':self.normalized_watermark_ns,'materializedWatermarkNs':self.materialized_watermark_ns}

def source_continuity_changed(h,b):
 """V3 delegates dual-observer timing/recovery to its canonical adapter.

 A recoverable single-observer reconnect is explicitly represented in the V3
 witness journals and must not invalidate the merged causal stream.  Only the
 adapter's durable terminal state may invalidate downstream materialization.
 """
 if h.get('globalState') == 'FAIL_CLOSED': return True
 global_state=h.get('global') or {}
 return global_state.get('mode') == 'FAIL_CLOSED'

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

def frames(f,offset,seekable=True):
 if seekable: f.seek(offset)
 position=offset if seekable else 0
 while True:
  at=position; head=f.read(8); position+=len(head)
  if not head: return
  if head==MAGIC:
   if at>=offset: offset=position
   continue
  if len(head)!=8:
   if seekable: f.seek(at)
   return
  ml,rl=struct.unpack('<II',head)
  if ml+rl>128*1024*1024: raise RuntimeError('UNSAFE_FRAME_LENGTH')
  body=f.read(ml+rl); digest=f.read(32); position+=len(body)+len(digest)
  if len(body)!=ml+rl or len(digest)!=32:
   if seekable: f.seek(at)
   return
  if hashlib.sha256(body).digest()!=digest: raise RuntimeError('FRAME_HASH_MISMATCH')
  if position<=offset: continue
  if at<offset: raise RuntimeError('CURSOR_NOT_ON_FRAME_BOUNDARY')
  offset=position; yield json.loads(body[:ml]),body[ml:],offset

def source_parts(base):
 parts=[]
 for directory in sorted(base.glob('partitions/*/*/*/*')):
  partial=directory/'evidence.frames.partial'; compressed=directory/'evidence.frames.zst'
  if partial.exists(): parts.append((str(directory),partial))
  elif compressed.exists(): parts.append((str(directory),compressed))
 return parts

def open_source(path,offset):
 if path.suffix=='.zst':
  proc=subprocess.Popen(['zstd','-dc','--',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  return proc.stdout,proc,False
 handle=path.open('rb')
 if offset>handle.seek(0,2): raise RuntimeError('CURSOR_BEYOND_SOURCE')
 return handle,None,True

def apply_rows(db, model, rawrows, ticks, certified_from_ns, commit=True):
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
   if commit: db.commit()
   continue
  for mint in candidate_mints:
   db.execute('INSERT OR IGNORE INTO creates VALUES(?,?,?,?,?,?)',(mint,sig,receipt,slot,meta['rawSha256'],'PENDING')); db.execute('INSERT OR IGNORE INTO slot_queue(slot,receipt_ns) VALUES(?,?)',(slot,receipt))
  if err: continue
  for e in events:
   # Teacher activity is stored only as a later evaluation label. It neither
   # updates autonomous state nor generates a decision tick.
   if e.user==WALLET:
    if e.is_buy:
     # Warm-up events may establish causal state, but can never become
     # certified teacher labels for the new segment.
     if receipt>=certified_from_ns:
      label_key=f'{sig}:{e.mint}'
      db.execute('INSERT OR IGNORE INTO teacher_labels VALUES(?,?,?,?,?,?,?,?,?)',(label_key,sig,e.mint,receipt,slot,ix,e.instruction_index,meta['rawSha256'],'PENDING'))
      db.execute('INSERT OR IGNORE INTO slot_queue(slot,receipt_ns) VALUES(?,?)',(slot,receipt))
    continue
   key=f'{sig}:{e.instruction_index}:{e.mint}'
   db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,e.mint,receipt,slot,ix,e.instruction_index,int(e.is_buy),e.user,e.creator,e.quote_lamports,e.token_raw,json.dumps(e.pre_state.__dict__),json.dumps(e.post_state.__dict__),meta['rawSha256'],'PENDING'))
   db.execute('INSERT OR IGNORE INTO slot_queue(slot,receipt_ns) VALUES(?,?)',(slot,receipt))
   if receipt>=certified_from_ns: updates.append(receipt)
 if commit: db.commit()
 for receipt in updates: ticks.add(receipt//TICK_NS*TICK_NS)

def emit(db, model, tick_ns, source_epoch, emitted_ns):
 if db.execute('SELECT 1 FROM decision_ticks WHERE tick_ns=?',(tick_ns,)).fetchone(): return {'snapshotNs':0,'commitNs':0,'candidateCount':0}
 started=time.perf_counter_ns()
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
  # A timer tick is the observation instant. The causal event supplies state,
  # but never advances time-dependent features beyond this scheduled instant.
  try: features=decomposition.state_features(e.post_state,tick_ns,mint,e.creator,events,int(create[2]))
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
 snapshot_ns=time.perf_counter_ns()-started; commit_started=time.perf_counter_ns()
 db.execute('INSERT INTO decision_ticks VALUES(?,?,?,?,?,?,?,?,?,?,?)',(tick_ns,source_epoch,prov,set_sha,None if not top else top[1],None if not top else top[0],second,None if not top else (top[0]-second if second is not None else None),len(snapshot),'PENDING',now()))
 for rank,value,mint,features,events,create,cprov in snapshot:
  db.execute('INSERT INTO decision_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(tick_ns,mint,rank,float(value),0.0 if not top else float(top[0]-value),prior.get(mint,0)+1,str(create[1]),int(create[2]),events[-1].signature,events[-1].received_ns,json.dumps({k:float(features[k]) for k in model['featureSet']},sort_keys=True),cprov,'PENDING'))
 db.execute('INSERT INTO cadence_ticks VALUES(?,?)',(tick_ns,emitted_ns))
 db.commit()
 return {'snapshotNs':snapshot_ns,'commitNs':time.perf_counter_ns()-commit_started,'candidateCount':len(snapshot)}

def scheduled_snapshot_loop(root, model, health_path, boundary, timer_start_ns, next_tick_ns, frontier):
 """Run wall-clock snapshots independently from bounded source ingestion."""
 timer_db=open_db(root/'autonomous-when-v2.sqlite3')
 failure_context={}
 try:
  while True:
   h=json.loads(health_path.read_text())
   if source_continuity_changed(h,boundary):
    raise RuntimeError('SOURCE_CONTINUITY_UNCERTAIN')
   current_ns=time.time_ns()
   if current_ns<next_tick_ns:
    time.sleep(min(.1,max(.002,(next_tick_ns-current_ns)/TICK_NS)))
    continue
   scheduler_wait_started=time.perf_counter_ns()
   watermark_ns=frontier.snapshot_ready(next_tick_ns)
   if watermark_ns is None:
    if time.time_ns()-next_tick_ns>CADENCE_TOLERANCE_NS:
     failure_context={'stage':'MATERIALIZED_WATERMARK','frontier':frontier.state()}
     raise RuntimeError('SCHEDULED_TICK_CADENCE_VIOLATION')
    frontier.wait_for_change(.002); continue
   with writer_lease(root,CADENCE_TOLERANCE_NS/TICK_NS) as writer_wait_ns:
    emitted_ns=time.time_ns()
    if emitted_ns-next_tick_ns>CADENCE_TOLERANCE_NS:
     failure_context={'stage':'SQLITE_WRITER_LEASE','materializedWatermarkNs':watermark_ns,'writerWaitNs':writer_wait_ns}
     raise RuntimeError('SCHEDULED_TICK_CADENCE_VIOLATION')
    stats=emit(timer_db,model,next_tick_ns,int(h['currentEpoch']),emitted_ns)
    timer_db.execute('INSERT INTO cadence_telemetry VALUES(?,?,?,?,?,?,?,?)',(next_tick_ns,emitted_ns-next_tick_ns,writer_wait_ns,stats['snapshotNs'],stats['commitNs'],stats['candidateCount'],watermark_ns,now()))
    next_tick_ns+=TICK_NS
    meta_set(timer_db,'next_scheduled_tick_ns',next_tick_ns); timer_db.commit()
 except BaseException as exc:
  failure=root/'cadence-failure.json'
  if not failure.exists():
   write_json(failure,{'schema':'AUTONOMOUS_WHEN_V2_CADENCE_FAILURE_V2','detectedAtUtc':now(),'timerStartNs':timer_start_ns,'nextScheduledTickNs':next_tick_ns,'error':f'{type(exc).__name__}:{exc}','toleranceNs':CADENCE_TOLERANCE_NS,'context':failure_context},immutable=True)
  # A scientific cadence failure must terminate the supervised lane promptly;
  # the monitor and service limits then preserve a visible fail-closed state.
  os._exit(1)
 finally:
  timer_db.close()

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
 boundary=json.loads(a.boundary.read_text()); boundary_ns=parse_utc_ns(boundary['boundaryUtc']); warmup_ns=int(boundary.get('warmupSeconds',120))*TICK_NS; certified_from_ns=boundary_ns+warmup_ns; configured_timer_start_ns=((certified_from_ns+TICK_NS-1)//TICK_NS)*TICK_NS; db=open_db(a.root/'autonomous-when-v2.sqlite3'); health=a.root.parent/'health.json'; fd=None; decompressor=None; current_key=None; current_path=None; offset=0; last_normalize_ns=0; last_apply_ns=0; last_batch_count=0
 saved_next=meta_get(db,'next_scheduled_tick_ns'); timer_start_ns=int(meta_get(db,'timer_first_scheduled_tick_ns') or 0); source_ready=meta_get(db,'source_cursor_caught_up')=='1'; timer_thread=None; frontier=MaterializationFrontier(); batch_id=int(meta_get(db,'ingestion_batch_id') or 0)
 next_tick_ns=int(saved_next or 0)
 if source_ready:
  if not timer_start_ns or not next_tick_ns: raise RuntimeError('TIMER_METADATA_INCOMPLETE')
  timer_thread=threading.Thread(target=scheduled_snapshot_loop,args=(a.root,model,health,boundary,timer_start_ns,next_tick_ns,frontier),daemon=True,name='v2-scheduled-snapshots')
  timer_thread.start()
 try:
  while True:
   h=json.loads(health.read_text())
   if source_continuity_changed(h,boundary): raise RuntimeError('SOURCE_CONTINUITY_UNCERTAIN')
   parts=source_parts(a.root.parent); keys=[x[0] for x in parts]
   saved_key=meta_get(db,'source_partition')
   if current_key is None:
    current_key=saved_key if saved_key in keys else (keys[-1] if keys else None)
    offset=int(meta_get(db,'source_offset') or 0) if current_key==saved_key else 0
   selected=dict(parts).get(current_key)
   # Rotation renames the active .partial path to .zst.  If we already hold
   # the old file open, its inode remains the authoritative, immutable tail.
   # Drain that handle before advancing rather than decompressing the entire
   # newly-created zst file to recover a byte offset during the 1s timer.
   rotated_open_tail = bool(
    fd and current_path and current_path.suffix == '.partial' and
    selected and selected != current_path and selected.suffix == '.zst'
   )
   if selected and selected!=current_path and not rotated_open_tail:
    if fd: fd.close()
    if decompressor: decompressor.wait(timeout=30)
    fd,decompressor,seekable=open_source(selected,offset); current_path=selected
   batch=[]; ticks=set(); source_caught_up=True; batch_watermark_ns=0; batch_offset_start=offset; read_started=time.perf_counter_ns()
   try:
    if fd:
     exhausted=True
     for meta,raw,offset in frames(fd,offset,seekable):
      if int(meta.get('receivedAtUnixNs',0))>=boundary_ns and meta.get('kind')=='transaction' and meta.get('rawFormat') in ('yellowstone_subscribe_update_protobuf_full_meta','yellowstone_subscribe_update_protobuf_full_meta_v3_closed') and 'V3_PROTOCOL_V2' in meta.get('filters',[]): batch.append((meta,raw))
      if len(batch)>=MAX_NORMALIZER_BATCH:
       source_caught_up=False
       break
    read_finished=time.perf_counter_ns()
    if batch:
     batch_id+=1; first_receipt_ns=min(int(meta['receivedAtUnixNs']) for meta,_ in batch); batch_watermark_ns=max(int(meta['receivedAtUnixNs']) for meta,_ in batch)
     frontier.begin_batch(batch_id,first_receipt_ns,batch_watermark_ns); frontier.read_complete(batch_watermark_ns)
     normalize_started=time.perf_counter_ns()
     normalized=normalize(a.normalizer,batch)
     last_normalize_ns=time.perf_counter_ns()-normalize_started
     normalize_finished=time.perf_counter_ns(); frontier.normalized(batch_watermark_ns)
     apply_started=time.perf_counter_ns()
     with writer_lease(a.root):
      apply_rows(db,model,normalized,ticks,certified_from_ns,commit=False)
      materialize_finished=time.perf_counter_ns(); published_ns=time.time_ns()
      db.execute('INSERT INTO ingestion_batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(batch_id,batch_offset_start,offset,len(batch),first_receipt_ns,batch_watermark_ns,read_started,read_finished,normalize_started,normalize_finished,apply_started,materialize_finished,published_ns,batch_watermark_ns,batch_watermark_ns,batch_watermark_ns,batch_watermark_ns,0,'MATERIALIZED',None,now()))
      meta_set(db,'ingestion_batch_id',batch_id); db.commit()
     frontier.materialized(batch_watermark_ns)
     last_apply_ns=time.perf_counter_ns()-apply_started; last_batch_count=len(batch)
    elif source_caught_up:
     # No unread record remains at the current source tail. This means the
     # materialized state is complete through the present instant, without
     # treating raw filesystem mtimes as scientific evidence.
     frontier.materialized(time.time_ns())
   finally:
    if batch and frontier.state()['inflight']: frontier.fail_batch()
   # Do not start the cadence clock while a new root is still scanning a
   # pre-boundary source tail. Once caught up, every later second is strict.
   if not source_ready and source_caught_up:
    timer_start_ns=max(configured_timer_start_ns,((time.time_ns()+TICK_NS-1)//TICK_NS)*TICK_NS)
    next_tick_ns=timer_start_ns; source_ready=True
    with writer_lease(a.root): meta_set(db,'source_cursor_caught_up','1'); meta_set(db,'timer_first_scheduled_tick_ns',timer_start_ns); meta_set(db,'next_scheduled_tick_ns',next_tick_ns); db.commit()
    timer_thread=threading.Thread(target=scheduled_snapshot_loop,args=(a.root,model,health,boundary,timer_start_ns,next_tick_ns,frontier),daemon=True,name='v2-scheduled-snapshots')
    timer_thread.start()
   if current_key:
    with writer_lease(a.root):
     meta_set(db,'source_partition',current_key); meta_set(db,'source_offset',offset); db.commit()
   cadence=db.execute('select max(emitted_ns-tick_ns),max(tick_ns) from cadence_ticks').fetchone(); persisted_next=int(meta_get(db,'next_scheduled_tick_ns') or 0); frontier_state=frontier.state()
   write_json(a.root/'health.json',{'schema':'2CQ_AUTONOMOUS_WHEN_V2_RANK_SNAPSHOT_HEALTH','updatedAtUtc':now(),'sourcePath':str(current_path) if current_path else None,'sourcePartition':current_key,'cursorOffset':offset,'sourceFramesSeen':int(db.execute('select count(*) from events').fetchone()[0]),'ticks':int(db.execute('select count(*) from decision_ticks').fetchone()[0]),'candidateRanks':int(db.execute('select count(*) from decision_candidates').fetchone()[0]),'teacherLabelsFinal':int(db.execute('select count(*) from teacher_labels where finality="FINAL"').fetchone()[0]),'teacherLabelsPending':int(db.execute('select count(*) from teacher_labels where finality="PENDING"').fetchone()[0]),'decoderRejections':int(db.execute('select count(*) from decoder_rejections').fetchone()[0]),'taintedMints':int(db.execute('select count(*) from tainted_mints').fetchone()[0]),'pendingSlots':int(db.execute('select count(*) from slot_queue where finality="PENDING"').fetchone()[0]),'sourceEpoch':h['currentEpoch'],'continuityGaps':h['continuityGaps'],'timerStartNs':timer_start_ns,'nextScheduledTickNs':persisted_next,'maxTimerDriftNs':cadence[0] or 0,'lastScheduledTickNs':cadence[1],'cadenceToleranceNs':CADENCE_TOLERANCE_NS,'lastNormalizerBatchCount':last_batch_count,'lastNormalizerNs':last_normalize_ns,'lastApplyNs':last_apply_ns,'ingestionInflight':frontier_state['inflight'],'sourceWatermarkNs':frontier_state['sourceWatermarkNs'],'readWatermarkNs':frontier_state['readWatermarkNs'],'normalizedWatermarkNs':frontier_state['normalizedWatermarkNs'],'materializedWatermarkNs':frontier_state['materializedWatermarkNs'],'ingestionBatchId':frontier_state['batchId'],'outcomeBlind':True})
   if rotated_open_tail and exhausted:
    idx=keys.index(current_key) if current_key in keys else -1
    if idx+1<len(keys):
     fd.close(); fd=None
     current_key=keys[idx+1]; current_path=None; offset=0
   elif exhausted and current_path and current_path.suffix=='.zst':
    idx=keys.index(current_key) if current_key in keys else -1
    if idx+1<len(keys):
     fd.close(); fd=None
     if decompressor:
      if decompressor.wait(timeout=30)!=0: raise RuntimeError('ZSTD_SOURCE_DECOMPRESS_FAILED')
      decompressor=None
     current_key=keys[idx+1]; current_path=None; offset=0
   time.sleep(.02 if not source_caught_up else .1)
 finally:
  if fd: fd.close()
  if decompressor: decompressor.wait(timeout=30)
  db.close()
if __name__=='__main__':
 try:
  main()
 except BaseException as exc:
  receipt=os.environ.get('V3_ADAPTER_EXIT_RECEIPT')
  if receipt:
   pathlib.Path(receipt).write_text(json.dumps({'atUtc':now(),'kind':type(exc).__name__,'repr':repr(exc),'traceback':traceback.format_exc()},sort_keys=True,indent=2)+'\n')
  raise
 else:
  receipt=os.environ.get('V3_ADAPTER_EXIT_RECEIPT')
  if receipt: pathlib.Path(receipt).write_text(json.dumps({'atUtc':now(),'kind':'NORMAL_RETURN'},sort_keys=True,indent=2)+'\n')
