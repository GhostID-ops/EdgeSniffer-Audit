#!/usr/bin/env python3
"""Bootstrap and validate exact LaunchLab coverage from cached 2CQ decisions.

This is outcome-blind. It validates curve transitions and authoritative wallet
inventory only; it never reads exits, PnL, validation labels, or model output.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import statistics
from typing import Any

import launchLabExactEconomicsV1 as launchlab


SCHEMA = "2CQ_LAUNCHLAB_COVERAGE_V1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def atomic_jsonl_gz(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for row in rows:
                stream.write(canonical(row) + b"\n")
    os.replace(temporary, path)


def has_launchlab(row: dict[str, Any]) -> bool:
    return any(
        value.get("name") == "RAYDIUM_LAUNCHLAB"
        for value in row.get("knownPrograms") or []
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = [
        json.loads(line)
        for line in args.route_inventory.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = [row for row in source if has_launchlab(row)]
    output_rows: list[dict[str, Any]] = []
    failures: collections.Counter[str] = collections.Counter()
    quote_mints: collections.Counter[str] = collections.Counter()
    instruction_shapes: collections.Counter[str] = collections.Counter()
    transfer_fee_bps: list[float] = []
    transfer_fee_affected = 0
    direct_family = 0

    for source_row in selected:
        signature = str(source_row["signature"])
        path = args.wallet_rpc_cache / f"tx-{signature}.json"
        if not path.exists():
            failures["TRANSACTION_CACHE_MISSING"] += 1
            continue
        tx = json.loads(path.read_text(encoding="utf-8"))
        try:
            instructions = launchlab.transaction_trade_instructions(tx)
            events = launchlab.decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
            if len(instructions) != len(events):
                raise launchlab.LaunchLabExactError(
                    f"trade instruction/event count mismatch {len(instructions)}/{len(events)}"
                )
            validated = [
                launchlab.validate_constant_product_transition(
                    event, instruction, tx, require_wallet_consistency=True
                )
                for instruction, event in zip(instructions, events)
            ]
        except launchlab.LaunchLabExactError as error:
            failures[str(error)] += 1
            continue
        if not validated:
            failures["NO_SUCCESSFUL_TRADE_TRANSITION"] += 1
            continue
        if str(source_row.get("economicFamily")) == "OTHER_DEX":
            direct_family += 1
        for result in validated:
            quote_mints[str(result["quoteMint"])] += 1
            instruction_shapes[f"{result['side']}_{result['amountMode']}"] += 1
            curve_out = int(result["curveAmountOutRaw"])
            base_delta = result.get("walletBaseDeltaRaw")
            if result["side"] == "BUY" and base_delta is not None and curve_out > 0:
                fee = int(result.get("baseTransferFeeRaw") or 0)
                if fee > 0:
                    transfer_fee_affected += 1
                transfer_fee_bps.append(fee * 10_000 / curve_out)
            output_rows.append({
                "schema": SCHEMA + "_TRANSITION",
                "signature": signature,
                "slot": int(tx.get("slot") or 0),
                "transactionIndex": tx.get("transactionIndex"),
                "teacherMint": str(source_row.get("mint") or ""),
                "sourceEconomicFamily": str(source_row.get("economicFamily") or ""),
                **result,
                "outcomesRead": 0,
                "validationRowsRead": 0,
                "submissions": 0,
            })

    output_rows.sort(key=lambda row: (
        int(row.get("slot") or 0),
        int(row.get("transactionIndex") or -1),
        str(row["signature"]),
    ))
    transitions = args.output / "teacher-exact-transitions.jsonl.gz"
    atomic_jsonl_gz(transitions, output_rows)
    teacher_pool_values = {row["poolState"] for row in output_rows}
    teacher_pools = args.output / "teacher-known-pools.txt"
    teacher_pools_temporary = teacher_pools.with_suffix(".txt.tmp")
    teacher_pools_temporary.write_text(
        "".join(f"{value}\n" for value in sorted(teacher_pool_values)), encoding="ascii"
    )
    os.replace(teacher_pools_temporary, teacher_pools)

    # Bootstrap may run while prospective collection is active. Seed the observer
    # registry without removing pools already discovered by the reconciler.
    known_pools = args.output / "known-pools.txt"
    known_pool_values = set(teacher_pool_values)
    if known_pools.exists():
        known_pool_values.update(
            value.strip()
            for value in known_pools.read_text(encoding="ascii").splitlines()
            if value.strip()
        )
    known_pools_temporary = known_pools.with_suffix(".txt.tmp")
    known_pools_temporary.write_text(
        "".join(f"{value}\n" for value in sorted(known_pool_values)), encoding="ascii"
    )
    os.replace(known_pools_temporary, known_pools)
    p50 = statistics.median(transfer_fee_bps) if transfer_fee_bps else None
    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceInventoryN": len(source),
        "teacherDecisionsInvokingLaunchLabN": len(selected),
        "directLaunchLabFamilyN": direct_family,
        "exactValidatedTransitionsN": len(output_rows),
        "knownTeacherPoolN": len({row["poolState"] for row in output_rows}),
        "failedDecisionN": len(selected) - len({row["signature"] for row in output_rows}),
        "failures": dict(failures.most_common()),
        "instructionShapes": dict(instruction_shapes.most_common()),
        "quoteMintCounts": dict(quote_mints.most_common()),
        "token2022Inventory": {
            "transferFeeAffectedBuyN": transfer_fee_affected,
            "medianCurveOutputToWalletLossBps": p50,
            "walletDeltaIsAuthoritative": True,
        },
        "coverageSemantics": {
            "historicalTeacherTransitionBootstrap": True,
            "historicalFullUniverseControls": False,
            "prospectiveFullUniverseCollectorRequired": True,
            "curveTypeValidated": "CONSTANT_PRODUCT_BY_EXACT_INTEGER_IDENTITY",
            "unsupportedCurveTypesFailClosed": True,
        },
        "integrity": {
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
            "liveTradingModified": False,
        },
    }
    atomic_json(args.output / "bootstrap-report.json", report)
    atomic_json(args.output / "bootstrap-provenance.json", {
        "schema": SCHEMA + "_PROVENANCE",
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "evaluatorSha256": sha256_file(pathlib.Path(launchlab.__file__)),
        "sourceInventorySha256": sha256_file(args.route_inventory),
        "transitionsSha256": sha256_file(transitions),
        "reportSha256": sha256_file(args.output / "bootstrap-report.json"),
        "outcomesRead": 0,
        "validationRowsRead": 0,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-inventory", type=pathlib.Path, required=True)
    parser.add_argument("--wallet-rpc-cache", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
