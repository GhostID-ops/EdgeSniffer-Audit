#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile

import twoCqProspectiveSymmetricChoiceMaterializerV3 as v3


def expect_runtime_error(callable_value, expected: str) -> None:
    try:
        callable_value()
    except RuntimeError as error:
        assert expected in str(error), (expected, error)
    else:
        raise AssertionError(f"expected RuntimeError containing {expected}")


def database_snapshot(connection: sqlite3.Connection) -> list[str]:
    return list(connection.iterdump())


def choice(signature: str) -> dict[str, object]:
    return {
        "schema": "TEST_OUTCOME_BLIND_CHOICE",
        "signature": signature,
        "states": [{"mint": f"mint-{signature}", "selected": True}],
        "outcomesRead": 0,
        "submissions": 0,
    }


def checkpoint_online(
    connection: sqlite3.Connection, signature: str, ordinal: int,
) -> None:
    v3.checkpoint(
        connection,
        {
            "signature": signature,
            "mint": f"mint-{signature}",
            "route": "PUMP_BONDING_CURVE",
            "blockTimeMs": ordinal,
            "slot": ordinal,
            "transactionIndex": ordinal,
        },
        "SOL_PUMP_CURVE",
        f"generation-{ordinal}",
        "ONLINE_ELIGIBLE",
        [],
        {"test": True},
        choice(signature) | {
            "decisionBoundary": {
                "arrivalUnixNs": ordinal * 1_000,
                "slot": ordinal,
                "transactionIndex": ordinal,
            },
        },
    )


def test_canonical_ordering() -> None:
    assert v3.canonical_order_before((10, 2, 0, "create"), (10, 4, -1, "")) is True
    assert v3.canonical_order_before((10, 5, 0, "create"), (10, 4, -1, "")) is False
    assert v3.canonical_order_before((9, -1, 0, "create"), (10, 4, -1, "")) is True
    assert v3.canonical_order_before((10, -1, 0, "create"), (10, 4, -1, "")) is None
    assert v3.canonical_order_before((10, 2, 0, "create"), (10, -1, -1, "")) is None

    selected = {
        "entryReceivedAtUnixNs": 1_000,
        "entrySlot": 10,
        "entryTransactionIndex": None,
        "mint": "mint",
    }
    expect_runtime_error(
        lambda: v3.build_online_choice(selected, {}, {}, {}),
        "CANONICAL_TRANSACTION_ORDER_UNAVAILABLE",
    )

    selected["entryTransactionIndex"] = 4
    row, reasons, _ = v3.build_online_choice(
        selected,
        {},
        {"mint": {"receivedNs": 900, "order": (10, -1, 0, "create")}},
        {},
    )
    assert row is None
    assert "CANONICAL_TRANSACTION_ORDER_UNAVAILABLE" in reasons


def test_exact_index_reconstruction(root: pathlib.Path) -> None:
    state_plane = root / "state-plane.sqlite3"
    connection = sqlite3.connect(state_plane)
    connection.execute(
        "CREATE TABLE transactions(signature TEXT PRIMARY KEY, slot INTEGER, tx_index INTEGER)"
    )
    connection.executemany(
        "INSERT INTO transactions VALUES (?,?,?)",
        [("exact", 10, 7), ("mismatch", 99, 8), ("negative", 12, -1)],
    )
    connection.commit()
    connection.close()

    rows = [
        {"signature": "exact", "slot": 10},
        {"signature": "missing", "slot": 11},
        {"signature": "mismatch", "slot": 13},
        {"signature": "negative", "slot": 12},
    ]
    evidence = v3.enrich_canonical_transaction_order(rows, state_plane)
    assert rows[0]["transactionIndex"] == 7
    assert rows[0]["transactionIndexSource"] == "EXACT_CAUSAL_STATE_PLANE_V1"
    assert "transactionIndex" not in rows[1]
    assert "transactionIndex" not in rows[2]
    assert "transactionIndex" not in rows[3]
    assert evidence == {
        "rows": 4,
        "exactMatches": 1,
        "indexesAdded": 1,
        "indexesCorrected": 0,
        "slotMismatches": 1,
        "unresolved": 2,
    }
    assert all(row.get("transactionIndex") != -1 for row in rows)


def test_scientific_generation_identity() -> None:
    base = {
        "hour": "2026/09/17/01",
        "temporalHourClosed": True,
        "inputStableDuringRun": True,
        "exactReplayComplete": True,
        "inputFingerprintSha256": "input",
        "manifestSha256": "manifest",
        "joinedEvidenceIdsSha256": "joined",
        "reconciliationVersion": "v1",
        "inputEvidence": [{"path": "evidence.jsonl", "sha256": "evidence"}],
    }
    without_retry = v3.scientific_reconciliation_generation([base])
    with_retry_false = v3.scientific_reconciliation_generation([
        base | {"reconciliationRetryExhausted": False}
    ])
    with_retry_true = v3.scientific_reconciliation_generation([
        base | {"reconciliationRetryExhausted": True}
    ])
    assert without_retry == with_retry_false == with_retry_true
    changed = v3.scientific_reconciliation_generation([
        base | {"joinedEvidenceIdsSha256": "changed"}
    ])
    assert changed != without_retry


def test_scientific_online_compatibility(root: pathlib.Path) -> None:
    connection = v3.open_state(root / "compatibility.sqlite3")
    statuses = [{
        "hour": "2026/09/17/01",
        "temporalHourClosed": True,
        "inputStableDuringRun": True,
        "exactReplayComplete": True,
        "inputFingerprintSha256": "input",
        "manifestSha256": "manifest",
        "joinedEvidenceIdsSha256": "joined",
        "reconciliationVersion": "v1",
        "inputEvidence": [{"path": "evidence", "sha256": "hash"}],
    }]
    candidate = {
        "receivedAtUnixNs": 123_456,
        "slot": 99,
        "source": "allenhark-subscribe-entries",
    }
    compatible_choice = choice("compatible") | {
        "decisionBoundary": {"arrivalUnixNs": 123_456, "slot": 99},
        "teacherActionProof": {
            "captureSource": "allenhark-subscribe-entries",
            "sameSignatureClockCalibration": None,
        },
    }
    v3.checkpoint(
        connection,
        {
            "signature": "compatible",
            "mint": "mint-compatible",
            "route": "PUMP_BONDING_CURVE",
            "blockTimeMs": 1,
            "slot": 99,
            "transactionIndex": -1,
        },
        "SOL_PUMP_CURVE",
        "legacy-generation",
        "ONLINE_ELIGIBLE",
        [],
        {"reconciliation": statuses},
        compatible_choice,
    )
    connection.commit()
    assert v3.current_online_scientifically_equivalent(
        connection,
        "compatible",
        [statuses[0] | {"reconciliationRetryExhausted": False}],
        candidate,
    )
    assert not v3.current_online_scientifically_equivalent(
        connection,
        "compatible",
        [statuses[0] | {"joinedEvidenceIdsSha256": "changed"}],
        candidate,
    )
    assert not v3.current_online_scientifically_equivalent(
        connection,
        "compatible",
        statuses,
        candidate | {"receivedAtUnixNs": 123_457},
    )
    connection.close()


def test_frozen_guard_and_publication(root: pathlib.Path) -> None:
    original_development_n = v3.DEVELOPMENT_N
    v3.DEVELOPMENT_N = 2
    try:
        output = root / "frozen-output"
        output.mkdir()
        (output / "development").mkdir()
        connection = v3.open_state(output / "causal-state-v2.sqlite3")
        checkpoint_online(connection, "sig-a", 1)
        checkpoint_online(connection, "sig-b", 2)
        connection.commit()
        v3.publish_development(connection, output)
        assert v3.frozen_development_signatures(output) == {"sig-a", "sig-b"}
        connection.execute(
            "UPDATE current_decisions SET transaction_index=-1 WHERE signature IN ('sig-a','sig-b')"
        )
        connection.commit()
        signature_rows = [
            {"signature": "sig-a", "slot": 1},
            {"signature": "sig-b", "slot": 2},
        ]
        original_rpc = v3.capture.denominator.rpc
        v3.capture.denominator.rpc = lambda _url, method, params: {
            "signatures": (
                ["unrelated", "sig-a"] if int(params[0]) == 1 else ["sig-b"]
            )
        } if method == "getBlock" else None
        try:
            recovered = v3.recover_missing_frozen_order_from_finalized_blocks(
                connection, signature_rows, output, ["https://unused.invalid"]
            )
        finally:
            v3.capture.denominator.rpc = original_rpc
        assert recovered["legacyFrozenTargets"] == 2
        assert recovered["recovered"] == 2
        assert signature_rows[0]["transactionIndex"] == 1
        assert signature_rows[1]["transactionIndex"] == 0
        connection.execute(
            "UPDATE current_decisions SET transaction_index=1 WHERE signature='sig-a'"
        )
        connection.execute(
            "UPDATE current_decisions SET transaction_index=2 WHERE signature='sig-b'"
        )
        connection.commit()
        connection.execute(
            "UPDATE current_decisions SET choice_json=? WHERE signature='sig-b'",
            (json.dumps(choice("sig-b") | {"preexistingDrift": True}),),
        )
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        assert v3.restore_frozen_choice_payloads(connection, output) == 1
        assert v3.validate_frozen_development(connection, output) is False
        connection.commit()
        frozen_snapshot = database_snapshot(connection)
        status_path = output / "status.json"
        status_path.write_text('{"generation":"old"}\n', encoding="utf-8")
        prior_status = status_path.read_bytes()

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE current_decisions SET choice_json=? WHERE signature='sig-a'",
            (json.dumps(choice("sig-a") | {"changed": True}, sort_keys=True),),
        )
        expect_runtime_error(
            lambda: v3.finalize_materializer_run(
                connection, output, {"generation": "new"}, output / "ledger.gz", None, None
            ),
            "FROZEN_DEVELOPMENT_PAYLOAD_CHANGED",
        )
        assert database_snapshot(connection) == frozen_snapshot
        assert status_path.read_bytes() == prior_status

        connection.execute("BEGIN IMMEDIATE")
        checkpoint_online(connection, "sig-earlier-late-recovery", 0)
        v3.finalize_materializer_run(
            connection,
            output,
            {"generation": "late-recovery", "outcomesRead": 0, "submissions": 0},
            output / "ledger.gz",
            None,
            None,
        )
        assert json.loads(
            (output / "development" / "freeze.json").read_text(encoding="utf-8")
        )["signatures"] == ["sig-a", "sig-b"]
        connection.execute("DELETE FROM current_decisions WHERE signature='sig-earlier-late-recovery'")
        connection.execute("DELETE FROM decision_versions WHERE signature='sig-earlier-late-recovery'")
        connection.commit()
        frozen_snapshot = database_snapshot(connection)
        prior_status = status_path.read_bytes()

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE current_decisions SET status='TERMINAL' WHERE signature='sig-a'"
        )
        expect_runtime_error(
            lambda: v3.finalize_materializer_run(
                connection, output, {"generation": "new"}, output / "ledger.gz", None, None
            ),
            "FROZEN_DEVELOPMENT_MEMBER_NOT_ELIGIBLE",
        )
        assert database_snapshot(connection) == frozen_snapshot
        assert status_path.read_bytes() == prior_status

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('testSuccessfulCommit','yes')"
        )
        final_status = {
            "generation": "committed",
            "validationSealed": True,
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "submissions": 0,
        }
        v3.finalize_materializer_run(
            connection, output, final_status, output / "ledger.gz", None, None
        )
        assert json.loads(status_path.read_text(encoding="utf-8")) == final_status
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='testSuccessfulCommit'"
        ).fetchone()[0] == "yes"
        freeze = json.loads(
            (output / "development" / "freeze.json").read_text(encoding="utf-8")
        )
        assert freeze["outcomesRead"] == 0
        assert freeze["validationRowsRead"] == 0
        connection.close()
    finally:
        v3.DEVELOPMENT_N = original_development_n


def main() -> None:
    test_canonical_ordering()
    test_scientific_generation_identity()
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        test_exact_index_reconstruction(root)
        test_scientific_online_compatibility(root)
        test_frozen_guard_and_publication(root)
    print("PASS")


if __name__ == "__main__":
    main()
