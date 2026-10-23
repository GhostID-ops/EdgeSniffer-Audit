#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import sqlite3
import tempfile

import publishTwoCqSealedValidationV1 as subject


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        state = root / "state.sqlite3"
        connection = sqlite3.connect(state)
        connection.execute(
            """CREATE TABLE current_decisions (
                 signature TEXT PRIMARY KEY, block_time_ms INTEGER, slot INTEGER,
                 transaction_index INTEGER, status TEXT, choice_json TEXT)"""
        )
        signatures = []
        for ordinal in range(1, 501):
            signature = f"sig-{ordinal:03d}"
            signatures.append(signature)
            payload = {
                "controlN": 1, "outcomesRead": 0,
                "states": [{"label": True}, {"label": False}],
            }
            connection.execute(
                "INSERT INTO current_decisions VALUES (?,?,?,?,?,?)",
                (signature, ordinal, ordinal, 0, "ONLINE_ELIGIBLE", json.dumps(payload)),
            )
        # Late evidence repair can legitimately recover pre-freeze rows. They
        # remain useful evidence but must never enter the subsequent N150.
        for ordinal in range(3):
            payload = {
                "controlN": 1, "outcomesRead": 0,
                "states": [{"label": True}, {"label": False}],
            }
            connection.execute(
                "INSERT INTO current_decisions VALUES (?,?,?,?,?,?)",
                (f"retro-{ordinal}", 349, 349, ordinal + 1,
                 "ONLINE_ELIGIBLE", json.dumps(payload)),
            )
        connection.commit()
        connection.close()
        status = root / "status.json"
        status.write_text(json.dumps({
            "validationSealed": True, "outcomesRead": 0,
            "validationRowsRead": 0, "onlineEligibleN": 503,
        }))
        development = root / "development.json"
        development.write_text(json.dumps({"signatures": signatures[:350]}))
        output = root / "sealed"
        freeze = subject.publish(argparse.Namespace(
            status=status, state_db=state, development_freeze=development, output=output,
        ))
        assert freeze["validationN"] == 150
        with gzip.open(output / "choice-sets.jsonl.gz", "rt") as stream:
            rows = [json.loads(line) for line in stream]
        assert [row["eligibleOrdinal"] for row in rows] == list(range(351, 501))
        assert all(row["split"] == "VALIDATION_SEALED" for row in rows)
        assert freeze["retroactiveEligibleExcludedFromValidationN"] == 3
        assert freeze["firstSignature"] == "sig-351"
        try:
            subject.publish(argparse.Namespace(
                status=status, state_db=state, development_freeze=development, output=output,
            ))
        except RuntimeError as error:
            assert str(error) == "SEALED_VALIDATION_ALREADY_PUBLISHED"
        else:
            raise AssertionError("publisher allowed a second publication")
    print("PASS")


if __name__ == "__main__":
    main()
