"""Bounded later finalized-block recovery, never original decision evidence."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time
from quote_context_v1 import jsonl,sha
from recover_receipts_v1 import read_endpoint,rpc


def select(cases):
    by_variant=defaultdict(list)
    for c in cases:
        # Select before finalized checks, without looking at agreement/difference,
        # returns, or token performance. Same-slot bracket limits query cost.
        if 'predicted' in c and c['pre_source']['slot']==c['slot']:
            by_variant[c['variant']].append(c)
    return [c for v,rows in sorted(by_variant.items()) for c in sorted(rows,key=lambda r:hashlib.sha256(r['signature'].encode()).hexdigest())[:3]]


def run(cases_path,env,output):
    output.mkdir(parents=True,exist_ok=False)
    selected=select(list(jsonl(cases_path)));slots=sorted({c['slot'] for c in selected})
    manifest={'schema':'LATER_QUOTE_FIXTURE_REFERENCE_V1','selection':'up to3 signature-hash-ranked independently decodable same-slot brackets per variant; no arithmetic agreement filter',
        'source_sha256':sha(cases_path),'selected_signatures':[c['signature'] for c in selected],'slots':slots,
        'later_reconstruction_only':True,'original_decision_availability':False}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    endpoint=read_endpoint(env,'SECURE_RPC_URL')
    with (output/'finalized-reference.jsonl').open('x') as out:
        for slot in slots:
            began=time.time_ns()
            try:result,error=rpc(endpoint,'getBlock',[slot,{'encoding':'json','transactionDetails':'full','rewards':False,'commitment':'finalized','maxSupportedTransactionVersion':1}])
            except Exception as e:result,error=None,type(e).__name__
            row={'slot':slot,'request_at_ns':began,'reference_available_at_ns':time.time_ns(),'raw_block':result,'error':error}
            out.write(json.dumps(row,separators=(',',':'))+'\n');out.flush()
            print(json.dumps({'slot':slot,'recovered':result is not None,'error':error}),flush=True)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('cases','env','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();run(a.cases,a.env,a.output)
