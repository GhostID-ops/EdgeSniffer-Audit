#!/usr/bin/env python3
"""Regression coverage for manifest-bound replacement holdout startup."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; WRAPPER=ROOT/'tools/kefi_replacement_holdout_launch_wrapper_v1.py'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p, obj): p.write_text(json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n'); return p
def fixture(base: Path):
    cohort=base/'v4'; cohort.mkdir(); boundary=put(cohort/'boundary.json',{'status':'OPEN','target_valid_first_observed_selections':20})
    ledger=put(base/'ledger.jsonl',{'x':1}); ledger_manifest=put(base/'ledger-manifest.json',{'ledger_path':str(ledger),'ledger_sha256':sha(ledger)})
    tail=put(base/'tail.json',{}); health=put(base/'health.json',{'sourceInstanceId':'source','currentEpoch':1,'reconnects':0,'receiveErrors':0,'continuityGaps':0})
    binary=base/'observer'; binary.write_text('observer'); finalizer=base/'finalizer'; finalizer.write_text('finalizer')
    m={'cohort_root':str(cohort),'boundary_path':str(boundary),'boundary_sha256':sha(boundary),'ledger_path':str(ledger),'ledger_manifest_path':str(ledger_manifest),'ledger_manifest_sha256':sha(ledger_manifest),'ledger_sha256':sha(ledger),'ledger_initial_bytes':ledger.stat().st_size,'ledger_initial_record_count':1,'tail_sync_receipt_path':str(tail),'tail_sync_receipt_sha256':sha(tail),'source_health_path':str(health),'source_instance_id':'source','continuity_epoch':1,'observer_binary':str(binary),'observer_binary_sha256':sha(binary),'finalizer_path':str(finalizer),'finalizer_sha256':sha(finalizer),'launcher_path':str(WRAPPER),'launcher_sha256':sha(WRAPPER),'source_replay_from_slot':7,'wallet_history_boundary_unix_ns':1}
    manifest=put(base/'launch.json',m); return manifest,m
def check(manifest, expect=True, env=None):
    run=subprocess.run([sys.executable,str(WRAPPER),'--manifest',str(manifest),'--verify-only'],text=True,capture_output=True,env=env)
    assert (run.returncode==0)==expect,(run.stdout,run.stderr)
with tempfile.TemporaryDirectory() as td:
    root=Path(td); manifest,m=fixture(root); check(manifest)
    # Each individual bound component must reject a V2/V4 mix before startup.
    for key in ('cohort_root','boundary_path','ledger_path','ledger_manifest_path','finalizer_path'):
        bad=dict(m); bad[key]=str(root/('v2-'+key)); put(root/(key+'.json'),bad); check(root/(key+'.json'),False)
    badenv=os.environ.copy(); badenv['KEFI_POOL_STATE_ROOT']=str(root/'v2-root'); check(manifest,False,badenv)
print('PASS: manifest wiring rejects V2/V4 root, ledger, boundary, finalizer and env mixes')
