#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


PATH = Path(__file__).with_name("kefi_choiceset_materializer_v1.py")
SPEC = importlib.util.spec_from_file_location("choiceset", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def event(frame: str, index: int, sequence: int, pool: str, mint: str, timestamp: int) -> dict:
    return {"source_frame_sha256": frame, "event_index": index, "received_at_unix_ns": timestamp,
            "source_instance_id": "source-a", "continuity_epoch": 1, "durable_output_sequence": sequence,
            "pool": pool, "mint": mint, "side": "BUY", "pool_quote_raw": 100, "pool_base_raw": 200,
            "layout": "PUMPSWAP_SUPPORTED", "fee_tuple": [20, 5]}


def write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def manifest(path: Path, events: Path, *, selected: bool = False) -> None:
    path.write_text(json.dumps({"schema": "KEFI_CHOICESET_CANONICAL_INPUT_V1", "teacher_independent": True,
                                "selected_centred": selected, "all_supported_pools_covered": not selected,
                                "events_path": str(events.resolve()), "events_sha256": MODULE.sha256(events),
                                "source_instance": "source-a", "continuity_epoch": 1}), encoding="utf-8")


def run(root: Path, suffix: str, rows: list[dict]) -> bytes:
    events, teachers, source = root / f"events-{suffix}.jsonl", root / f"teachers-{suffix}.jsonl", root / f"source-{suffix}.json"
    write(events, rows); write(teachers, [{**rows[2], "teacher": True}]); manifest(source, events)
    subprocess.run([sys.executable, str(PATH), "--canonical-events", str(events), "--canonical-manifest", str(source),
                    "--teacher-events", str(teachers), "--out-root", str(root / f"out-{suffix}")], check=True, capture_output=True)
    return (root / f"out-{suffix}" / "choice-sets.jsonl").read_bytes()


def main() -> None:
    rows = [event("f1", 0, 1, "p1", "m1", 10), event("f2", 0, 2, "p2", "m2", 20),
            event("f3", 0, 3, "p1", "m1", 30), event("future", 0, 4, "p3", "m3", 40)]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        first = run(root, "first", rows)
        second = run(root, "second", rows)
        assert first == second
        result = json.loads(first.decode().strip())
        assert result["status"] == "VALID" and len(result["ALL_ELIGIBLE_POOLS"]) == 2
        assert {row["mint"] for row in result["ALL_ELIGIBLE_POOLS"]} == {"m1", "m2"}
        # A future event cannot alter the earlier snapshot.
        altered = [*rows[:3], {**rows[3], "pool_quote_raw": 999999}]
        assert run(root, "future", altered) == first
        # The teacher transaction itself is applied only after its snapshot.
        changed_teacher = [rows[0], rows[1], {**rows[2], "pool_quote_raw": 999999}, rows[3]]
        assert run(root, "teacher-self", changed_teacher) == first
        events = root / "bad.jsonl"; write(events, rows); bad = root / "bad.json"; manifest(bad, events, selected=True)
        teachers = root / "bad-teachers.jsonl"; write(teachers, [{**rows[2], "teacher": True}])
        completed = subprocess.run([sys.executable, str(PATH), "--canonical-events", str(events), "--canonical-manifest", str(bad),
                                    "--teacher-events", str(teachers), "--out-root", str(root / "bad-out")], capture_output=True, text=True)
        assert completed.returncode != 0 and "selected-centred source rejected" in completed.stderr
    print("PASS")


if __name__ == "__main__":
    main()
