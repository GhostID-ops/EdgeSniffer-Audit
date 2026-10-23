#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import tempfile

import twoCqProspectiveSymmetricChoiceMaterializerV3 as v3


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        direct = root / "direct.jsonl"
        report = root / "calibration.json"
        direct.write_text(json.dumps({
            "signature": "sig", "detectedAtMs": 2_000_000,
            "sourceSlot": 10,
        }) + "\n")
        report.write_text(json.dumps({"clockDomain": {
            "sameHostSources": True, "hostBootId": "boot",
            "ntpSynchronized": True, "validExactSignatureAnchors": 25,
        }, "matchedRows": [{
            "signature": "anchor", "yellowstoneDetectedAtMs": 1_999_900,
            "yellowstoneMinusAllenharkMs": -12.5,
        }]}) + "\n")
        episode = {
            "signature": "sig", "slot": 10, "blockTimeMs": 1_999_000,
            "transactionIndex": 1,
        }
        recovered, evidence = v3.recover_yellowstone_candidates(
            direct, [episode], {}, 1_000_000, set(), report, root / "ledger.jsonl"
        )
        assert recovered["sig"]["source"] == v3.YELLOWSTONE_SOURCE
        assert recovered["sig"]["receivedAtUnixNs"] == 2_000_000_000_000
        assert recovered["sig"]["timestampInferred"] is False
        assert recovered["sig"]["calibrationAnchor"]["signature"] == "anchor"
        assert evidence["matches"] == 1

        cut = v3.NETCUP_CLOCK_EPOCH_START_MS
        old_anchors = [
            {"yellowstoneDetectedAtMs": cut - 10_000 + item,
             "yellowstoneMinusAllenharkMs": -10}
            for item in range(v3.CALIBRATION_MIN_EXACT_ANCHORS)
        ]
        new_anchors = [
            {"yellowstoneDetectedAtMs": cut + 10_000 + item,
             "yellowstoneMinusAllenharkMs": 10}
            for item in range(v3.CALIBRATION_MIN_EXACT_ANCHORS)
        ]
        ranges = v3.calibrated_yellowstone_ranges_ns({
            "clockDomain": {"sameHostSources": True, "hostBootId": "boot",
                            "ntpSynchronized": True},
            "matchedRows": old_anchors + new_anchors,
        })
        assert ranges == [
            ((cut - 10_000) * 1_000_000, cut * 1_000_000),
            (cut * 1_000_000, None),
        ]

        far = dict(episode, signature="far", slot=12)
        with direct.open("a") as stream:
            stream.write(json.dumps({
                "signature": "far", "detectedAtMs": 3_000_000,
                "sourceSlot": 12,
            }) + "\n")
        recovered, evidence = v3.recover_yellowstone_candidates(
            direct, [far], {}, 1_000_000, set(), report, root / "far-ledger.jsonl"
        )
        assert recovered["far"]["timestampInferred"] is False
        assert recovered["far"]["calibrationAnchor"]["clockDomainCalibrated"] is True
        assert v3.decision_generation_version(recovered["far"]) == v3.IMPLEMENTATION_VERSION
        assert v3.decision_generation_version({
            "source": v3.YELLOWSTONE_SOURCE, "calibrationAnchor": {"signature": "old"},
        }) == "3.0.0"

        allen = {"sig": {"source": "allenhark-subscribe-entries"}}
        recovered, evidence = v3.recover_yellowstone_candidates(
            direct, [episode], allen, 1_000_000, set(), report, root / "ledger.jsonl"
        )
        assert recovered == allen
        assert evidence["matches"] == 0

        bad = dict(episode, slot=11)
        recovered, evidence = v3.recover_yellowstone_candidates(
            direct, [bad], {}, 1_000_000, set(), report, root / "bad-ledger.jsonl"
        )
        assert "sig" not in recovered
        assert evidence["slotMismatches"] == 1

        state = v3.open_state(root / "state.sqlite3")
        hour = "2026/09/06/00"
        status_path = root / "exact" / "partitions" / hour / "reconciliation-status.json"
        status_path.parent.mkdir(parents=True)
        status_path.write_text(json.dumps({
            "reconciliationRetryExhausted": True,
            "manifestSha256": "manifest",
            "inputFingerprintSha256": "input",
            "coverage": {"unresolved": 1},
        }))
        v3.checkpoint(
            state,
            {"signature":"deferred","mint":"mint","route":"PUMP_CURVE",
             "blockTimeMs":1,"slot":2,"transactionIndex":3},
            "SOL_PUMP", "old-generation", "DEFERRED",
            ["EXACT_RECONCILIATION_NOT_READY"],
            {"hours":[{"hour":hour}]},
        )
        state.commit()
        assert v3.finalize_exhausted_deferred(state, root / "exact") == 1
        assert state.execute(
            "SELECT status FROM current_decisions WHERE signature='deferred'"
        ).fetchone()[0] == "TERMINAL"
        structural_hour = "2026/09/05/00"
        structural_path = root / "exact" / "partitions" / structural_hour / "reconciliation-status.json"
        structural_path.parent.mkdir(parents=True)
        structural_path.write_text(json.dumps({
            "temporalHourClosed": True, "inputStableDuringRun": True,
            "manifestSha256": "structural", "inputFingerprintSha256": "input-2",
            "coverage": {"decodeFailures": 1},
        }))
        v3.checkpoint(
            state,
            {"signature":"structural","mint":"mint-2","route":"PUMP_CURVE",
             "blockTimeMs":2,"slot":3,"transactionIndex":4},
            "SOL_PUMP", "old-structural", "DEFERRED",
            ["EXACT_RECONCILIATION_NOT_READY"],
            {"hours":[{"hour":structural_hour}]},
        )
        state.commit()
        assert v3.finalize_exhausted_deferred(state, root / "exact") == 1
        status, reasons = state.execute(
            "SELECT status,reasons_json FROM current_decisions WHERE signature='structural'"
        ).fetchone()
        assert status == "TERMINAL"
        assert "EXACT_RECONCILIATION_STRUCTURAL_EVIDENCE_INCOMPLETE" in reasons
        state.close()

    print("PASS")


if __name__ == "__main__":
    main()
