#!/usr/bin/env python3
"""Classify missing 2CQ exact-ledger signatures without reading trade outcomes."""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import urllib.request


def env_value(path: pathlib.Path, name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"missing {name}")


def statuses(endpoint: str, signatures: list[str]) -> list[dict | None]:
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [signatures, {"searchTransactionHistory": True}],
    }).encode()
    request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    return result["result"]["value"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    rows = [row for row in audit["rows"] if row["classification"].startswith("NO_RAW_SIGNATURE_")]
    endpoint = env_value(args.env, "SECURE_RPC_URL")
    classified = []
    for offset in range(0, len(rows), 200):
        batch = rows[offset:offset + 200]
        values = statuses(endpoint, [row["signature"] for row in batch])
        for row, status in zip(batch, values):
            if status is None:
                classification = "NO_CHAIN_STATUS_DROPPED_OR_NEVER_LANDED"
            elif status.get("err") is not None:
                classification = "CHAIN_STATUS_FAILED_EXACT_LEDGER_MISS"
            elif status.get("confirmationStatus") == "finalized":
                classification = "CHAIN_STATUS_FINALIZED_SUCCESS_EXACT_LEDGER_MISS"
            else:
                classification = "CHAIN_STATUS_NONFINAL_EXACT_LEDGER_MISS"
            classified.append({**row, "statusClassification": classification, "chainStatus": status})
    counts = collections.Counter(row["statusClassification"] for row in classified)
    report = {
        "schema": "2CQ_PROSPECTIVE_COLLECTION_RELIABILITY_REPAIR_V1_MISSING_SIGNATURE_STATUS",
        "sourceAudit": str(args.audit),
        "queriedN": len(classified),
        "counts": dict(sorted(counts.items())),
        "rows": classified,
        "outcomesRead": 0,
        "pnlRead": 0,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
