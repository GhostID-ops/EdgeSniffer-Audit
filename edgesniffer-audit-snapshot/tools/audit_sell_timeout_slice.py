#!/usr/bin/env python3
import collections
import json

START = "/tmp/edgesniffer_timeout_validation_start_line"
AUD = "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-dust/rust-selected-candidate-audit.jsonl"

try:
    start = int(open(START, "r", encoding="utf-8").read().strip() or "0")
except Exception:
    start = 0

rows = []
with open(AUD, "r", encoding="utf-8", errors="replace") as f:
    for i, line in enumerate(f, start=1):
        if i <= start:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass

counts = collections.Counter(r.get("kind") for r in rows)
rejects = collections.Counter(
    r.get("reason")
    or r.get("rejectReason")
    or r.get("reasonCode")
    or ",".join(r.get("reasonCodes", []) or [])
    for r in rows
    if r.get("kind") == "rustSelectedCandidateRejected"
)
accepted = [r for r in rows if r.get("kind") == "rustSelectedCandidateAccepted"]
by_mint = collections.defaultdict(collections.Counter)
for r in rows:
    m = r.get("mint")
    if m:
        by_mint[m][r.get("kind")] += 1
print(json.dumps({
    "startLine": start,
    "totalRows": len(rows),
    "counts": counts,
    "rejectReasons": rejects.most_common(20),
    "acceptedMints": collections.Counter(r.get("mint") for r in accepted).most_common(20),
    "mintEventCounts": {m: dict(c) for m, c in list(by_mint.items())[-20:]},
    "tail": rows[-10:],
}, indent=2, sort_keys=True))
