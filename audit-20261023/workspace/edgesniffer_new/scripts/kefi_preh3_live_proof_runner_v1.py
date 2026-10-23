#!/usr/bin/env python3
"""One-shot, isolated proof that post-repair live windows meet H3 mechanics.

It polls only manifests until one fully post-restart promoted window exists,
then materializes that *engineering sandbox* through the mandatory scientific
guard and restart-safe index.  It never creates an H3 record, boundary, or
result, and it leaves the active capture service untouched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import time
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(path)


def epoch_ns(value: str) -> int:
    # Do not round nanoseconds through a float when a scientific boundary is
    # compared to captured receipt timestamps.
    normalized = value[:-1] if value.endswith("Z") else value
    main, dot, fraction = normalized.partition(".")
    parsed = dt.datetime.fromisoformat(main + "+00:00")
    seconds = int(parsed.timestamp())
    nanos = int((fraction + "000000000")[:9]) if dot else 0
    return seconds * 1_000_000_000 + nanos


def candidate_window(root: Path, not_before_ns: int) -> str | None:
    for manifest_path in sorted((root / "shared-windows").glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text())
            capture_start = int(manifest.get("requestedCaptureStartNs", 0))
            candidates = manifest.get("candidates", [])
            if capture_start < not_before_ns or not candidates:
                continue
            complete = True
            for candidate in candidates:
                receipt = root / "promotion-results" / f"{candidate['signature']}.json"
                complete &= receipt.is_file() and json.loads(receipt.read_text()).get("status") == "COMPLETE"
            if complete:
                return manifest_path.parent.name
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def call(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "subprocess failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--promotion-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--not-before-utc", required=True)
    parser.add_argument("--materializer", type=Path, required=True)
    parser.add_argument("--scientific-runner", type=Path, required=True)
    parser.add_argument("--indexer", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--registry-script", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--max-wait-seconds", type=int, default=43_200)
    args = parser.parse_args()
    start = time.monotonic(); boundary_ns = epoch_ns(args.not_before_utc) + 120_000_000_000
    status = args.out / "status.json"
    try:
        call(["python3", str(args.registry_script), "--registry", str(args.registry), "--epoch-id", "isolated-preh3-live-proof",
              "--kind", "FUTURE_UNOPENED", "--status", "UNOPENED", "--start-utc", args.not_before_utc,
              "--reason", "isolated engineering proof; not an H3 cohort"])
        while time.monotonic() - start < args.max_wait_seconds:
            window_id = candidate_window(args.promotion_root, boundary_ns)
            if window_id:
                materialized = args.out / "materialized"
                call(["python3", str(args.scientific_runner), "--materializer", str(args.materializer),
                      "--root", str(args.promotion_root), "--out", str(materialized), "--decoder", str(args.decoder),
                      "--registry-script", str(args.registry_script), "--registry-root", str(args.registry),
                      "--boundary-utc", args.not_before_utc, "--window-id", window_id])
                call(["python3", str(args.indexer), "--materializer-out", str(materialized),
                      "--index-root", str(args.out / "index"), "--require-scientific-order", "--export-canonical"])
                row_file = next((materialized / "market-rows").glob("*.jsonl"))
                receipt_path = materialized / "receipts" / f"{window_id}.json"
                call(["python3", str(args.contract), "--sample-row", str(row_file),
                      "--scientific-receipt", str(receipt_path), "--out", str(args.out / "contract.json")])
                contract = json.loads((args.out / "contract.json").read_text())
                if not contract.get("ready_for_h3_paper"):
                    raise RuntimeError(f"FAIL_CLOSED: live contract blockers {contract.get('blockers')}")
                proof = {"schema": "KEFI_PREH3_LIVE_PROOF_V1", "status": "PASS", "engineering_only": True,
                         "h3_boundary_created": False, "window_id": window_id,
                         "not_before_utc": args.not_before_utc,
                         "canonical_manifest_sha256": sha256(args.out / "index/canonical-export-v1/manifest.json"),
                         "contract_sha256": sha256(args.out / "contract.json")}
                atomic(args.out / "proof.json", proof)
                atomic(status, {"status": "PASS", "proof_path": str(args.out / "proof.json"), "proof_sha256": sha256(args.out / "proof.json")})
                return
            atomic(status, {"status": "WAITING_FOR_POST_REPAIR_CANDIDATE", "not_before_utc": args.not_before_utc})
            time.sleep(30)
        atomic(status, {"status": "TIMED_OUT_NO_POST_REPAIR_CANDIDATE", "not_before_utc": args.not_before_utc})
    except Exception as error:
        atomic(status, {"status": "FAIL_CLOSED", "error": str(error), "h3_boundary_created": False})
        raise


if __name__ == "__main__":
    main()
