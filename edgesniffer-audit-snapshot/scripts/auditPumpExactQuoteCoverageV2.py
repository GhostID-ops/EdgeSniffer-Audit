#!/usr/bin/env python3
"""Cross-check non-SOL Pump transitions against immutable finalized evidence."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
from typing import Any

from exactCurveEconomicsQuoteV2 import (
    SCHEMA,
    decode_quote_trade_events,
    validate_quote_transition,
)
from exactCurveEconomicsV1 import extract_pump_source_observations
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
        if "unsupported_trade_event_schema" in row["exclusionReasons"]
    }
    validated_mints: set[str] = set()
    quote_mints: collections.Counter[str] = collections.Counter()
    layouts: collections.Counter[str] = collections.Counter()
    failures: list[dict[str, Any]] = []
    partition_hour = dt.datetime.fromisoformat(
        readiness["partitionHourUtc"].replace("Z", "+00:00")
    )
    directories = [
        args.root
        / "partitions"
        / (partition_hour + dt.timedelta(hours=offset)).strftime("%Y/%m/%d/%H")
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
                continue
            try:
                events = decode_quote_trade_events((tx.get("meta") or {}).get("logMessages") or [])
            except Exception as error:
                failures.append({"signature": metadata.get("signature"), "error": str(error)})
                continue
            unused_sources = []
            for mint in {event.mint for event in events if event.mint in targets}:
                unused_sources.extend(
                    extract_pump_source_observations(
                        tx, metadata.get("signature") or "", mint, 0, "immutable_ledger"
                    )
                )
            unused_sources.sort(key=lambda source: source.instruction_index)
            for event in events:
                if event.mint not in targets:
                    continue
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
                    failures.append(
                        {
                            "mint": event.mint,
                            "signature": metadata.get("signature"),
                            "error": "source pair missing",
                        }
                    )
                    continue
                source = unused_sources.pop(source_index)
                layouts[f"{source.instruction.kind}:{len(source.source_accounts)}"] += 1
                try:
                    validate_quote_transition(source.instruction, event)
                except Exception as error:
                    failures.append(
                        {
                            "mint": event.mint,
                            "signature": metadata.get("signature"),
                            "error": str(error),
                        }
                    )
                    continue
                validated_mints.add(event.mint)
    missing = sorted(targets - validated_mints)
    report = {
        "schema": SCHEMA,
        "classification": (
            "CURRENT_LEDGER_SUPPORTS_EXACT_QUOTE_NATIVE_REPLAY"
            if not failures and not missing
            else "QUOTE_NATIVE_REPLAY_BLOCKER"
        ),
        "targetExcludedMints": len(targets),
        "validatedMints": len(validated_mints),
        "missingMints": missing,
        "quoteMints": dict(sorted(quote_mints.items())),
        "instructionLayouts": dict(sorted(layouts.items())),
        "failures": failures,
        "solNormalization": "BLOCKED_CAUSAL_CONVERSION_EVIDENCE_REQUIRED",
        "v1EvaluatorMutated": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["classification"] == "CURRENT_LEDGER_SUPPORTS_EXACT_QUOTE_NATIVE_REPLAY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
