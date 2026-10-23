#!/usr/bin/env python3
"""Publish immutable V3 sealed ordinals 351-500 without reading outcomes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_SEALED_VALIDATION_PUBLICATION_V1"
DEVELOPMENT_N = 350
VALIDATION_N = 150
FORBIDDEN_KEY_PARTS = ("pnl", "roi", "profit", "future_return", "exit_outcome")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def assert_outcome_blind(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                raise RuntimeError(f"OUTCOME_FIELD_FORBIDDEN:{location}.{key}")
            if lowered in {"outcomesread", "outcomes_read"}:
                if int(item or 0) != 0:
                    raise RuntimeError(f"OUTCOME_TAINT:{location}.{key}")
                continue
            assert_outcome_blind(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_outcome_blind(item, f"{location}[{index}]")


def publish(args: argparse.Namespace) -> dict[str, Any]:
    destination = args.output / "choice-sets.jsonl.gz"
    freeze_path = args.output / "freeze.json"
    if destination.exists() or freeze_path.exists():
        raise RuntimeError("SEALED_VALIDATION_ALREADY_PUBLISHED")

    status = json.loads(args.status.read_text(encoding="utf-8"))
    if status.get("validationSealed") is not True:
        raise RuntimeError("VALIDATION_NOT_SEALED")
    if int(status.get("outcomesRead") or 0) != 0 or int(status.get("validationRowsRead") or 0) != 0:
        raise RuntimeError("VALIDATION_ALREADY_READ_OR_TAINTED")
    if int(status.get("onlineEligibleN") or 0) < DEVELOPMENT_N + VALIDATION_N:
        raise RuntimeError("N500_NOT_READY")

    development = json.loads(args.development_freeze.read_text(encoding="utf-8"))
    frozen_signatures = [str(value) for value in development.get("signatures") or []]
    if len(frozen_signatures) != DEVELOPMENT_N:
        raise RuntimeError("DEVELOPMENT_FREEZE_NOT_N350")

    connection = sqlite3.connect(f"file:{args.state_db}?mode=ro", uri=True)
    try:
        if str(connection.execute("PRAGMA quick_check(1)").fetchone()[0]) != "ok":
            raise RuntimeError("STATE_DB_INTEGRITY_BLOCKER")
        placeholders = ",".join("?" for _ in frozen_signatures)
        frozen_rows = connection.execute(
            f"""SELECT signature,block_time_ms,slot,transaction_index
                FROM current_decisions
                WHERE signature IN ({placeholders}) AND status='ONLINE_ELIGIBLE'
                ORDER BY block_time_ms,slot,transaction_index,signature""",
            frozen_signatures,
        ).fetchall()
        if len(frozen_rows) != DEVELOPMENT_N:
            raise RuntimeError(f"FROZEN_DEVELOPMENT_MEMBER_MISSING:{len(frozen_rows)}")
        if [str(row[0]) for row in frozen_rows] != frozen_signatures:
            raise RuntimeError("FROZEN_DEVELOPMENT_ORDER_CHANGED")
        boundary_signature, boundary_ms, boundary_slot, boundary_index = frozen_rows[-1]
        rows = connection.execute(
            """SELECT signature,choice_json FROM current_decisions
               WHERE status='ONLINE_ELIGIBLE' AND (
                 block_time_ms > ? OR
                 (block_time_ms = ? AND slot > ?) OR
                 (block_time_ms = ? AND slot = ? AND transaction_index > ?) OR
                 (block_time_ms = ? AND slot = ? AND transaction_index = ? AND signature > ?)
               )
               ORDER BY block_time_ms,slot,transaction_index,signature
               LIMIT ?""",
            (
                boundary_ms,
                boundary_ms, boundary_slot,
                boundary_ms, boundary_slot, boundary_index,
                boundary_ms, boundary_slot, boundary_index, boundary_signature,
                VALIDATION_N,
            ),
        ).fetchall()
        retroactive_eligible = int(connection.execute(
            f"""SELECT COUNT(*) FROM current_decisions
                WHERE status='ONLINE_ELIGIBLE'
                  AND signature NOT IN ({placeholders})
                  AND NOT (
                    block_time_ms > ? OR
                    (block_time_ms = ? AND slot > ?) OR
                    (block_time_ms = ? AND slot = ? AND transaction_index > ?) OR
                    (block_time_ms = ? AND slot = ? AND transaction_index = ? AND signature > ?)
                  )""",
            frozen_signatures + [
                boundary_ms,
                boundary_ms, boundary_slot,
                boundary_ms, boundary_slot, boundary_index,
                boundary_ms, boundary_slot, boundary_index, boundary_signature,
            ],
        ).fetchone()[0])
    finally:
        connection.close()
    if len(rows) != VALIDATION_N:
        raise RuntimeError(f"TRUE_SEALED_VALIDATION_NOT_READY:{len(rows)}")

    validation: list[dict[str, Any]] = []
    for ordinal, (signature, raw) in enumerate(rows, DEVELOPMENT_N + 1):
        if not raw:
            raise RuntimeError(f"VALIDATION_CHOICE_PAYLOAD_MISSING:{signature}")
        payload = json.loads(str(raw))
        states = payload.get("states") or []
        if len(states) < 2 or int(payload.get("controlN") or 0) < 1:
            raise RuntimeError(f"VALIDATION_HAS_NO_CONTROL:{ordinal}")
        if sum(bool(state.get("label")) for state in states) != 1:
            raise RuntimeError(f"VALIDATION_LABEL_COUNT_INVALID:{ordinal}")
        payload["eligibleOrdinal"] = ordinal
        payload["split"] = "VALIDATION_SEALED"
        assert_outcome_blind(payload, f"validation[{ordinal}]")
        validation.append(payload)

    args.output.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for payload in validation:
                stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    os.replace(temporary, destination)
    freeze = {
        "schema": SCHEMA,
        "validationN": VALIDATION_N,
        "validationOrdinals": [DEVELOPMENT_N + 1, DEVELOPMENT_N + VALIDATION_N],
        "firstSignature": str(rows[0][0]),
        "lastSignature": str(rows[-1][0]),
        "developmentBoundary": {
            "signature": str(boundary_signature),
            "blockTimeMs": int(boundary_ms),
            "slot": int(boundary_slot),
            "transactionIndex": int(boundary_index),
        },
        "retroactiveEligibleExcludedFromValidationN": retroactive_eligible,
        "choiceSetsSha256": sha256_file(destination),
        "developmentFreezeSha256": sha256_file(args.development_freeze),
        "statusSha256AtPublication": sha256_file(args.status),
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(freeze_path, freeze)
    return freeze


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=pathlib.Path, required=True)
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--development-freeze", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(publish(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
