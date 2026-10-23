#!/usr/bin/env python3
"""Prepare/verify a transparent NFS-backed local-path release.

The live path is preserved as a symlink to its already verified canonical NFS
payload.  This tool is deliberately split into PLAN, VERIFY, and RELEASE.
RELEASE is not used until a prior archived object has survived a validation
cycle.  The archive receipt and dependency audit remain immutable facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def utcnow(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def sha(path: Path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()
def canon(v): return (json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=True)+"\n").encode()
def load(path): return json.loads(path.read_text())

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-payload",required=True); ap.add_argument("--archive-payload",required=True)
    ap.add_argument("--archive-receipt",required=True); ap.add_argument("--dependency-audit",required=True)
    ap.add_argument("--release-receipt-dir",required=True); ap.add_argument("--mode",choices=["PLAN","VERIFY","RELEASE"],default="PLAN")
    args=ap.parse_args(); local=Path(args.local_payload).absolute(); archive=Path(args.archive_payload).absolute()
    archive_receipt=Path(args.archive_receipt).absolute(); audit_path=Path(args.dependency_audit).absolute()
    try:
        if not local.is_file() or local.is_symlink(): raise ValueError("local payload must be a regular local file")
        if not archive.is_file(): raise ValueError("canonical archive payload missing")
        audit=load(audit_path); receipt=load(archive_receipt)
        if audit.get("outcome") != "DEPENDENCY_NONE_PROVEN":
            raise ValueError("dependency audit does not prove absence")
        if receipt.get("status") not in {"NFS_VERIFIED","ALREADY_VERIFIED"}:
            raise ValueError("archive receipt is not NFS_VERIFIED")
        local_sha=sha(local); archive_sha=sha(archive)
        if local_sha != archive_sha: raise ValueError("payload SHA-256 mismatch")
        plan={"schema":"transparent-release-v1","mode":args.mode,"local_path":str(local),"archive_path":str(archive),
              "payload_sha256":local_sha,"released_bytes":local.stat().st_size,"path_semantics_preserved":True,
              "archive_receipt_sha256":sha(archive_receipt),"dependency_audit_sha256":sha(audit_path),
              "archive_verified":True,"local_release_permitted":args.mode=="RELEASE"}
        if args.mode != "RELEASE": print(json.dumps(plan,sort_keys=True)); return
        # A validated archive must already have survived an independent cycle;
        # release itself is atomic at the pathname level.
        tmp=local.with_name(local.name+".nfs-link.tmp")
        if tmp.exists() or tmp.is_symlink(): raise ValueError("temporary symlink path already exists")
        os.symlink(archive, tmp)
        if sha(tmp) != local_sha:
            tmp.unlink(); raise ValueError("symlink readback mismatch")
        os.replace(tmp,local)
        if not local.is_symlink() or sha(local) != local_sha: raise ValueError("post-release path verification failed")
        out=Path(args.release_receipt_dir).absolute(); out.mkdir(parents=True,exist_ok=True)
        receipt_path=out/(local.name+".release.json")
        release={**plan,"released_at_utc":utcnow(),"local_present":False,"local_released":True,"local_path_now_symlink":str(local),"previous_release_receipt_sha256":None}
        receipt_path.write_bytes(canon(release)); release["release_receipt_sha256"]=sha(receipt_path)
        print(json.dumps({**release,"release_receipt":str(receipt_path)},sort_keys=True))
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(json.dumps({"status":"FAIL_CLOSED","error":str(exc)}),file=sys.stderr); raise SystemExit(2)

if __name__=="__main__": main()
