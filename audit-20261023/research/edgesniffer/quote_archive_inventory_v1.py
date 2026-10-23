"""Inspect sealed historical ledger partitions for retained raw account evidence."""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
from recover_receipts_v1 import frames
from quote_context_v1 import sha


def summarize_snapshot(metadata, raw, selected):
    mint=metadata.get('accountPubkey'); payload=json.loads(raw)
    result=payload.get('result') or {}; value=result.get('value') or {}
    data=value.get('data')
    decoded=base64.b64decode(data[0],validate=True) if isinstance(data,list) and len(data)>1 and data[1]=='base64' else None
    return {'account':mint,'selected_mint':mint in selected,'owner':value.get('owner'),
        'received_at_ns':metadata.get('receivedAtUnixNs'),'decode_at_ns':metadata.get('decodeAtUnixNs'),
        'rpc_context_slot':(result.get('context') or {}).get('slot'),
        'raw_account_bytes':len(decoded) if decoded is not None else None,
        'raw_account_sha256':hashlib.sha256(decoded).hexdigest() if decoded is not None else None,
        'raw_rpc_sha256':hashlib.sha256(raw).hexdigest(),
        'availability_note':'Retained RPC response receipt/decode time, not earlier chain slot or native transaction observation',
        'continuity_certified':False}


def run(archives,canonical,output):
    output.mkdir(parents=True,exist_ok=False)
    selected=set(json.loads((canonical/'report.json').read_text())['canonical_derivation']['mappings'])
    reports=[]; snapshots=[]
    with (output/'raw-snapshots.jsonl').open('x') as out:
        for archive in archives:
            manifest=json.loads(archive.with_name('manifest.json').read_text())
            h=sha(archive)
            if h!=manifest['compressedSha256']:raise ValueError('sealed archive changed')
            counts=Counter(); first=last=None
            for index,(metadata,raw) in enumerate(frames(archive),1):
                counts[str(metadata.get('kind'))]+=1
                t=metadata.get('receivedAtUnixNs')
                if type(t) is int:first=t if first is None else min(first,t);last=t if last is None else max(last,t)
                if metadata.get('kind')=='rpc_account_snapshot':
                    row=summarize_snapshot(metadata,raw,selected)
                    row.update(archive=str(archive),frame_index=index)
                    snapshots.append(row)
                    out.write(json.dumps(dict(row,original_metadata=metadata,raw_rpc_base64=base64.b64encode(raw).decode()),sort_keys=True)+'\n')
            reports.append({'archive':str(archive),'sha256':h,'counts':dict(counts),'first_receipt_ns':first,'last_receipt_ns':last,
                'manifest_exact_replay_complete':manifest['exactReplayComplete'],'manifest_full_hour':manifest['completeUtcHour']})
    report={'schema':'QUOTE_CONTEXT_RETAINED_ARCHIVE_INVENTORY_V1','archives':reports,'snapshots':snapshots,
        'selected_mint_snapshots':sum(x['selected_mint'] for x in snapshots),
        'interpretation':'Raw account recovery inventory, not continuous state/fee/mint coverage. Non-snapshot formats counted, not exhaustively re-decoded.',
        'raw_snapshots_sha256':sha(output/'raw-snapshots.jsonl'),'implementation_sha256':sha(Path(__file__))}
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,action='append',required=True)
    p.add_argument('--canonical',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=run(a.archive,a.canonical,a.output);print(json.dumps(r,indent=2))
