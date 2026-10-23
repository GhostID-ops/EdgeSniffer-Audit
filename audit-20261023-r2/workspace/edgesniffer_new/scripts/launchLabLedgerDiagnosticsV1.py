#!/usr/bin/env python3
"""Print compact, read-only integrity diagnostics for the LaunchLab ledger."""

import argparse
import collections
import json
import pathlib
import sqlite3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=pathlib.Path)
    args = parser.parse_args()
    connection = sqlite3.connect(args.database)
    errors = collections.Counter(
        str(row[0]) for row in connection.execute(
            "SELECT error FROM arrivals WHERE error IS NOT NULL"
        )
    )
    print(json.dumps({"errors": errors}, sort_keys=True))
    previous: dict[str, tuple[str, str, int]] = {}
    gaps = []
    rows = connection.execute(
        "SELECT signature,slot,canonical_tx_index,pool_state,side,pre_state_json,post_state_json "
        "FROM events ORDER BY pool_state,slot,canonical_tx_index,event_index"
    )
    for signature, slot, tx_index, pool, side, pre, post in rows:
        if pool in previous and previous[pool][0] != pre:
            prior_state = json.loads(previous[pool][0])
            current_state = json.loads(pre)
            gaps.append({
                "pool": pool,
                "previousSignature": previous[pool][1],
                "previousSlot": previous[pool][2],
                "signature": signature,
                "slot": slot,
                "txIndex": tx_index,
                "side": side,
                "diff": {
                    key: [prior_state.get(key), current_state.get(key)]
                    for key in sorted(set(prior_state) | set(current_state))
                    if prior_state.get(key) != current_state.get(key)
                },
            })
        previous[pool] = (post, signature, slot)
    print(json.dumps({"continuityGaps": gaps}, sort_keys=True))


if __name__ == "__main__":
    main()
