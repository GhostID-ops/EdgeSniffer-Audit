#!/usr/bin/env python3
"""Cross-check every current Mayhem exclusion against immutable finalized evidence."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
from typing import Any

from exactCurveEconomicsMayhemV2 import SCHEMA, validate_mayhem_transition
from exactCurveEconomicsV1 import decode_trade_events, extract_pump_source_observations
from reconcilePumpExactEvidenceLedgerV1 import iter_frames, partition_data_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    parser.add_argument("readiness_report", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    readiness = json.loads(args.readiness_report.read_text(encoding="utf-8"))
    targets = {
        row["mint"]
        for row in readiness["mints"]
        if "unsupported_mayhem_transition" in row["exclusionReasons"]
    }
    counters: collections.Counter[str] = collections.Counter()
    validated_mints: set[str] = set()
    failures: list[dict[str, Any]] = []
    layouts: collections.Counter[str] = collections.Counter()
    quote_mints: collections.Counter[str] = collections.Counter()
    partition_hour = dt.datetime.fromisoformat(
        readiness["partitionHourUtc"].replace("Z", "+00:00")
    )
    directories = [
        args.root / "partitions" / (partition_hour + dt.timedelta(hours=offset)).strftime("%Y/%m/%d/%H")
        for offset in range(3)
    ]
    for directory in directories:
        path = partition_data_path(directory)
        if path is None:
            continue
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            if metadata.get("kind") != "rpc_finalized_transaction":
                continue
            if not targets.intersection(metadata.get("discoveredMints") or []):
                continue
            try:
                tx = json.loads(raw).get("result")
            except Exception as error:
                failures.append({"signature": metadata.get("signature"), "error": str(error)})
                continue
            if not tx or (tx.get("meta") or {}).get("err") is not None:
                counters["failed_zero_state"] += 1
                continue
            try:
                events = decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
            except Exception as error:
                counters["unsupported_schema"] += 1
                failures.append({"signature": metadata.get("signature"), "error": str(error)})
                continue
            transaction_mints = {event.mint for event in events if event.mint in targets}
            unused_sources = []
            for mint in transaction_mints:
                unused_sources.extend(
                    extract_pump_source_observations(
                        tx, metadata.get("signature") or "", mint, 0, "immutable_ledger"
                    )
                )
            unused_sources.sort(key=lambda source: source.instruction_index)
            for event in events:
                if event.mint not in targets or not event.mayhem_mode:
                    continue
                counters["mayhem_events"] += 1
                quote_mints[event.quote_mint] += 1
                source_index = next(
                    (
                        index
                        for index, source in enumerate(unused_sources)
                        if source.mint == event.mint and (source.side == "buy") == event.is_buy
                    ),
                    None,
                )
                if source_index is None:
                    counters["source_pair_failures"] += 1
                    failures.append(
                        {"mint": event.mint, "signature": metadata.get("signature"), "error": "source pair missing"}
                    )
                    continue
                source = unused_sources.pop(source_index)
                layouts[f"{source.instruction.kind}:{len(source.source_accounts)}"] += 1
                try:
                    validate_mayhem_transition(source, event)
                except Exception as error:
                    counters["validation_failures"] += 1
                    failures.append(
                        {"mint": event.mint, "signature": metadata.get("signature"), "error": str(error)}
                    )
                    continue
                counters["validated_events"] += 1
                validated_mints.add(event.mint)
    missing_mints = sorted(targets - validated_mints)
    report = {
        "schema": SCHEMA,
        "classification": (
            "CURRENT_LEDGER_SUFFICIENT_FOR_MAYHEM_EXACT_REPLAY"
            if not failures and not missing_mints
            else "MAYHEM_EXACT_REPLAY_EVIDENCE_BLOCKER"
        ),
        "targetExcludedMints": len(targets),
        "validatedMints": len(validated_mints),
        "missingMints": missing_mints,
        "counters": dict(sorted(counters.items())),
        "instructionLayouts": dict(sorted(layouts.items())),
        "quoteMints": dict(sorted(quote_mints.items())),
        "failures": failures,
        "v1EvaluatorMutated": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["classification"] == "CURRENT_LEDGER_SUFFICIENT_FOR_MAYHEM_EXACT_REPLAY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
