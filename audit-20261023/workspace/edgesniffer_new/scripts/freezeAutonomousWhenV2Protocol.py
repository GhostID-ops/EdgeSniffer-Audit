#!/usr/bin/env python3
import datetime as dt, hashlib, json, os, pathlib, shutil, sys

base=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2]); protocol=pathlib.Path(sys.argv[3])
root=base/'autonomous-when-v2'; health=json.loads((base/'health.json').read_text())
if health.get('connectionState')!='LIVE': raise RuntimeError('SOURCE_NOT_LIVE')
if root.exists(): raise RuntimeError('V2_ROOT_EXISTS')
root.mkdir(); (root/'source').mkdir(); (root/'protocol-v1').mkdir()
for p in (protocol, source/'twoCqAutonomousWhenV2.py', source/'twoCqAutonomousWhenFinalizerV2.py'):
    target=(root/'protocol-v1'/ 'AUTONOMOUS_WHEN_V2_PRE_REGISTERED_PROTOCOL.json') if p==protocol else root/'source'/p.name
    shutil.copyfile(p,target); os.chmod(target,0o444)
def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
boundary={'schema':'AUTONOMOUS_WHEN_V2_PROSPECTIVE_DEVELOPMENT_BOUNDARY_V1','boundaryUtc':dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z'),'sourceContinuityBaseline':{'sourceEpoch':int(health['currentEpoch']),'reconnects':int(health['reconnects']),'continuityGaps':int(health['continuityGaps'])},'sourceLastDurableFrameAtUtc':health['lastDurableFrameAtUtc'],'sourceLastDurableSlot':health['lastDurableSlot'],'protocolSha256':sha(root/'protocol-v1/AUTONOMOUS_WHEN_V2_PRE_REGISTERED_PROTOCOL.json'),'frozenWhichSha256':'7606a3a138591516f503e7f7b56501f1828b92947a2b38707bff166fbb233b64','laneSourceSha256':sha(root/'source/twoCqAutonomousWhenV2.py'),'finalizerSourceSha256':sha(root/'source/twoCqAutonomousWhenFinalizerV2.py'),'freshOnly':True,'oldQuarantineUsed':False,'outcomesRead':0,'pnlRead':0,'v2FitPerformed':False}
receipt=root/'prospective-start-receipt.json'; receipt.write_text(json.dumps(boundary,sort_keys=True,indent=2)+'\n'); os.chmod(receipt,0o444)
freeze={'schema':'AUTONOMOUS_WHEN_V2_PROTOCOL_FREEZE_RECEIPT_V1','createdAtUtc':boundary['boundaryUtc'],'protocolSha256':boundary['protocolSha256'],'boundary':{'path':str(receipt),'sha256':sha(receipt)},'attestation':{'v1Modified':False,'v2FitPerformed':False,'quarantineUsed':False,'outcomesRead':0,'pnlRead':0}}
p=root/'protocol-v1/AUTONOMOUS_WHEN_V2_PROTOCOL_FREEZE_RECEIPT.json'; p.write_text(json.dumps(freeze,sort_keys=True,indent=2)+'\n'); os.chmod(p,0o444)
print(json.dumps({'boundary':boundary,'receiptSha256':sha(receipt),'protocolSha256':boundary['protocolSha256'],'freezeReceiptSha256':sha(p)},sort_keys=True))
