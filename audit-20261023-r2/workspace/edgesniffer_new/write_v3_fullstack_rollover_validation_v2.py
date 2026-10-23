#!/usr/bin/env python3
"""Persist the second instrumented full-stack V3 rollover validation."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1/autonomous-when-v3")
SOURCE = Path("/tmp/v3-fullstack-forced-rollover-result-v2.json")
DEST = BASE / "receipts" / "V3_FULLSTACK_FORCED_ROLLOVER_VALIDATION_V2.json"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(1 << 20), b""):
            h.update(data)
    return h.hexdigest()


if DEST.exists():
    raise SystemExit("immutable validation output exists")
result = json.loads(SOURCE.read_text(encoding="utf-8"))
if result.get("status") != "PASS" or result.get("missingTicks") != 0 or result.get("duplicateTicks") != 0:
    raise SystemExit("forced rollover validation failed")
payload = {"schema": "V3_FULLSTACK_FORCED_ROLLOVER_VALIDATION_V2", "createdAtUtc": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"), "result": result, "outcomeBlind": True, "pnlRead": 0, "productionScientificRootsWritten": False}
temp = DEST.with_suffix(".partial")
with temp.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush(); os.fsync(handle.fileno())
os.replace(temp, DEST); DEST.chmod(0o444)
print(json.dumps({"path": str(DEST), "sha256": digest(DEST)}, sort_keys=True))
