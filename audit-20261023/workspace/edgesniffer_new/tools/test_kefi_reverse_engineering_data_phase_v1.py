#!/usr/bin/env python3
"""Regression tests for the outcome-blind kEFi V1 reconstruction contract."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "kefi_reverse_engineering_data_phase_v1.py"


def action(signature: str, side: str, before: int, after: int, slot: int) -> dict:
    return {"action_id": signature + side, "signature": signature, "mint": "mint", "slot": slot, "side": side, "pre_wallet_token_raw": before, "post_wallet_token_raw": after, "provenance": {"source": "TEST"}}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["python", str(TOOL), *args], text=True, capture_output=True, check=False)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        actions = root / "actions.jsonl"
        actions.write_text("\n".join(json.dumps(value) for value in [action("a", "BUY", 0, 10, 1), action("b", "SELL", 10, 0, 2), action("c", "BUY", 0, 12, 3)]) + "\n")
        out_a, out_b = root / "a", root / "b"
        assert run("--wallet-actions", str(actions), "--out-root", str(out_a)).returncode == 0
        assert run("--wallet-actions", str(actions), "--out-root", str(out_b)).returncode == 0
        assert (out_a / "wallet-state-index.jsonl").read_bytes() == (out_b / "wallet-state-index.jsonl").read_bytes()
        states = [json.loads(line)["wallet_state_class"] for line in (out_a / "wallet-state-index.jsonl").read_text().splitlines()]
        assert states == ["FIRST_OBSERVED_ENTRY", "CONFIRMED_FLAT_EXIT", "TRUE_REENTRY_AFTER_CONFIRMED_FLAT"]
        rejected = run("--wallet-actions", str(actions), "--out-root", str(root / "reject"), "--independent-public-events", str(actions), "--independent-public-manifest", str(root / "missing.json"))
        assert rejected.returncode != 0
        events = root / "events.jsonl"
        event = {"pool": "pool", "mint": "mint", "received_at_unix_ns": 20_000_000_000, "candidate_signature": "public", "event_index": 0, "side": "BUY", "user_quote_amount": 7, "pool_quote_raw": 100, "virtual_quote_reserves": 90, "lp_fee_bps": 20, "protocol_fee_bps": 5, "coin_creator_bps": 0, "buyback_bps": 0, "cashback_bps": 0, "post_entry_pnl": 999999}
        events.write_text(json.dumps(event) + "\n")
        manifest = root / "independent.json"
        manifest.write_text(json.dumps({"independent_candidate_universe": True, "uses_future_outcomes": False}))
        positive = run("--wallet-actions", str(actions), "--out-root", str(root / "positive"), "--independent-public-events", str(events), "--independent-public-manifest", str(manifest))
        assert positive.returncode == 0, positive.stderr
        candidate = json.loads((root / "positive" / "independent-candidates.jsonl").read_text())
        assert candidate["candidate_id"] and "post_entry_pnl" not in candidate
    print("PASS")


if __name__ == "__main__":
    main()
