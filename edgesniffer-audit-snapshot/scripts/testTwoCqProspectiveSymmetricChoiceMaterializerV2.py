#!/usr/bin/env python3
"""Deterministic tests for the V2 online/forensic materializer split."""

from __future__ import annotations

import json
import pathlib
import tempfile

import twoCqDecisionSystemDecompositionV1 as decision
import twoCqProspectiveSymmetricChoiceMaterializerV1 as v1
import twoCqProspectiveSymmetricChoiceMaterializerV2 as subject
from exactCurveEconomicsV1 import CurveState


def event(mint: str, signature: str, slot: int, received_ns: int, user: str) -> decision.CausalEvent:
    pre = CurveState(30_000_000_000, 1_000_000_000_000, 0, 800_000_000_000)
    post = CurveState(31_000_000_000, 970_000_000_000, 1_000_000_000, 770_000_000_000)
    return decision.CausalEvent(
        mint=mint, signature=signature, slot=slot, transaction_index=0,
        instruction_index=0, received_ns=received_ns, is_buy=True,
        user=user, creator="creator", quote_lamports=1_000_000_000,
        token_raw=100, pre_state=pre, post_state=post,
    )


def selected() -> dict:
    return {
        "signature": "teacher-entry", "mint": "chosen", "route": "PUMP_BONDING_CURVE",
        "economicFamily": "SOL_PUMP_CURVE", "acquisitionClass": "NEW_FROM_FLAT",
        "entryReceivedAtUnixNs": 10_000_000_000, "entrySlot": 100,
        "entryTransactionIndex": 5, "teacherActionProof": {
            "canonicalFinalizedSuccess": True,
            "captureSource": "allenhark-subscribe-entries",
        },
    }


def main() -> None:
    assert subject.decision_generation_version("allenhark-subscribe-entries") == "2.1.0"
    assert subject.decision_generation_version(subject.CAPTURE_ALL_SOURCE) == "2.2.0"
    events = {
        "chosen": [event("chosen", "chosen-prior", 99, 9_000_000_000, "buyer-a")],
        "control": [event("control", "control-prior", 98, 8_500_000_000, "buyer-b")],
    }
    creates = {
        "chosen": {"receivedNs": 1_000_000_000, "order": (90, 0, -1, "create-a")},
        "control": {"receivedNs": 2_000_000_000, "order": (91, 0, -1, "create-b")},
    }
    row, reasons, detail = subject.build_online_choice(selected(), events, creates, {})
    assert row is not None and reasons == []
    assert row["controlN"] == 1 and len(row["states"]) == 2
    assert detail["selectedExclusionReasons"] == []
    # V1 requires a redundant compact-observer copy of the teacher action.
    strict, strict_reasons, _ = v1.build_choice(selected(), events, creates, {})
    assert strict is None and strict_reasons == ["SELECTED_ENTRY_EVENT_NOT_EXACTLY_JOINED"]

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        compact = root / "arrivals.bin"
        signature = "5as4akCwtKPfDDnMd5ihi26WPaSYR1y3k5ABJgpB6pBkeuDhu8G6p6gxv2L3n9Vx73rDV4xonzxffGQ3cCtx6Vfk"
        compact.write_bytes(
            v1.COMPACT_MAGIC
            + v1.COMPACT_RECORD.pack(
                subject.tier_a.signature_digest(signature), 10_250_000_000, 100
            )
        )
        recovered, recovery_evidence = subject.recover_capture_all_candidates(
            compact,
            [{
                "signature": signature, "slot": 100, "blockTimeMs": 9_000,
            }],
            {},
            8_000,
        )
        assert recovered[signature]["source"] == subject.CAPTURE_ALL_SOURCE
        assert recovered[signature]["receivedAtUnixNs"] == 10_250_000_000
        assert recovery_evidence["captureAllJoinMatches"] == 1
        frozen, frozen_evidence = subject.recover_capture_all_candidates(
            compact,
            [{
                "signature": signature, "slot": 100, "blockTimeMs": 9_000,
            }],
            {},
            9_000,
        )
        assert frozen == {} and frozen_evidence["captureAllJoinTargets"] == 0

        exact = root / "exact"
        hour = exact / "partitions" / "1970" / "01" / "01" / "00"
        hour.mkdir(parents=True)
        evidence = hour / "evidence.frames.zst"
        evidence.write_bytes(b"evidence")
        status = {
            "temporalHourClosed": True,
            "inputStableDuringRun": True,
            "exactReplayComplete": False,
            "inputFingerprintSha256": "a",
            "manifestSha256": "b",
            "joinedEvidenceIdsSha256": "c",
            "reconciliationVersion": "test",
            "inputEvidence": [{"path": "partitions/1970/01/01/00/evidence.frames.zst", "sha256": "d"}],
            "coverage": {"missingFinality": 1},
        }
        (hour / "reconciliation-status.json").write_text(json.dumps(status), encoding="utf-8")
        try:
            subject.exact_evidence_snapshot(exact, 120_000_000_000)
        except subject.EvidenceNotReady as error:
            assert error.hours and error.hours[0]["reason"] == "EXACT_REPLAY_INCOMPLETE"
        else:
            raise AssertionError("inexact reconciliation must defer")

        status["exactReplayComplete"] = True
        status["coverage"] = {}
        (hour / "reconciliation-status.json").write_text(json.dumps(status), encoding="utf-8")
        paths, generation, summaries = subject.exact_evidence_snapshot(exact, 120_000_000_000)
        assert paths == [evidence] and len(generation) == 64 and len(summaries) == 1

        connection = subject.open_state(root / "state.sqlite3")
        frontier = subject.refresh_partition_generations(connection, exact)
        assert frontier["exactCompletePartitionN"] == 1
        assert frontier["latestExactCompleteHour"] == "1970/01/01/00"
        indexed = connection.execute(
            "SELECT exact_complete FROM partition_generations WHERE partition_hour=?",
            ("1970/01/01/00",),
        ).fetchone()
        assert indexed == (1,)
        episode = {
            "signature": "teacher-entry", "mint": "chosen", "route": "PUMP_BONDING_CURVE",
            "classification": "NEW_FROM_FLAT", "blockTimeMs": 1, "slot": 100,
            "transactionIndex": 5,
        }
        subject.checkpoint(
            connection, episode, "SOL_PUMP_CURVE", "generation-a", "TERMINAL",
            ["OLD_INCOMPLETE_REASON"], {},
        )
        subject.checkpoint(
            connection, episode, "SOL_PUMP_CURVE", "generation-b", "ONLINE_ELIGIBLE",
            [], {}, row, {"eligible": False},
        )
        connection.commit()
        current = connection.execute(
            "SELECT generation_sha,status FROM current_decisions WHERE signature='teacher-entry'"
        ).fetchone()
        versions = connection.execute(
            "SELECT COUNT(*) FROM decision_versions WHERE signature='teacher-entry'"
        ).fetchone()[0]
        assert current == ("generation-b", "ONLINE_ELIGIBLE") and versions == 2
        connection.close()

    subject.v1.assert_outcome_blind(row)
    print("PASS: V2 defers inexact evidence, supersedes by generation, and separates online from forensic")


if __name__ == "__main__":
    main()
