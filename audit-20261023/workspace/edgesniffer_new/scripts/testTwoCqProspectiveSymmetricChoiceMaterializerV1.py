#!/usr/bin/env python3
"""Deterministic semantic tests for the symmetric choice materializer."""

from __future__ import annotations

import json
import os
import pathlib
import struct
import tempfile

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqProspectiveSymmetricChoiceMaterializerV1 as subject
from exactCurveEconomicsV1 import CurveState


def event(
    mint: str, signature: str, slot: int, received_ns: int, is_buy: bool,
    user: str, pre: CurveState, post: CurveState, transaction_index: int = 0,
) -> decision.CausalEvent:
    return decision.CausalEvent(
        mint=mint, signature=signature, slot=slot, transaction_index=transaction_index,
        instruction_index=0, received_ns=received_ns, is_buy=is_buy,
        user=user, creator="creator", quote_lamports=1_000_000_000,
        token_raw=100, pre_state=pre, post_state=post,
    )


def selected() -> dict:
    return {
        "signature": "teacher-entry", "mint": "chosen", "route": "PUMP_BONDING_CURVE",
        "acquisitionClass": "NEW_FROM_FLAT", "entryReceivedAtUnixNs": 10_000_000_000,
        "entrySlot": 100, "entryTransactionIndex": 5, "tierAEvidence": {},
    }


def main() -> None:
    boundaries = [1_000_000_000_000]
    assert subject.inside_expanded_decision_window(800_000_000_000, boundaries)
    assert not subject.inside_expanded_decision_window(500_000_000_000, boundaries)

    with tempfile.TemporaryDirectory() as raw:
        journal = pathlib.Path(raw) / "arrivals.bin"
        wanted = bytes.fromhex("11" * 32)
        other = bytes.fromhex("22" * 32)
        with journal.open("wb") as stream:
            stream.write(subject.COMPACT_MAGIC)
            for digest, received_ns, slot in (
                (other, 1, 90), (wanted, 2, 100), (other, 3, 101), (wanted, 4, 110),
            ):
                stream.write(struct.pack("<32sqQ", digest, received_ns, slot))
        arrivals, evidence = subject.targeted_compact_arrivals_by_slot(
            journal, {wanted}, 95, 105
        )
        assert arrivals == {wanted: (2, 100)}
        assert evidence["compactTargetMatches"] == 1

    a = CurveState(30_000_000_000, 1_000_000_000_000, 0, 800_000_000_000)
    b = CurveState(31_000_000_000, 970_000_000_000, 1_000_000_000, 770_000_000_000)
    c = CurveState(32_000_000_000, 940_000_000_000, 2_000_000_000, 740_000_000_000)
    events = {
        "chosen": [
            event("chosen", "chosen-external", 99, 9_000_000_000, True, "buyer-a", a, b),
            # The teacher's own transaction is present in canonical evidence but
            # must never enter its pre-decision features.
            event("chosen", "teacher-entry", 100, 10_000_000_000, True, decision.WALLET, b, c, 5),
        ],
        "control": [event("control", "control-external", 98, 8_500_000_000, True, "buyer-b", a, c)],
    }
    creates = {
        "chosen": {"receivedNs": 1_000_000_000, "order": (90, 0, -1, "create-a")},
        "control": {"receivedNs": 2_000_000_000, "order": (91, 0, -1, "create-b")},
    }
    row, reasons, detail = subject.build_choice(selected(), events, creates, {})
    assert not reasons and row is not None
    assert row["controlN"] == 1 and len(row["states"]) == 2
    assert sum(bool(state["label"]) for state in row["states"]) == 1
    assert all(set(state["features"]) == set(timing.FULL_FEATURES) for state in row["states"])
    subject.assert_outcome_blind(row)

    # A missing causal transition removes a control under the same evidence
    # rule and therefore makes the decision ineligible, rather than backfilling.
    incomplete = {"control": [((95, 0, -1, "missing"), "ALLENHARK_TRADE_ARRIVAL_MISSING")]}
    missing_row, missing_reasons, _ = subject.build_choice(selected(), events, creates, incomplete)
    assert missing_row is None
    assert "NO_CONTEMPORANEOUS_ALTERNATIVE" in missing_reasons

    no_entry_row, no_entry_reasons, no_entry_detail = subject.build_choice(
        selected(), {"control": events["control"]}, creates,
        {"chosen": [((100, 5, -1, "teacher-entry"), "TRADE_LOG_DECODE_FAILED")]},
    )
    assert no_entry_row is None
    assert no_entry_reasons == ["SELECTED_ENTRY_EVENT_NOT_EXACTLY_JOINED"]
    assert no_entry_detail["selectedEntryEvidenceFailures"] == ["TRADE_LOG_DECODE_FAILED"]

    # Reserve discontinuity is fail-closed.
    events["control"].append(event("control", "bad", 99, 9_100_000_000, True, "buyer-c", a, b))
    bad_row, bad_reasons, bad_detail = subject.build_choice(selected(), events, creates, {})
    assert bad_row is None
    assert "NO_CONTEMPORANEOUS_ALTERNATIVE" in bad_reasons
    assert bad_detail["candidateExclusions"]["RESERVE_CONTINUITY_INCOMPLETE"] == 1

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        contract = root / "source-contract.json"
        contract.write_text(json.dumps({
            "schema": subject.CONTRACT_SCHEMA,
            "cohortStartUtc": "2026-09-11T00:00:00Z",
            "choiceUniverse": {"selectedMintAndControlsUseIdenticalEvidenceRules": True},
            "integrity": {"outcomesPermitted": False},
        }), encoding="utf-8")
        args = type("Args", (), {"contract": contract, "output": root / "out"})()
        _, state = subject.initialize(args)
        assert (args.output / "validation-sealed" / "decisions").exists()
        if os.name != "nt":
            assert (args.output / "validation-sealed").stat().st_mode & 0o777 == 0o700
        first = row | {"signature": "teacher-entry", "episodeId": "teacher-entry"}
        subject.checkpoint_decision(args.output, state, "teacher-entry", first, {
            "terminal": False, "reasons": [], "atUtc": subject.now_utc(),
        })
        # Replaying the same decision is idempotent.
        subject.checkpoint_decision(args.output, state, "teacher-entry", first, {
            "terminal": False, "reasons": [], "atUtc": subject.now_utc(),
        })
        recovered = subject.recover_eligible_index(args.output, state)
        assert len(recovered) == 1 and recovered["teacher-entry"]["ordinal"] == 1
        assert "teacher-entry" not in state["deferredStatuses"]
        assert len(list((args.output / "development" / "decisions").glob("*.json"))) == 1

        contaminated = dict(first)
        contaminated["pnlSol"] = 1.0
        try:
            subject.assert_outcome_blind(contaminated)
        except RuntimeError as error:
            assert str(error).startswith("OUTCOME_FIELD_FORBIDDEN")
        else:
            raise AssertionError("outcome-bearing data must fail closed")

    print("PASS: symmetric materializer is causal, symmetric, sealed, and idempotent")


if __name__ == "__main__":
    main()
