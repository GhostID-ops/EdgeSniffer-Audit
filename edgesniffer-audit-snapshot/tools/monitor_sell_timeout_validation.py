#!/usr/bin/env python3
import collections
import json
import sys

start_line_path = "/tmp/edgesniffer_timeout_validation_start_line"
audit_path = "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-dust/rust-selected-candidate-audit.jsonl"

try:
    with open(start_line_path, "r", encoding="utf-8") as f:
        start_line = int(f.read().strip() or "0")
except Exception:
    start_line = 0

interesting = {
    "rustSelectedCandidateAccepted",
    "rustSelectedCandidateBuyResult",
    "rustExitSellBroadcast",
    "rustExitSellFinality",
    "rustTestInjectSellStatusTimeoutTriggered",
    "rustExitSellStatusTimeoutFlatReconcileStart",
    "rustExitSellStatusTimeoutWalletFlatAck",
    "rustExitSellStatusTimeoutFlatReconcileNonzero",
    "rustExitSellStatusTimeoutFlatReconcileUnknown",
    "rustSelectedCandidateWalletReconcile",
}

counts = collections.Counter()
events = []

with open(audit_path, "r", encoding="utf-8", errors="replace") as f:
    for lineno, line in enumerate(f, start=1):
        if lineno <= start_line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        kind = row.get("kind")
        counts[kind] += 1
        if kind in interesting:
            events.append(
                {
                    "kind": kind,
                    "atMs": row.get("atMs"),
                    "mint": row.get("mint"),
                    "signature": row.get("signature"),
                    "status": row.get("status"),
                    "ok": row.get("ok"),
                    "error": row.get("error") or row.get("err"),
                    "observedStatus": row.get("observedStatus"),
                    "observedOk": row.get("observedOk"),
                    "testInjectedSellStatusTimeout": row.get("testInjectedSellStatusTimeout"),
                    "managed": row.get("managed"),
                    "positions": row.get("positions"),
                    "unmanaged": row.get("unmanaged"),
                    "rawTokenBalance": row.get("rawTokenBalance") or row.get("amountRaw"),
                    "source": row.get("source"),
                }
            )

print(json.dumps({"startLine": start_line, "counts": counts, "eventsTail": events[-50:]}, indent=2, sort_keys=True))
