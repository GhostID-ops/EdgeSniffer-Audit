#!/usr/bin/env python3
"""Deterministic tests for the rebuildable 2CQ causal state plane."""

from __future__ import annotations

import json
import pathlib
import tempfile

import twoCqIndexedCausalStatePlaneV1 as subject
import twoCqProspectiveSymmetricChoiceMaterializerV1 as legacy
from reconcilePumpExactEvidenceLedgerV1 import MAGIC


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        create_signature = "1" * 88
        trade_signature = "2" * 88
        empty_evidence = root / "evidence.frames.partial"
        empty_evidence.write_bytes(MAGIC)
        connection = subject.open_state(root / "state.sqlite3")
        changed, _ = subject.import_evidence_file(connection, empty_evidence, "evidence-sha")
        reused, _ = subject.import_evidence_file(connection, empty_evidence, "evidence-sha")
        assert changed is True and reused is False

        for signature, block_time, slot, hints in (
            (create_signature, 1, 90, ["mint"]),
            (trade_signature, 9, 99, ["mint"]),
        ):
            connection.execute(
                "INSERT INTO transactions VALUES (?,?,?,?,?,?)",
                (signature, block_time, slot, 0, json.dumps(["BUY"]), json.dumps(hints)),
            )
        connection.execute("INSERT INTO create_events VALUES (?,?)", (create_signature, "mint"))
        connection.execute(
            "INSERT INTO causal_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade_signature, 0, "mint", 1, "buyer", "creator", 1_000_000_000, 100,
                30_000_000_000, 1_000_000_000_000, 0, 800_000_000_000,
                31_000_000_000, 970_000_000_000, 1_000_000_000, 770_000_000_000,
            ),
        )
        connection.commit()

        compact = root / "arrivals.bin"
        compact.write_bytes(
            legacy.COMPACT_MAGIC
            + legacy.COMPACT_RECORD.pack(
                legacy.tier_a.signature_digest(create_signature), 1_100_000_000, 90
            )
            + legacy.COMPACT_RECORD.pack(
                legacy.tier_a.signature_digest(trade_signature), 9_100_000_000, 99
            )
        )
        events, creates, incomplete, evidence = subject.materialize_universe(
            connection, compact, [10_000_000_000]
        )
        assert len(events["mint"]) == 1
        assert events["mint"][0].received_ns == 9_100_000_000
        assert creates["mint"]["receivedNs"] == 1_100_000_000
        assert incomplete == {}
        assert evidence["indexedCanonicalSuccessfulPumpTransactions"] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        connection.close()
    print("PASS: causal state plane imports immutable evidence once and queries causal state")


if __name__ == "__main__":
    main()
