"""Bounded raw-context canary with off-path, explicitly timed RPC bootstrap.

Snapshots do not certify a stream watermark, finality, or past availability.
No source service or strategy is changed. No executor or signing capability.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from recover_receipts_v1 import read_endpoint, rpc

CLOCK='SysvarC1ock11111111111111111111111111111111'
GLOBAL='4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf'


def snapshot_row(addresses, began, ended, response, error):
    return {'schema':'OFFPATH_CONTEXT_SNAPSHOT_V1','addresses':addresses,'request_at_ns':began,
        'response_available_at_ns':ended,'commitment_requested':'processed','raw_rpc_result':response,'error':error,
        'original_decision_availability':'NO_BACKDATING; only available at response time',
        'continuity_certified':False,'chain_execution_timestamp':None}


def run(binary, env_file, output, seconds, fee, synchronized=None):
    if not 1<=seconds<=300: raise ValueError('bounded context canary duration 1..300')
    if shutil.disk_usage(output.parent).free < 16*1024**3: raise ValueError('insufficient bounded-capture headroom')
    output.mkdir(parents=True,exist_ok=False)
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR'}}
    for name in ('SHYFT_GRPC_URL','SHYFT_GRPC_X_TOKEN'):env[name]=read_endpoint(env_file,name)
    endpoint=read_endpoint(env_file,'SECURE_RPC_URL')
    capture=output/'capture.jsonl'
    env.update(PUMP_EVIDENCE_OUTPUT=str(capture),PUMP_EVIDENCE_SECONDS=str(seconds),PUMP_CONTEXT_FEE_ACCOUNT=fee)
    started=time.time_ns(); counts=Counter();seen=set();pending=[];last_global=0;remainder=b'';position=0
    scheduler=None;last_checkpoint=0;requests=None;checkpoint_cursor=0
    if synchronized:
        from synchronized_context_v1 import Scheduler
        if seconds < synchronized['signal_seconds']+25:raise ValueError('at least25s outcome drain required')
        scheduler=Scheduler(synchronized['population'],synchronized['signal_seconds'],synchronized['web3_module'],
                            pre_signal=synchronized.get('pre_signal',False))
    manifest={'schema':'BOUNDED_RAW_QUOTE_CONTEXT_RUN_V1','started_at_ns':started,'duration_seconds':seconds,
        'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'binary_path':str(binary),
        'mode':'receive_only','fee_account_candidate':fee,'global_account_candidate':GLOBAL,
        'bootstrap':'all discovered Pump-scope pre/post balance mints, not signal or wallet selected',
        'limitations':['RPC bootstrap has later availability than native mint discovery.',
            'Subscription requests have no server activation acknowledgment; do not infer continuous coverage.',
            'Candidate fee/global identities and raw layouts require independent validation before quotes.']}
    if synchronized:
        manifest['synchronized_context']={k:str(v) if isinstance(v,Path) else v for k,v in synchronized.items()}
        manifest['synchronized_context'].update(checkpoint_period_ms=250,
            timing='Boundary inputs require response available <= frozen deadline; no backdating.',
            meaning='Single-bank read sets; NOT write continuity, fork finality, fills or net PnL.')
        code_root=Path(__file__).parent
        manifest['research_code_sha256']={name:hashlib.sha256((code_root/name).read_bytes()).hexdigest() for name in (
            'run_context_capture_v1.py','synchronized_context_v1.py','burst_persistence_v1.py',
            'transaction_evidence_v1.py','burst-persistence-contract-v1.json','synchronized-canary-contract-v2.json')}
        if synchronized.get('parallel_context'):
            manifest['research_code_sha256']['context_requests_v1.py']=hashlib.sha256((code_root/'context_requests_v1.py').read_bytes()).hexdigest()
            manifest['research_code_sha256']['synchronized-canary-contract-v3.json']=hashlib.sha256((code_root/'synchronized-canary-contract-v3.json').read_bytes()).hexdigest()
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    with (output/'stderr.log').open('x') as err, (output/'bootstrap.jsonl').open('x') as sink, (output/'scheduler.jsonl').open('x') as schedule_sink:
        proc=subprocess.Popen([str(binary)],env=env,stdout=subprocess.DEVNULL,stderr=err)
        def retrieve(addresses, plan=None):
            began=time.time_ns()
            config={'encoding':'base64','commitment':'processed'}
            if plan:config['minContextSlot']=plan['minimum_native_slot']
            try:result,error=rpc(endpoint,'getMultipleAccounts',[addresses,config])
            except Exception as exc:result,error=None,type(exc).__name__
            ended=time.time_ns();row=snapshot_row(addresses,began,ended,result,error)
            if plan:
                row.update(purpose='SYNCHRONIZED_SIGNAL_CONTROL_READ_SET',request_plan=plan)
            return row
        def persist(row):
            if row.get('purpose')=='SYNCHRONIZED_SIGNAL_CONTROL_READ_SET':
                counts['synchronized_requests']+=1
            sink.write(json.dumps(row,sort_keys=True)+'\n');sink.flush()
            counts['rpc_requests']+=1;counts['rpc_errors']+=bool(row.get('error'))
            values=(row.get('raw_rpc_result') or {}).get('value') or []
            counts['returned_accounts']+=sum(v is not None for v in values)
            counts['missing_accounts']+=sum(v is None for v in values)
        def fetch(addresses,plan=None):
            if requests:return requests.submit('checkpoint' if plan else 'bootstrap',addresses,plan)
            persist(retrieve(addresses,plan));return True
        if synchronized and synchronized.get('parallel_context'):
            from context_requests_v1 import ContextRequests
            requests=ContextRequests(retrieve)
        try:
            while True:
                if requests:
                    for row in requests.poll():persist(row)
                if time.time_ns()-started > (seconds+45)*1_000_000_000:
                    proc.terminate();counts['safety_timeout']+=1;break
                if capture.exists():
                    with capture.open('rb') as incoming:
                        incoming.seek(position);chunk=incoming.read(16*1024*1024);position=incoming.tell()
                    parts=(remainder+chunk).split(b'\n');remainder=parts.pop()
                    for raw in parts:
                        row=json.loads(raw);counts['capture_rows_read']+=1
                        if scheduler:
                            for seed in scheduler.consume(row,counts['capture_rows_read'],time.time_ns()):
                                schedule_sink.write(json.dumps(seed,sort_keys=True)+'\n');schedule_sink.flush()
                        if row.get('kind')=='MINT_DISCOVERED' and row['mint'] not in seen:
                            seen.add(row['mint']);pending.append(row['mint'])
                if scheduler and proc.poll() is None and time.monotonic()-last_checkpoint>=0.25:
                    if not requests or requests.available('checkpoint'):
                        plans=scheduler.request_plan(time.time_ns())
                        if requests and plans:
                            # Fixed cyclic batch scheduling, never volume/outcome
                            # ranking. No mint silently dropped at the100-account cap.
                            plan=plans[checkpoint_cursor%len(plans)]
                            if fetch(plan['addresses'],plan):checkpoint_cursor+=1
                        else:
                            for plan in plans:fetch(plan['addresses'],plan)
                        last_checkpoint=time.monotonic()
                if time.monotonic()-last_global>=5 and proc.poll() is None:
                    if fetch([fee,GLOBAL,CLOCK]):last_global=time.monotonic()
                if pending and (not requests or requests.available('bootstrap')):
                    batch=pending[:100]
                    if fetch(batch):del pending[:100]
                if proc.poll() is not None and (not capture.exists() or position>=capture.stat().st_size) and not pending and (not requests or not requests.pending):break
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            if requests:
                for row in requests.finish():persist(row)
        code=proc.wait(timeout=5)
    result={'exit_code':code,'finished_at_ns':time.time_ns(),'counts':dict(counts),'unique_discovered_mints':len(seen),
        'unparsed_tail_bytes':len(remainder),'unfetched_mints':len(pending),'trading_enabled':False}
    if scheduler:
        result['synchronized']={'seeds':len(scheduler.seed_logs),
            'signal_period_seeds':sum(s['eligible_signal_period'] for s in scheduler.seed_logs),
            'disabled_by_gap_or_scope_limit':scheduler.disabled,'gaps':scheduler.gaps}
    (output/'completion.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('binary','env-file','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=120);p.add_argument('--fee-account',required=True)
    p.add_argument('--signal-seconds',type=int);p.add_argument('--population');p.add_argument('--web3-module',type=Path)
    p.add_argument('--pre-signal-context',action='store_true')
    p.add_argument('--parallel-context',action='store_true')
    a=p.parse_args();sync=None
    if a.signal_seconds is not None:
        if not a.population or not a.web3_module or a.signal_seconds<=0:p.error('synchronized capture requires population, web3-module and positive signal-seconds')
        sync={'signal_seconds':a.signal_seconds,'population':a.population,'web3_module':a.web3_module,
              'pre_signal':a.pre_signal_context}
        sync['parallel_context']=a.parallel_context
    print(json.dumps(run(a.binary,a.env_file,a.output,a.seconds,a.fee_account,sync)))
