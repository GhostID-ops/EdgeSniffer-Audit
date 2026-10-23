"""Start only the isolated receive-only collector, without exposing credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from recover_receipts_v1 import read_endpoint


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--binary', type=Path, required=True);p.add_argument('--env-file',type=Path,required=True)
    p.add_argument('--output-dir', type=Path, required=True);p.add_argument('--seconds',type=int,default=120)
    a=p.parse_args()
    if not 1 <= a.seconds <= 3600:p.error('duration must be 1..3600 seconds')
    a.output_dir.mkdir(parents=True,exist_ok=False)
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR'}}
    for name in ('SHYFT_GRPC_URL','SHYFT_GRPC_X_TOKEN'):
        env[name]=read_endpoint(a.env_file,name)
    env.update(PUMP_EVIDENCE_OUTPUT=str(a.output_dir/'capture.jsonl'),PUMP_EVIDENCE_SECONDS=str(a.seconds))
    manifest={'schema':'PUMP_CAPTURE_RUN_V1','started_at_ns':time.time_ns(),'duration_seconds':a.seconds,
              'binary_path':str(a.binary),'binary_sha256':hashlib.sha256(a.binary.read_bytes()).hexdigest(),
              'mode':'receive_only','endpoint_credentials_retained':False}
    (a.output_dir/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    with (a.output_dir/'stderr.log').open('x') as err:
        completed=subprocess.run([str(a.binary)],env=env,stdout=subprocess.DEVNULL,stderr=err,timeout=a.seconds+45)
    (a.output_dir/'completion.json').write_text(json.dumps({'exit_code':completed.returncode,'finished_at_ns':time.time_ns()})+'\n')
    print(json.dumps({'output':str(a.output_dir),'exit_code':completed.returncode}))
    raise SystemExit(completed.returncode)
