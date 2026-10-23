#!/usr/bin/env python3
import datetime as dt, hashlib, json, os, pathlib, sys

base=pathlib.Path(sys.argv[1]); root=base/'autonomous-when-v2'
protocol=root/'protocol-v1/AUTONOMOUS_WHEN_V2_PRE_REGISTERED_PROTOCOL.json'; boundary=root/'prospective-start-receipt.json'; health=root/'health.json'
if not protocol.exists() or not boundary.exists(): raise RuntimeError('REQUIRED_V2_ARTIFACT_MISSING')
if list(root.glob('**/*model*')) or list(root.glob('**/*evaluation*')): raise RuntimeError('PREDICTIVE_ARTIFACT_PRESENT_FAIL_CLOSED')
def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
p=json.loads(protocol.read_text()); b=json.loads(boundary.read_text()); h=json.loads(health.read_text()) if health.exists() else {}
old={'cleanUninterruptedHoursAtLeast':36,'finalizedIndependentTeacherAcquisitionsAtLeast':1500,'reason':'Provides material temporal coverage beyond a short 24-hour probe while avoiding a new arbitrary performance gate.'}
if p['developmentStoppingRule'] != old: raise RuntimeError('ORIGINAL_STOPPING_RULE_MISMATCH')
out=root/'protocol-v1/AUTONOMOUS_WHEN_V2_DEVELOPMENT_STOPPING_RULE_AMENDMENT_V1.json'
if out.exists(): raise RuntimeError('AMENDMENT_ALREADY_EXISTS')
now=dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
payload={'schema':'AUTONOMOUS_WHEN_V2_DEVELOPMENT_STOPPING_RULE_AMENDMENT_V1','amendedAtUtc':now,'originalProtocol':{'path':str(protocol),'sha256':sha(protocol),'oldStoppingRule':old},'newStoppingRule':{'cleanUninterruptedHoursAtLeast':24,'finalizedIndependentTeacherAcquisitionsAtLeast':1000},'rationale':['Exploratory development only, not validation.','Complete causal full-rank state is captured prospectively.','One thousand independent acquisitions supports development discovery.','Twenty-four uninterrupted hours retains a full daily cycle.','Fresh untouched validation remains mandatory after a later model freeze.'],'attestation':{'predictiveResultsInspectedBeforeAmendment':False,'v2FitPerformed':False,'featureLabelRelationshipsInspected':False,'horizonResultsInspected':False,'whenWhichPredictiveResultsInspected':False,'validationStarted':False,'oldQuarantineUsed':False,'boundaryRemainsValid':True,'boundaryRestartRequired':False,'noOtherProtocolRuleChanged':True,'outcomesRead':0,'pnlRead':0},'boundary':{'path':str(boundary),'sha256':sha(boundary),'boundaryUtc':b['boundaryUtc']},'operationalCountersAtAmendment':{'ticks':h.get('ticks'),'teacherLabelsFinal':h.get('teacherLabelsFinal'),'teacherLabelsPending':h.get('teacherLabelsPending'),'updatedAtUtc':h.get('updatedAtUtc')}}
out.write_text(json.dumps(payload,sort_keys=True,indent=2)+chr(10)); os.chmod(out,0o444)
print(json.dumps({'path':str(out),'sha256':sha(out),'boundaryUtc':b['boundaryUtc'],'finalizedN':h.get('teacherLabelsFinal'),'ticks':h.get('ticks')},sort_keys=True))
