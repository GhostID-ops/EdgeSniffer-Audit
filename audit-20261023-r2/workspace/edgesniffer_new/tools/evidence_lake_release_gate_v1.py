#!/usr/bin/env python3
"""Fail-closed preflight for releasing a locally redundant evidence path.

The tool intentionally never deletes, moves, or edits a target.  It emits a
machine-readable PASS only when an exact archive receipt is cataloged and no
active experiment pin covers the path.  A verified archiver can use its output
as one mandatory prerequisite; it must still perform its own source/archive
byte verification immediately before release.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def covered(candidate: Path, protected: Path) -> bool:
    try:
        candidate.relative_to(protected)
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    candidate = Path(args.path).absolute()
    receipt = str(Path(args.receipt).absolute())
    try:
        with sqlite3.connect(args.db) as conn:
            receipt_row = conn.execute("SELECT receipt_sha256 FROM receipts WHERE receipt_path=?", (receipt,)).fetchone()
            pins = conn.execute("SELECT experiment_id, path, pin_type, reason FROM pins").fetchall()
    except sqlite3.Error as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "reason": f"catalog-error: {exc}"}))
        return 2
    blockers = [{"experiment": exp, "pin": path, "type": typ, "reason": why}
                for exp, path, typ, why in pins if covered(candidate, Path(path)) or covered(Path(path), candidate)]
    if not receipt_row:
        print(json.dumps({"status": "FAIL_CLOSED", "path": str(candidate),
                          "reason": "archive-receipt-not-cataloged"}, sort_keys=True))
        return 2
    if blockers:
        print(json.dumps({"status": "FAIL_CLOSED", "path": str(candidate),
                          "reason": "active-pin-overlap", "blockers": blockers}, sort_keys=True))
        return 2
    print(json.dumps({"status": "PREFLIGHT_PASS", "path": str(candidate), "receipt": receipt,
                      "receipt_sha256": receipt_row[0],
                      "required_before_release": [
                          "fresh source stability check",
                          "full archive inventory equality",
                          "full raw-byte sha256 readback equality",
                          "immutable release receipt"
                      ]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
