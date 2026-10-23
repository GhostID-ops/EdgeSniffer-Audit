#!/usr/bin/env python3
"""Write the first immutable V3 certified-segment ledger from a verified receipt."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1")
V3 = BASE / "autonomous-when-v3"
FAILED_ROOT = V3 / "scientific-20260930T073833Z"
RECEIPT = FAILED_ROOT / "receipts" / "failed-segment-prefix-certification-v1.json"
AMENDMENT = V3 / "protocol-v2" / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_DEVELOPMENT_AMENDMENT_V1.json"
TIMING = V3 / "protocol-v2" / "AUTONOMOUS_WHEN_V3_REDUNDANT_LIVE_TIMING_CONTRACT_V2.json"
BOUNDARY = FAILED_ROOT / "lane" / "boundary.json"
LEDGER_DIR = V3 / "certified-segments-v1"
LEDGER = LEDGER_DIR / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_LEDGER_V1.json"
LEDGER_RECEIPT = LEDGER_DIR / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_LEDGER_V1_RECEIPT.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_immutable(path: Path, payload: dict) -> None:
    if path.exists():
        raise SystemExit(f"refusing to overwrite immutable artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    with temp.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def main() -> None:
    for artifact in (RECEIPT, AMENDMENT, TIMING, BOUNDARY):
        if not artifact.is_file():
            raise SystemExit(f"required authoritative artifact missing: {artifact}")

    prefix = json.loads(RECEIPT.read_text(encoding="utf-8"))
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    if prefix["result"] != "CERTIFIED_PREFIX_ONLY":
        raise SystemExit("prefix receipt is not a certified-prefix result")
    if prefix["hardWallUtc"] != "2026-09-30T08:00:02.002132Z":
        raise SystemExit("unexpected first hard wall")
    if prefix["certifiedStartUtc"] != "2026-09-30T07:44:31.455772Z":
        raise SystemExit("unexpected certified start")
    if prefix["certifiedEndUtc"] != "2026-09-30T07:59:42.002132Z":
        raise SystemExit("unexpected certified end")
    if amendment["status"] != "FROZEN_PRE_ANALYSIS":
        raise SystemExit("governing amendment is not frozen")

    receipt_sha = sha256(RECEIPT)
    amendment_sha = sha256(AMENDMENT)
    timing_sha = sha256(TIMING)
    boundary_sha = sha256(BOUNDARY)
    labels = prefix["finalizedTeacherLabels"]
    identities = {(label["signature"], label["mint"]) for label in labels}
    if len(identities) != len(labels):
        raise SystemExit("duplicate teacher identity in certification receipt")

    seconds = prefix["certifiedDurationSeconds"]
    count = prefix["certifiableFinalizedIndependentTeacherN"]
    if count != len(labels):
        raise SystemExit("teacher label count does not match receipt total")

    entry = {
        "accountingAmendmentSha256": amendment_sha,
        "boundarySha256": boundary_sha,
        "boundaryTimestampUtc": prefix["boundaryUtc"],
        "cadenceStatus": "CERTIFIED_CONTINUOUS_PREFIX",
        "certifiableFinalizedIndependentTeacherN": count,
        "certificationReceiptPath": str(RECEIPT),
        "certificationReceiptSha256": receipt_sha,
        "certifiedEndUtc": prefix["certifiedEndUtc"],
        "certifiedStartUtc": prefix["certifiedStartUtc"],
        "continuityStatus": "CLEAN_UNTIL_FIRST_HARD_WALL",
        "cumulativeCertifiableN": count,
        "cumulativeCertifiedSeconds": seconds,
        "exactCertifiedSeconds": seconds,
        "firstHardWallReason": "SCHEDULED_TICK_CADENCE_VIOLATION / HOURLY_PARTITION_ROTATION_CAUSED_SOURCE_STALL",
        "firstHardWallTimestampUtc": prefix["hardWallUtc"],
        "segmentId": "v3-failed-prefix-scientific-20260930T073833Z",
        "sourceRoot": str(FAILED_ROOT),
        "suffixSecondsExcluded": prefix["suffixIsolationSeconds"],
        "timingContractSha256": timing_sha,
        "timingProvenanceStatus": "PRESERVED_ORIGINAL_RECEIPT_TIMESTAMPS",
        "warmupSecondsExcluded": prefix["warmupSeconds"],
    }
    ledger = {
        "accountingAmendment": {"path": str(AMENDMENT), "sha256": amendment_sha},
        "createdAtUtc": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "entries": [entry],
        "invariants": {
            "crossSegmentCausalState": "PROHIBITED",
            "postWallEvidence": "EXCLUDED",
            "rawEvidenceMutated": False,
            "validPreWallEvidenceBankedOnlyAfterIndependentCertification": True,
        },
        "schema": "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_LEDGER_V1",
        "status": "CERTIFIED_SEGMENT_ACCOUNTING_ACTIVE",
    }
    write_immutable(LEDGER, ledger)
    ledger_sha = sha256(LEDGER)
    receipt = {
        "createdAtUtc": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "ledgerPath": str(LEDGER),
        "ledgerSha256": ledger_sha,
        "operation": "FIRST_CERTIFIED_V3_SEGMENT_LEDGER_ENTRY",
        "sourceCertificationReceipt": {"path": str(RECEIPT), "sha256": receipt_sha},
        "verified": {
            "certifiedSeconds": seconds,
            "hardWallExcluded": True,
            "independentTeacherIdentities": len(identities),
            "postWallEvidenceExcluded": True,
            "rawEvidenceMutated": False,
        },
    }
    write_immutable(LEDGER_RECEIPT, receipt)
    print(json.dumps({
        "ledger": str(LEDGER),
        "ledger_sha256": ledger_sha,
        "receipt": str(LEDGER_RECEIPT),
        "receipt_sha256": sha256(LEDGER_RECEIPT),
        "certified_seconds": seconds,
        "certifiable_n": count,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
