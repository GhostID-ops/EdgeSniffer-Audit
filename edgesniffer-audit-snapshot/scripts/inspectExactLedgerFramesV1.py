#!/usr/bin/env python3
"""Read decompressed PEL1 frames from stdin and emit a compact integrity summary."""

import hashlib
import json
import os
import struct
import sys
from datetime import datetime
from collections import Counter


MAGIC = b"PEL1FRM\0"


def main() -> int:
    stream = sys.stdin.buffer
    if stream.read(len(MAGIC)) != MAGIC:
        raise SystemExit("invalid frame magic")
    kinds = Counter()
    transaction_shapes = Counter()
    rpc_results = []
    samples = []
    first_transaction = None
    last_transaction = None
    first_transaction_at_or_after_boundary = None
    boundary_text = os.environ.get("PEL_BOUNDARY_UTC")
    boundary = datetime.fromisoformat(boundary_text.replace("Z", "+00:00")) if boundary_text else None
    observed_signatures = set()
    finalized_signatures = set()
    count = 0
    while True:
        prefix = stream.read(8)
        if not prefix:
            break
        if prefix == MAGIC:
            continue
        if len(prefix) != 8:
            raise SystemExit("truncated frame prefix")
        metadata_len, raw_len = struct.unpack("<II", prefix)
        body = stream.read(metadata_len + raw_len)
        digest = stream.read(32)
        if len(body) != metadata_len + raw_len or len(digest) != 32:
            raise SystemExit("truncated frame")
        if hashlib.sha256(body).digest() != digest:
            raise SystemExit("frame digest mismatch")
        metadata = json.loads(body[:metadata_len])
        raw = body[metadata_len:]
        count += 1
        kinds[metadata.get("kind")] += 1
        if metadata.get("kind") == "transaction":
            first_transaction = first_transaction or metadata
            last_transaction = metadata
            if boundary is not None and first_transaction_at_or_after_boundary is None:
                received = datetime.fromisoformat(metadata["receivedAtUtc"].replace("Z", "+00:00"))
                if received >= boundary:
                    first_transaction_at_or_after_boundary = metadata
            if metadata.get("signature"):
                observed_signatures.add(metadata["signature"])
            transaction_shapes[(
                metadata.get("successful"),
                metadata.get("failed"),
                metadata.get("unresolved"),
                metadata.get("hasTradeEvent"),
            )] += 1
        if metadata.get("kind") == "rpc_account_snapshot":
            try:
                payload = json.loads(raw)
            except Exception as error:
                payload = {"decodeError": str(error)}
            rpc_results.append({
                "mint": metadata.get("accountPubkey"),
                "slot": metadata.get("slot"),
                "successful": metadata.get("successful"),
                "payload": payload,
            })
        if metadata.get("kind") == "rpc_finalized_transaction" and metadata.get("signature"):
            if metadata.get("finalized"):
                finalized_signatures.add(metadata["signature"])
        if len(samples) < 3 and metadata.get("kind") == "transaction":
            samples.append(metadata)
    print(json.dumps({
        "records": count,
        "kinds": kinds,
        "transactionShapes": [
            {"successful": key[0], "failed": key[1], "unresolved": key[2],
             "hasTradeEvent": key[3], "count": value}
            for key, value in transaction_shapes.items()
        ],
        "finalityJoin": {
            "observed": len(observed_signatures),
            "finalized": len(finalized_signatures),
            "pending": len(observed_signatures - finalized_signatures),
            "orphanFinality": len(finalized_signatures - observed_signatures),
        },
        "rpcSnapshots": rpc_results,
        "transactionSamples": samples,
        "firstTransaction": first_transaction,
        "lastTransaction": last_transaction,
        "firstTransactionAtOrAfterBoundary": first_transaction_at_or_after_boundary,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
