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


def run(binary, env_file, output, seconds, fee):
    if not 1<=seconds<=300: raise ValueError('bounded context canary duration 1..300')
    if shutil.disk_usage(output.parent).free < 16*1024**3: raise ValueError('insufficient bounded-capture headroom')
    output.mkdir(parents=True,exist_ok=False)
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR'}}
    for name in ('SHYFT_GRPC_URL','SHYFT_GRPC_X_TOKEN'):env[name]=read_endpoint(env_file,name)
    endpoint=read_endpoint(env_file,'SECURE_RPC_URL')
    capture=output/'capture.jsonl'
    env.update(PUMP_EVIDENCE_OUTPUT=str(capture),PUMP_EVIDENCE_SECONDS=str(seconds),PUMP_CONTEXT_FEE_ACCOUNT=fee)
    started=time.time_ns(); counts=Counter();seen=set();pending=[];last_global=0;remainder=b'';position=0
    manifest={'schema':'BOUNDED_RAW_QUOTE_CONTEXT_RUN_V1','started_at_ns':started,'duration_seconds':seconds,
        'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'binary_path':str(binary),
        'mode':'receive_only','fee_account_candidate':fee,'global_account_candidate':GLOBAL,
        'bootstrap':'all discovered Pump-scope pre/post balance mints, not signal or wallet selected',
        'limitations':['RPC bootstrap has later availability than native mint discovery.',
            'Subscription requests have no server activation acknowledgment; do not infer continuous coverage.',
            'Candidate fee/global identities and raw layouts require independent validation before quotes.']}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    with (output/'stderr.log').open('x') as err, (output/'bootstrap.jsonl').open('x') as sink:
        proc=subprocess.Popen([str(binary)],env=env,stdout=subprocess.DEVNULL,stderr=err)
        def fetch(addresses):
            began=time.time_ns()
            try:result,error=rpc(endpoint,'getMultipleAccounts',[addresses,{'encoding':'base64','commitment':'processed'}])
            except Exception as exc:result,error=None,type(exc).__name__
            ended=time.time_ns();sink.write(json.dumps(snapshot_row(addresses,began,ended,result,error),sort_keys=True)+'\n');sink.flush()
            counts['rpc_requests']+=1;counts['rpc_errors']+=bool(error)
            values=(result or {}).get('value') or []
            counts['returned_accounts']+=sum(v is not None for v in values)
            counts['missing_accounts']+=sum(v is None for v in values)
        try:
            while True:
                if time.time_ns()-started > (seconds+45)*1_000_000_000:
                    proc.terminate();counts['safety_timeout']+=1;break
                if capture.exists():
                    with capture.open('rb') as incoming:
                        incoming.seek(position);chunk=incoming.read(16*1024*1024);position=incoming.tell()
                    parts=(remainder+chunk).split(b'\n');remainder=parts.pop()
                    for raw in parts:
                        row=json.loads(raw);counts['capture_rows_read']+=1
                        if row.get('kind')=='MINT_DISCOVERED' and row['mint'] not in seen:
                            seen.add(row['mint']);pending.append(row['mint'])
                if time.monotonic()-last_global>=5 and proc.poll() is None:
                    fetch([fee,GLOBAL,CLOCK]);last_global=time.monotonic()
                if pending:
                    batch=pending[:100];del pending[:100];fetch(batch)
                if proc.poll() is not None and (not capture.exists() or position>=capture.stat().st_size) and not pending:break
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        code=proc.wait(timeout=5)
    result={'exit_code':code,'finished_at_ns':time.time_ns(),'counts':dict(counts),'unique_discovered_mints':len(seen),
        'unparsed_tail_bytes':len(remainder),'unfetched_mints':len(pending),'trading_enabled':False}
    (output/'completion.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('binary','env-file','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=120);p.add_argument('--fee-account',required=True)
    a=p.parse_args();print(json.dumps(run(a.binary,a.env_file,a.output,a.seconds,a.fee_account)))
