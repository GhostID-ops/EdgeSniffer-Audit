#!/usr/bin/env python3
"""Build a bounded, resumable, direct kEFi wallet-action ledger.

This is deliberately an outcome-blind data collector.  It stores compact
finalized transaction summaries and token-balance deltas, never market outcomes
or PnL.  A generic market sell is never considered a kEFi exit: every emitted
row comes from the tracked wallet's own finalized transaction.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import time
import urllib.request
from collections.abc import Iterable
from pathlib import Path

try:  # The production index runs on Linux; keep pure parsing tests portable.
    import fcntl
except ImportError:  # pragma: no cover - Windows test host only
    fcntl = None


PUMP_PROGRAMS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
}
ROUTER_PROGRAMS = {"JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_endpoint(env_file: Path) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("SECURE_RPC_URL="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("SECURE_RPC_URL missing from RPC environment file")


def rpc(endpoint: str, method: str, params: list[object]) -> object:
    # The wallet index is a bounded operational side lane.  A stalled RPC must
    # fail a timer run and release its lock, never block a fresh cohort handoff
    # for many minutes.
    timeout_seconds = float(os.environ.get("KEFI_WALLET_RPC_TIMEOUT_SECONDS", "8"))
    request = urllib.request.Request(
        endpoint,
        data=canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read())
    if payload.get("error"):
        raise RuntimeError(f"{method}: {payload['error']}")
    return payload["result"]


def account_program_ids(transaction: dict) -> set[str]:
    ids: set[str] = set()
    message = transaction.get("transaction", {}).get("message", {})
    for instruction in message.get("instructions", []) or []:
        value = instruction.get("programId") or instruction.get("program")
        if value:
            ids.add(value)
    for group in transaction.get("meta", {}).get("innerInstructions", []) or []:
        for instruction in group.get("instructions", []) or []:
            value = instruction.get("programId") or instruction.get("program")
            if value:
                ids.add(value)
    return ids


def raw_amount(balance: dict) -> int:
    amount = balance.get("uiTokenAmount", {}).get("amount")
    return int(amount) if amount not in (None, "") else 0


def keyed_balances(balances: Iterable[dict], wallet: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for balance in balances:
        if balance.get("owner") != wallet:
            continue
        mint = balance.get("mint")
        if mint:
            result[mint] = result.get(mint, 0) + raw_amount(balance)
    return result


def wallet_lamports(transaction: dict, wallet: str) -> tuple[int | None, int | None]:
    message = transaction.get("transaction", {}).get("message", {})
    keys = message.get("accountKeys", []) or []
    for index, key in enumerate(keys):
        pubkey = key.get("pubkey") if isinstance(key, dict) else key
        if pubkey == wallet:
            meta = transaction.get("meta", {})
            pre, post = meta.get("preBalances", []), meta.get("postBalances", [])
            return (int(pre[index]) if index < len(pre) else None, int(post[index]) if index < len(post) else None)
    return None, None


def classify_delta(delta: int, programs: set[str]) -> tuple[str, str]:
    pump = bool(programs & PUMP_PROGRAMS)
    router = bool(programs & ROUTER_PROGRAMS)
    if delta > 0:
        return ("BUY", "DIRECT_SWAP_BUY") if pump or router else ("TRANSFER_IN", "DIRECT_TOKEN_TRANSFER_IN")
    return ("SELL", "DIRECT_SWAP_SELL") if pump or router else ("TRANSFER_OUT", "DIRECT_TOKEN_TRANSFER_OUT")


def records_for_transaction(wallet: str, signature_row: dict, transaction: dict) -> list[dict]:
    meta = transaction.get("meta") or {}
    pre = keyed_balances(meta.get("preTokenBalances", []) or [], wallet)
    post = keyed_balances(meta.get("postTokenBalances", []) or [], wallet)
    programs = account_program_ids(transaction)
    pre_lamports, post_lamports = wallet_lamports(transaction, wallet)
    records: list[dict] = []
    for mint in sorted(set(pre) | set(post)):
        before, after = pre.get(mint, 0), post.get(mint, 0)
        delta = after - before
        if not delta:
            continue
        side, action_type = classify_delta(delta, programs)
        identity = {"signature": signature_row["signature"], "mint": mint, "side": side, "delta": delta}
        records.append(
            {
                "schema": "KEFI_DIRECT_WALLET_ACTION_V1",
                "action_id": sha256(identity),
                "wallet": wallet,
                "signature": signature_row["signature"],
                "slot": signature_row.get("slot"),
                "block_time": signature_row.get("blockTime"),
                "transaction_error": signature_row.get("err"),
                "mint": mint,
                "side": side,
                "action_type": action_type,
                "pre_wallet_token_raw": before,
                "post_wallet_token_raw": after,
                "wallet_token_delta_raw": delta,
                "wallet_pre_lamports": pre_lamports,
                "wallet_post_lamports": post_lamports,
                "wallet_lamport_delta": (post_lamports - pre_lamports) if pre_lamports is not None and post_lamports is not None else None,
                "program_ids": sorted(programs),
                "transaction_summary_sha256": sha256(transaction),
                "provenance": {"source": "FINALIZED_RPC_DIRECT_WALLET_TRANSACTION", "observed_at_utc": utc_now()},
            }
        )
    return records


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["action_id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema": "KEFI_DIRECT_WALLET_ACTION_INDEX_STATE_V1", "history_before_signature": None, "runs": 0}
    return json.loads(path.read_text(encoding="utf-8"))


@contextlib.contextmanager
def action_index_lock(root: Path) -> Iterable[None]:
    """Serialize the periodic index and the launch-time tail synchronizer."""
    lock_path = root / "wallet-actions.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is None:
            # The operational program is never deployed on this platform.
            # Refusing a concurrent Windows invocation is still preferable to
            # silently pretending there is a cross-process lock.
            yield
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("FAIL_CLOSED: wallet action index is already being updated") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--rpc-env", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--max-signatures", type=int, default=2500)
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--sleep-seconds", type=float, default=0.08)
    args = parser.parse_args()
    if not 1 <= args.max_signatures <= 10_000 or not 1 <= args.page_size <= 1000:
        raise SystemExit("bounded max-signatures/page-size required")

    root, actions_path = args.out_root, args.out_root / "wallet-actions.jsonl"
    root.mkdir(parents=True, exist_ok=True)
    with action_index_lock(root):
        return run_index(args, actions_path)


def run_index(args: argparse.Namespace, actions_path: Path) -> int:
    root = args.out_root
    endpoint, seen, state = read_endpoint(args.rpc_env), load_seen(actions_path), load_state(root / "state.json")
    # Every run tails the newest bounded page *and* advances one independent
    # older-history cursor. This avoids rereading the same 2,500 newest rows
    # forever while still retaining newly arriving direct wallet actions.
    newest_page = rpc(endpoint, "getSignaturesForAddress", [args.wallet, {"limit": min(args.page_size, args.max_signatures), "commitment": "finalized"}])
    signatures: list[dict] = list(newest_page)
    before: str | None = state.get("history_before_signature") or (newest_page[-1]["signature"] if newest_page else None)
    history_pages = 0
    while before and len(signatures) < args.max_signatures:
        options: dict[str, object] = {"limit": min(args.page_size, args.max_signatures - len(signatures)), "commitment": "finalized", "before": before}
        page = rpc(endpoint, "getSignaturesForAddress", [args.wallet, options])
        if not page:
            before = None
            break
        signatures.extend(page)
        before = page[-1]["signature"]
        history_pages += 1
        if len(page) < options["limit"]:
            before = None
            break
        time.sleep(args.sleep_seconds)

    deduped = {row["signature"]: row for row in signatures}
    signatures = list(deduped.values())

    emitted = 0
    tx_errors = 0
    oldest_block_time, newest_block_time = None, None
    for row in reversed(signatures):
        block_time = row.get("blockTime")
        oldest_block_time = block_time if oldest_block_time is None else min(oldest_block_time, block_time) if block_time is not None else oldest_block_time
        newest_block_time = block_time if newest_block_time is None else max(newest_block_time, block_time) if block_time is not None else newest_block_time
        try:
            transaction = rpc(endpoint, "getTransaction", [row["signature"], {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
            if transaction:
                for record in records_for_transaction(args.wallet, row, transaction):
                    if record["action_id"] in seen:
                        continue
                    with actions_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, sort_keys=True) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    seen.add(record["action_id"])
                    emitted += 1
        except Exception:
            tx_errors += 1
        time.sleep(args.sleep_seconds)

    state.update({"history_before_signature": before, "runs": int(state.get("runs", 0)) + 1, "last_run_utc": utc_now()})
    atomic_json(root / "state.json", state)
    manifest = {
        "schema": "KEFI_DIRECT_WALLET_ACTION_INDEX_MANIFEST_V1",
        "wallet": args.wallet,
        "coverage": {"signatures_examined": len(signatures), "newest_tail_signatures": len(newest_page), "historical_pages_advanced": history_pages, "next_history_before_signature": before, "oldest_block_time": oldest_block_time, "newest_block_time": newest_block_time, "max_signatures": args.max_signatures, "bounded": True},
        "emitted_actions_this_run": emitted,
        "transaction_fetch_errors": tx_errors,
        "actions_path": str(actions_path),
        "actions_sha256": hashlib.sha256(actions_path.read_bytes()).hexdigest() if actions_path.exists() else None,
        "prohibitions": {"pnl": "not_read", "outcomes": "not_read", "generic_market_sells_used_as_wallet_exits": False},
        "generated_at_utc": utc_now(),
    }
    atomic_json(root / "manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
