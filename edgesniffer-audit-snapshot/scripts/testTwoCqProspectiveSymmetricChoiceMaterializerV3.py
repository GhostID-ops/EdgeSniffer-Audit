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

    print("PASS")


if __name__ == "__main__":
    main()
