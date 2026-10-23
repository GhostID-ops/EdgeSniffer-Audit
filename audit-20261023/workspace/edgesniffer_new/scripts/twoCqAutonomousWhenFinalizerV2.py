#!/usr/bin/env python3
"""Independent finalized-slot worker for AUTONOMOUS_WHEN_V2 rank snapshots."""
from __future__ import annotations

import argparse
import fcntl
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import twoCqAutonomousWhenV2 as lane


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def write_status(root: pathlib.Path, db, source_health: dict, processed: int) -> None:
    state = {
        'schema': '2CQ_AUTONOMOUS_WHEN_V2_FINALIZER_HEALTH',
        'updatedAtUtc': now(),
        'processedSlotsLastCycle': processed,
        'finalizedSlotFrontier': db.execute(
            'SELECT max(slot) FROM slot_queue WHERE finality="FINAL"'
        ).fetchone()[0],
        'pendingSlots': db.execute(
            'SELECT count(*) FROM slot_queue WHERE finality="PENDING"'
        ).fetchone()[0],
        'teacherLabelsFinal': db.execute(
            'SELECT count(*) FROM teacher_labels WHERE finality="FINAL"'
        ).fetchone()[0],
        'teacherLabelsPending': db.execute(
            'SELECT count(*) FROM teacher_labels WHERE finality="PENDING"'
        ).fetchone()[0],
        'sourceLatestSlot': source_health['latestSlot'],
        'sourceContinuityGaps': source_health['continuityGaps'],
        'sourceReconnects': source_health['reconnects'],
    }
    target = root / 'finalizer-health.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, sort_keys=True, indent=2) + '\n')
    temporary.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=pathlib.Path, required=True)
    parser.add_argument('--env', type=pathlib.Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    lock_path = args.root / 'finalizer.lock'
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('FINALIZER_LOCK_HELD') from exc
        db = lane.open_db(args.root / 'autonomous-when-v2.sqlite3')
        source_health_path = args.root.parent / 'health.json'
        url = lane.env(args.env, 'SECURE_RPC_URL')
        try:
            while True:
                source_health = json.loads(source_health_path.read_text())
                boundary=json.loads((args.root/'prospective-start-receipt.json').read_text())
                if lane.source_continuity_changed(source_health,boundary):
                    raise RuntimeError('SOURCE_CONTINUITY_UNCERTAIN')
                processed = lane.finalize(db, url, int(source_health['latestSlot']))
                write_status(args.root, db, source_health, processed)
                if args.once:
                    return
                time.sleep(0.1 if processed else 1.0)
        finally:
            db.close()


if __name__ == '__main__':
    main()
