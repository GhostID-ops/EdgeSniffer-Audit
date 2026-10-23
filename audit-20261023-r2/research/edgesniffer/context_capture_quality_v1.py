"""Context-capture capability audit, deliberately not an economics admission gate."""
import argparse
import base64
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import statistics
from quote_context_v1 import sha,jsonl
from run_context_capture_v1 import CLOCK,GLOBAL


def checked_account(account):
    data=base64.b64decode(account['data_base64'],validate=True)
    if hashlib.sha256(data).hexdigest()!=account['data_sha256']:raise ValueError('account data hash mismatch')
    for key in ('pubkey','owner'):
        if not isinstance(account.get(key),str) or not account[key]:raise ValueError('missing account identity')
    for key in ('slot','write_version'):
        if type(account.get(key)) is not int or account[key]<0:raise ValueError('invalid account order')
    return len(data)


def run(source,output):
    output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((source/'manifest.json').read_text());completion=json.loads((source/'completion.json').read_text())
    fee=manifest['fee_account_candidate'];counts=Counter();owners=Counter();discovered={};writes=defaultdict(list)
    sequence=0;last_clock=None;connections=set()
    for row in jsonl(source/'capture.jsonl'):
        kind=row.get('kind');counts[kind]+=1
        if kind=='MINT_DISCOVERED':discovered.setdefault(row['mint'],row['available_at_ns'])
        if kind=='CONNECTED':connections.add(row['connection_id'])
        if 'sequence' in row:
            if row['sequence']!=sequence+1:counts['SEQUENCE_ERRORS']+=1
            sequence=row['sequence'];at=row['received_at_unix_ns']
            if last_clock is not None and at<last_clock:counts['CLOCK_REGRESSIONS']+=1
            last_clock=at
            if row['normalized_at_unix_ns']<at:counts['DECODE_BEFORE_RECEIVE']+=1
            raw=base64.b64decode(row['raw_protobuf_base64'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=row['raw_sha256']:raise ValueError('raw protobuf hash mismatch')
        if kind=='ACCOUNT':
            a=row['account'];checked_account(a);owners[a['owner']]+=1
            writes[a['pubkey']].append(row['normalized_at_unix_ns'])
    bootstrap=Counter();availability=defaultdict(list);latencies=[]
    for row in jsonl(source/'bootstrap.jsonl'):
        if row['response_available_at_ns']<row['request_at_ns']:raise ValueError('invalid RPC availability')
        if row.get('error'):bootstrap['error_responses']+=1;continue
        result=row.get('raw_rpc_result') or {};values=result.get('value')
        if not isinstance(values,list) or len(values)!=len(row['addresses']):bootstrap['invalid_response_shape']+=1;continue
        for address,value in zip(row['addresses'],values):
            if value is None:bootstrap['missing_accounts']+=1;continue
            data=value.get('data')
            if not isinstance(data,list) or len(data)!=2 or data[1]!='base64' or not value.get('owner'):
                bootstrap['unsupported_account_encoding']+=1;continue
            base64.b64decode(data[0],validate=True)
            at=row['response_available_at_ns'];availability[address].append(at)
            role='fee' if address==fee else 'global' if address==GLOBAL else 'clock' if address==CLOCK else 'mint'
            bootstrap[role+'_raw_accounts']+=1
    for mint,at in discovered.items():
        if availability[mint]:latencies.append((min(availability[mint])-at)/1e9)
    result={'schema':'RAW_QUOTE_CONTEXT_CANARY_QUALITY_V1','capture_counts':dict(counts),'account_owners':dict(owners),
        'connections':len(connections),'bootstrap':dict(bootstrap),'discovered_mints':len(discovered),
        'mints_with_raw_bootstrap':sum(bool(availability[m]) for m in discovered),
        'mints_without_raw_bootstrap':sorted(m for m in discovered if not availability[m]),
        'discovered_mints_with_native_writes':sum(bool(writes[m]) for m in discovered),
        'bootstrap_delay_from_native_discovery_seconds':{'n':len(latencies),'median':statistics.median(latencies) if latencies else None,
            'max':max(latencies) if latencies else None,'within_1s':sum(0<=x<=1 for x in latencies)},
        'completion':completion,'inputs':{n:sha(source/n) for n in ('capture.jsonl','bootstrap.jsonl','manifest.json','completion.json')},
        'implementation_sha256':sha(Path(__file__)),'quote_certification':False,
        'limitations':['No native subscription activation ack, full write/fork reconciliation or layout/fee/mint validation yet.',
            'No account update is not evidence of no write. Bootstrap is not usable before response.',
            'First native receipt and chain state/confirmation are different clocks.',
            'This separate engineering canary neither changes the original78 signals nor validates a strategy.']}
    (output/'report.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(run(a.source,a.output),indent=2))
