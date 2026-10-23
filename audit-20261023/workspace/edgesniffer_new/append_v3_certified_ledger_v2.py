#!/usr/bin/env python3
"""Create an immutable V2 V3 certified-segment ledger; never mutates V1."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1")
V3 = BASE / "autonomous-when-v3"
DIR = V3 / "certified-segments-v1"
V1 = DIR / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_LEDGER_V1.json"
LEDGER = DIR / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_LEDGER_V2.json"
RECEIPT = DIR / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_LEDGER_V2_RECEIPT.json"
ROOT = V3 / "scientific-20260930T141719Z"
PREFIX = ROOT / "receipts" / "failed-segment-prefix-certification-v1.json"
BOUNDARY = ROOT / "lane" / "boundary.json"
AMENDMENT = V3 / "protocol-v2" / "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_DEVELOPMENT_AMENDMENT_V1.json"
TIMING = V3 / "protocol-v2" / "AUTONOMOUS_WHEN_V3_REDUNDANT_LIVE_TIMING_CONTRACT_V2.json"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def immutable(path: Path, value: dict) -> None:
    if path.exists():
        raise SystemExit(f"immutable output exists: {path}")
    temp = path.with_suffix(".partial")
    with temp.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def main() -> None:
    for file in (V1, PREFIX, BOUNDARY, AMENDMENT, TIMING):
        if not file.is_file():
            raise SystemExit(f"missing authoritative input: {file}")
    prior = json.loads(V1.read_text(encoding="utf-8"))
    prefix = json.loads(PREFIX.read_text(encoding="utf-8"))
    if prior.get("status") != "CERTIFIED_SEGMENT_ACCOUNTING_ACTIVE":
        raise SystemExit("V1 ledger status is not active")
    if prefix.get("result") != "CERTIFIED_PREFIX_ONLY":
        raise SystemExit("new prefix is not certified")
    existing = prior["entries"]
    new_identities = {(label["signature"], label["mint"]) for label in prefix["finalizedTeacherLabels"]}
    if len(new_identities) != prefix["certifiableFinalizedIndependentTeacherN"]:
        raise SystemExit("duplicate teacher identity in new prefix")
    prior_seconds = existing[-1]["cumulativeCertifiedSeconds"] if existing else 0
    prior_n = existing[-1]["cumulativeCertifiableN"] if existing else 0
    seconds = prefix["certifiedDurationSeconds"]
    count = prefix["certifiableFinalizedIndependentTeacherN"]
    entry = {
        "segmentId": "v3-failed-prefix-scientific-20260930T141719Z",
        "sourceRoot": str(ROOT),
        "boundaryTimestampUtc": prefix["boundaryUtc"],
        "boundarySha256": digest(BOUNDARY),
        "timingContractSha256": digest(TIMING),
        "accountingAmendmentSha256": digest(AMENDMENT),
        "certifiedStartUtc": prefix["certifiedStartUtc"],
        "certifiedEndUtc": prefix["certifiedEndUtc"],
        "firstHardWallTimestampUtc": prefix["hardWallUtc"],
        "firstHardWallReason": "SCHEDULED_TICK_CADENCE_VIOLATION / LANE_EOF_DOES_NOT_DISCOVER_NEXT_PARTITION",
        "warmupSecondsExcluded": prefix["warmupSeconds"],
        "suffixSecondsExcluded": prefix["suffixIsolationSeconds"],
        "exactCertifiedSeconds": seconds,
        "certifiableFinalizedIndependentTeacherN": count,
        "continuityStatus": "CLEAN_UNTIL_FIRST_HARD_WALL",
        "cadenceStatus": "CERTIFIED_CONTINUOUS_PREFIX",
        "timingProvenanceStatus": "PRESERVED_ORIGINAL_RECEIPT_TIMESTAMPS",
        "certificationReceiptPath": str(PREFIX),
        "certificationReceiptSha256": digest(PREFIX),
        "cumulativeCertifiedSeconds": prior_seconds + seconds,
        "cumulativeCertifiableN": prior_n + count,
    }
    ledger = {
        "schema": "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_LEDGER_V2",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "supersedes": {"path": str(V1), "sha256": digest(V1)},
        "entries": existing + [entry],
        "status": "CERTIFIED_SEGMENT_ACCOUNTING_ACTIVE",
        "invariants": {"crossSegmentCausalState": "PROHIBITED", "postWallEvidence": "EXCLUDED", "rawEvidenceMutated": False},
    }
    immutable(LEDGER, ledger)
    receipt = {
        "schema": "AUTONOMOUS_WHEN_V3_CERTIFIED_SEGMENT_LEDGER_V2_RECEIPT",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "ledgerPath": str(LEDGER),
        "ledgerSha256": digest(LEDGER),
        "newEntry": entry["segmentId"],
        "cumulativeCertifiedSeconds": entry["cumulativeCertifiedSeconds"],
        "cumulativeCertifiableN": entry["cumulativeCertifiableN"],
        "rawEvidenceMutated": False,
    }
    immutable(RECEIPT, receipt)
    print(json.dumps({"ledger": str(LEDGER), "ledger_sha256": digest(LEDGER), "receipt": str(RECEIPT), "receipt_sha256": digest(RECEIPT), "seconds": entry["cumulativeCertifiedSeconds"], "n": entry["cumulativeCertifiableN"]}, sort_keys=True))


if __name__ == "__main__":
    main()
