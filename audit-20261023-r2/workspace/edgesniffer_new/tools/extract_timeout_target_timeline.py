#!/usr/bin/env python3
import json
import sys

START = "/tmp/edgesniffer_timeout_validation_start_line"
AUD = "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-dust/rust-selected-candidate-audit.jsonl"

try:
    start = int(open(START, "r", encoding="utf-8").read().strip() or "0")
except Exception:
    start = 0

rows = []
target = None
for i, line in enumerate(open(AUD, "r", encoding="utf-8", errors="replace"), start=1):
    if i <= start:
        continue
    try:
        row = json.loads(line)
    except Exception:
        continue
    rows.append(row)
    if row.get("kind") == "rustTestInjectSellStatusTimeoutTriggered":
        target = row.get("mint")

if not target:
    print(json.dumps({"target": None, "events": []}, indent=2))
    sys.exit(0)

interesting = []
for r in rows:
    if r.get("mint") != target and r.get("kind") != "rustSelectedCandidateWalletReconcile":
        continue
    if r.get("kind") in {
        "rustSelectedCandidateAccepted",
        "rustSelectedCandidateBuyStart",
        "rustSelectedCandidateBuyResult",
        "rustSelectedCandidateBuyFinality",
        "rustSelectedCandidateExactBalanceProof",
        "rustSelectedCandidateStreamingExactBalanceActivation",
        "rustExitDecision",
        "rustExitSellBroadcast",
        "rustTestInjectSellStatusTimeoutTriggered",
        "rustExitSellFinality",
        "rustExitSellStatusTimeoutFlatReconcileStart",
        "rustExitSellStatusTimeoutFlatReconcileNonzero",
        "rustExitSellStatusTimeoutWalletFlatAck",
        "rustExitSellFailed",
        "rustExitSellFailureCount",
        "rustSelectedCandidateWalletTrackedFlat",
        "rustSelectedCandidateWalletManagedBalanceUpdate",
        "rustSelectedCandidateWalletReconcile",
    }:
        interesting.append(
            {
                "kind": r.get("kind"),
                "atMs": r.get("atMs") or r.get("observedAtMs"),
                "mint": r.get("mint"),
                "signature": r.get("signature"),
                "reason": r.get("reason"),
                "status": r.get("status"),
                "ok": r.get("ok"),
                "error": r.get("error") or r.get("finalityError"),
                "pnlPct": r.get("pnlPct") or r.get("feeAdjustedPnlPct"),
                "expectedSolOutLamports": r.get("expectedSolOutLamports") or r.get("executableSolOutLamports"),
                "minSolOutputLamports": r.get("minSolOutputLamports") or r.get("minOutLamports"),
                "observedStatus": r.get("observedStatus"),
                "observedOk": r.get("observedOk"),
                "testInjectedSellStatusTimeout": r.get("testInjectedSellStatusTimeout"),
                "amountRaw": r.get("amountRaw") or r.get("rawTokenBalance"),
                "managed": r.get("managed"),
                "positions": r.get("positions"),
                "unmanaged": r.get("unmanaged"),
                "source": r.get("source") or r.get("quoteSource"),
            }
        )

print(json.dumps({"target": target, "events": interesting}, indent=2, sort_keys=True))
