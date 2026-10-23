#!/usr/bin/env python3
"""Read-only operational monitor for the independent AUTONOMOUS_WHEN_V2 lane."""
import datetime as dt, json, pathlib, sqlite3, subprocess, time

BASE=pathlib.Path('/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1')
ROOT=BASE/'autonomous-when-v2'; OUT=ROOT/'operational-monitor-v1'; STATE=OUT/'state.json'; HEALTH=OUT/'health.json'
def now(): return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')
def read(p): return json.loads(p.read_text())
def active(unit): return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
def restarts(unit): return int(subprocess.check_output(['systemctl','show',unit,'-p','NRestarts','--value'],text=True).strip() or 0)
def write(p,v):
 p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.tmp'); t.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n'); t.replace(p)
def main():
 state=read(STATE) if STATE.exists() else {'startedAtUtc':now(),'restartBaseline':restarts('edgesniffer-2cq-autonomous-when-v2.service'),'failures':[],'last':{}}
 while True:
  source=read(BASE/'health.json'); lane=read(ROOT/'health.json') if (ROOT/'health.json').exists() else {}; finalizer=read(ROOT/'finalizer-health.json') if (ROOT/'finalizer-health.json').exists() else {}
  c=sqlite3.connect(f'file:{ROOT/"autonomous-when-v2.sqlite3"}?mode=ro',uri=True)
  ticks,ranks,last_tick,rejects=c.execute('select (select count(*) from decision_ticks),(select count(*) from decision_candidates),(select max(tick_ns) from decision_ticks),(select count(*) from decoder_rejections)').fetchone()
  mismatch=c.execute('select count(*) from decision_ticks t where t.universe_n != (select count(*) from decision_candidates c where c.tick_ns=t.tick_ns)').fetchone()[0]; c.close()
  epoch_ns=int(dt.datetime.now(dt.timezone.utc).timestamp()*1e9); age=None if last_tick is None else (epoch_ns-int(last_tick))/1e9
  previous=state.get('last',{}); source_adv=int(source.get('framesReceived',0))>int(previous.get('frames',0)); tick_adv=int(ticks)>int(previous.get('ticks',0)); rank_adv=int(ranks)>int(previous.get('ranks',0))
  lane_ok=active('edgesniffer-2cq-autonomous-when-v2.service'); finalizer_ok=active('edgesniffer-2cq-autonomous-when-v2-finalizer.service'); restart_delta=restarts('edgesniffer-2cq-autonomous-when-v2.service')-int(state['restartBaseline'])
  reasons=[]
  if source_adv and (not tick_adv or not rank_adv) and age is not None and age>60: reasons.append('STALE_V2_RANK_CAPTURE_WHILE_SOURCE_ADVANCES')
  if mismatch: reasons.append('CARDINALITY_MISMATCH')
  if not lane_ok: reasons.append('V2_LANE_NOT_ACTIVE')
  if not finalizer_ok: reasons.append('V2_FINALIZER_NOT_ACTIVE')
  if int(source.get('currentEpoch',-1))!=14 or int(source.get('reconnects',-1))!=13 or int(source.get('continuityGaps',-1))!=11: reasons.append('SOURCE_CONTINUITY_BASELINE_CHANGED')
  if restart_delta: reasons.append('UNEXPECTED_V2_RESTART')
  state['failures']=[x for x in state['failures'] if epoch_ns-x<300_000_000_000]
  if not lane_ok: state['failures'].append(epoch_ns)
  blocked=len(state['failures'])>=3 or 'SOURCE_CONTINUITY_BASELINE_CHANGED' in reasons
  status='BLOCKED' if blocked else ('DEGRADED' if reasons else 'HEALTHY')
  state['last']={'frames':int(source.get('framesReceived',0)),'ticks':int(ticks),'ranks':int(ranks)}; write(STATE,state)
  write(HEALTH,{'schema':'AUTONOMOUS_WHEN_V2_OPERATIONAL_MONITOR_V1','updatedAtUtc':now(),'status':status,'reasons':reasons,'sourceFresh':source.get('connectionState')=='LIVE','ticks':ticks,'rankRows':ranks,'lastTickAgeSeconds':age,'decoderRejections':rejects,'cardinalityMismatches':mismatch,'laneActive':lane_ok,'finalizerActive':finalizer_ok,'pendingSlots':finalizer.get('pendingSlots'),'finalizedSlotFrontier':finalizer.get('finalizedSlotFrontier'),'restartsSinceMonitorStart':restart_delta,'startupGateElapsedSeconds':(epoch_ns-int(dt.datetime.fromisoformat(state['startedAtUtc'].replace('Z','+00:00')).timestamp()*1e9))/1e9,'gate5m':'PASS' if status=='HEALTHY' and (epoch_ns-int(dt.datetime.fromisoformat(state['startedAtUtc'].replace('Z','+00:00')).timestamp()*1e9))/1e9>=300 else 'PENDING','gate15m':'PASS' if status=='HEALTHY' and (epoch_ns-int(dt.datetime.fromisoformat(state['startedAtUtc'].replace('Z','+00:00')).timestamp()*1e9))/1e9>=900 else 'PENDING','gate30m':'PASS' if status=='HEALTHY' and (epoch_ns-int(dt.datetime.fromisoformat(state['startedAtUtc'].replace('Z','+00:00')).timestamp()*1e9))/1e9>=1800 else 'PENDING'})
  time.sleep(15)
if __name__=='__main__': main()
