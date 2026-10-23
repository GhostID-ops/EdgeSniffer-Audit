#!/usr/bin/env python3
"""Independent canonical Pump evidence tape for 8YCB.

Reads retained PEL frames read-only and writes only to the canonical namespace.
The decoder intentionally records normalized TradeEvent/CREATE evidence and its
source hash/path; it never emits a submission or modifies V4/V5 artifacts.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pathlib
import struct
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Iterable, Iterator

MAGIC = b"PEL1FRM\0"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TRADE_EVENT = bytes.fromhex("bddb7fd34ee661ee")
KIND = {
    bytes.fromhex("181ec828051c0777"): "CREATE",
    bytes.fromhex("d6904ce c5f8b31b4".replace(" ", "")): "CREATE_V2",
    bytes.fromhex("66063d1201daebea"): "BUY",
    bytes.fromhex("38fc74089ed fcd5f".replace(" ", "")): "BUY_EXACT_SOL_IN",
    bytes.fromhex("b817ee6167c5d33d"): "BUY_V2",
    bytes.fromhex("c2ab1c46684d5b2f"): "BUY_EXACT_QUOTE_IN_V2",
    bytes.fromhex("33e685a4017f83ad"): "SELL",
    bytes.fromhex("5df6823ce7e940b2"): "SELL_V2",
}


def b58(data: bytes) -> str:
    alphabet = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(data, "big")
    out = bytearray()
    while n:
        n, r = divmod(n, 58)
        out.append(alphabet[r])
    out.extend(alphabet[:1] * (len(data) - len(data.lstrip(b"\0"))))
    return bytes(reversed(out or alphabet[:1])).decode()


def varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while pos < len(data):
        x = data[pos]
        pos += 1
        value |= (x & 0x7F) << shift
        if not x & 0x80:
            return value, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


def fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    pos = 0
    while pos < len(data):
        tag, pos = varint(data, pos)
        no, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = varint(data, pos)
        elif wire == 1:
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = varint(data, pos)
            value, pos = data[pos:pos + length], pos + length
        elif wire == 5:
            value, pos = data[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield no, wire, value


def vals(data: bytes, no: int, wire: int | None = None) -> list[int | bytes]:
    return [v for n, w, v in fields(data) if n == no and (wire is None or w == wire)]


def one(data: bytes, no: int, default=None):
    values = vals(data, no)
    return values[0] if values else default


def u64(data: bytes | int | None, default: int = 0) -> int:
    if isinstance(data, int):
        return data
    if isinstance(data, bytes):
        return int.from_bytes(data, "little")
    return default


def tx_parts(raw: bytes) -> tuple[str, int, int, bytes, bytes, list[bytes], list[bytes], list[bytes], list[bytes]] | None:
    """Return signature, slot, tx index, message, meta, keys, loaded keys, instructions, inner."""
    update = one(raw, 4, None)
    if not isinstance(update, bytes):
        return None
    slot = u64(one(update, 2), 0)
    info = one(update, 1, None)
    if not isinstance(info, bytes):
        return None
    signature_raw = one(info, 1, None)
    if not isinstance(signature_raw, bytes):
        return None
    tx_index = u64(one(info, 5), 0)
    tx = one(info, 3, None)
    meta = one(info, 4, b"")
    if not isinstance(tx, bytes):
        return None
    message = one(tx, 2, None)
    if not isinstance(message, bytes):
        return None
    keys = [x for x in vals(message, 2, 2) if isinstance(x, bytes)]
    loaded = [x for x in vals(meta, 12, 2) if isinstance(x, bytes)] + [x for x in vals(meta, 13, 2) if isinstance(x, bytes)]
    instructions = [x for x in vals(message, 4, 2) if isinstance(x, bytes)]
    inner = [x for group in vals(meta, 5, 2) if isinstance(group, bytes) for x in vals(group, 2, 2) if isinstance(x, bytes)]
    return b58(signature_raw), slot, tx_index, message, meta, keys, loaded, instructions, inner


def instruction_info(data: bytes, keys: list[bytes]) -> tuple[str | None, str | None]:
    program_index = u64(one(data, 1), 999999)
    program = b58(keys[program_index]) if program_index < len(keys) else None
    ix_data = one(data, 3, b"")
    kind = KIND.get(ix_data[:8]) if isinstance(ix_data, bytes) else None
    mint = None
    if kind in ("CREATE", "CREATE_V2"):
        accounts = one(data, 2, b"")
        if isinstance(accounts, bytes) and accounts:
            mint_index = accounts[0]
            if mint_index < len(keys):
                mint = b58(keys[mint_index])
    return (kind if program == PUMP else None), mint


def pump_instructions(parts) -> list[tuple[str, str, str | None]]:
    _sig, _slot, _idx, _message, _meta, keys, loaded, instructions, inner = parts
    all_keys = keys + loaded
    result: list[tuple[str, str, str | None]] = []
    for i, ix in enumerate(instructions):
        kind, mint = instruction_info(ix, all_keys)
        if kind:
            result.append((f"outer:{i}", kind, mint))
    for i, ix in enumerate(inner):
        kind, mint = instruction_info(ix, all_keys)
        if kind:
            result.append((f"inner:{i}", kind, mint))
    return result


def trade_event(data: bytes):
    if len(data) < 121 or data[:8] != TRADE_EVENT:
        return None
    body = data[8:]
    return {
        "mint": b58(body[0:32]),
        "sol": int.from_bytes(body[32:40], "little"),
        "token": int.from_bytes(body[40:48], "little"),
        "is_buy": bool(body[48]),
        "wallet": b58(body[49:81]),
        "timestamp": int.from_bytes(body[81:89], "little", signed=True),
        "virtual_sol": int.from_bytes(body[89:97], "little"),
        "virtual_token": int.from_bytes(body[97:105], "little"),
        "real_sol": int.from_bytes(body[105:113], "little"),
        "real_token": int.from_bytes(body[113:121], "little"),
    }


def metadata_ns(metadata: dict) -> int:
    for key in ("receivedAtUnixNs", "received_at_unix_ns", "receivedNs"):
        try:
            return int(metadata.get(key) or 0)
        except (TypeError, ValueError):
            pass
    return 0


def read_frames(path: pathlib.Path) -> Iterator[tuple[dict, bytes]]:
    if path.name.endswith(".zst"):
        with subprocess.Popen(["/usr/bin/zstd", "-q", "-dc", str(path)], stdout=subprocess.PIPE) as proc:
            assert proc.stdout is not None
            yield from read_frame_stream(proc.stdout)
        return
    with path.open("rb") as f:
        yield from read_frame_stream(f)


def read_frame_stream(f) -> Iterator[tuple[dict, bytes]]:
    if f.read(8) != MAGIC:
        return
    while True:
        header = f.read(8)
        if len(header) != 8:
            return
        metadata_len, raw_len = struct.unpack("<II", header)
        if metadata_len > 16 * 1024 * 1024 or raw_len > 128 * 1024 * 1024:
            return
        metadata_raw = f.read(metadata_len)
        raw = f.read(raw_len)
        digest = f.read(32)
        if len(metadata_raw) != metadata_len or len(raw) != raw_len or len(digest) != 32:
            return
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            continue
        if hashlib.sha256(metadata_raw + raw).digest() != digest:
            continue
        yield metadata, raw


def load_targets(ledger: pathlib.Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
            target = {k: row.get(k) for k in ("canonical_ordinal", "signature", "mint", "slot", "transaction_index", "source_receive_ns")}
            if not target.get("signature") or not target.get("mint"):
                continue
            out[f"sig:{target['signature']}"] = target
            out[f"mint:{target['mint']}"] = target
        except (ValueError, TypeError):
            continue
    return out


def in_window(target: dict, slot: int, received_ns: int) -> bool:
    target_ns = int(target.get("source_receive_ns") or 0)
    if target_ns and received_ns:
        return target_ns - 5_000_000 <= received_ns <= target_ns + 120_000_000_000
    start = int(target.get("slot") or 0)
    return start <= slot <= start + 1000


def canonical_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def stable(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Tape:
    def __init__(self, root: pathlib.Path, ledger: pathlib.Path):
        self.root = root
        self.ledger = ledger
        self.output = root / "evidence.jsonl"
        self.health_path = root / "health.json"
        self.state_path = root / "source-scan-state.json"
        self.seen: set[str] = set()
        if self.output.exists():
            for line in self.output.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    key = json.loads(line).get("dedupe_key")
                    if key:
                        self.seen.add(key)
                except ValueError:
                    pass
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.state = {}
        try:
            prior_health = json.loads(self.health_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            prior_health = {}
        self.stats = {"files_scanned": 0, "frames_scanned": 0, "matching_frames": 0, "events_written": 0, "bad_frames": 0}
        self.max_source_received_ns = int(prior_health.get("max_source_received_ns") or 0)
        self.max_source_slot = int(prior_health.get("max_source_slot") or 0)

    def emit(self, row: dict) -> None:
        key = row["dedupe_key"]
        if key in self.seen:
            return
        with self.output.open("a", encoding="utf-8") as f:
            f.write(stable(row) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.seen.add(key)
        self.stats["events_written"] += 1

    def process(self, path: pathlib.Path, targets: dict[str, dict]) -> None:
        stat = path.stat()
        marker = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        old = self.state.get(str(path))
        # Complete promoted windows are immutable; active files are revisited on growth.
        if old == marker:
            return
        self.stats["files_scanned"] += 1
        for metadata, raw in read_frames(path):
            self.stats["frames_scanned"] += 1
            parts = tx_parts(raw)
            if not parts:
                continue
            signature, slot, tx_index, _message, meta, _keys, _loaded, _ixs, _inner = parts
            received_ns = metadata_ns(metadata)
            self.max_source_received_ns = max(self.max_source_received_ns, received_ns)
            self.max_source_slot = max(self.max_source_slot, slot)
            target = targets.get(f"sig:{signature}")
            p_ixs = pump_instructions(parts)
            creates = [x for x in p_ixs if x[1] in ("CREATE", "CREATE_V2")]
            for _n, kind, mint in creates:
                if mint:
                    target = target or targets.get(f"mint:{mint}")
            logs = []
            for value in vals(meta, 6, 2):
                if isinstance(value, bytes):
                    try:
                        logs.append(value.decode("utf-8"))
                    except UnicodeDecodeError:
                        pass
            events = []
            for log_index, log in enumerate(logs):
                if not log.startswith("Program data: "):
                    continue
                try:
                    event = trade_event(base64.b64decode(log[14:], validate=True))
                except Exception:
                    event = None
                if event:
                    events.append((log_index, event))
            for event_index, (log_index, event) in enumerate(events):
                event_target = targets.get(f"mint:{event['mint']}") or target
                if not event_target or event_target.get("mint") != event["mint"] or not in_window(event_target, slot, received_ns):
                    continue
                self.stats["matching_frames"] += 1
                matching_ix = next((x for x in p_ixs if (event["is_buy"] and x[1].startswith("BUY")) or ((not event["is_buy"]) and x[1].startswith("SELL"))), None)
                row = {
                    "schema": "8ycb-canonical-pump-evidence",
                    "schema_version": "v1",
                    "dedupe_key": f"{event_target['canonical_ordinal']}|{signature}|trade|{event_index}",
                    "canonical_ordinal": event_target["canonical_ordinal"],
                    "launch_signature": event_target["signature"],
                    "mint": event["mint"],
                    "evidence_kind": "TRADE_EVENT",
                    "transaction_signature": signature,
                    "slot": slot,
                    "transaction_index": tx_index,
                    "instruction_coordinate": matching_ix[0] if matching_ix else None,
                    "pump_instruction_kind": matching_ix[1] if matching_ix else None,
                    "side": "buy" if event["is_buy"] else "sell",
                    "wallet": event["wallet"],
                    "quote_lamports": event["sol"],
                    "token_amount_raw": event["token"],
                    "event_timestamp_s": event["timestamp"],
                    "virtual_sol_reserves_after": event["virtual_sol"],
                    "virtual_token_reserves_after": event["virtual_token"],
                    "real_sol_reserves_after": event["real_sol"],
                    "real_token_reserves_after": event["real_token"],
                    "source_received_ns": received_ns,
                    "source_evidence_id": metadata.get("evidenceId"),
                    "source_path": str(path),
                    "raw_sha256": metadata.get("rawSha256") or hashlib.sha256(raw).hexdigest(),
                    "exact_ordering": f"slot:{slot}|tx:{tx_index}|log:{log_index}|event:{event_index}",
                    "created_at_utc": canonical_now(),
                }
                self.emit(row)
            for coord, kind, mint in creates:
                create_target = targets.get(f"sig:{signature}") or (targets.get(f"mint:{mint}") if mint else None)
                if not create_target or (mint and create_target.get("mint") != mint) or not in_window(create_target, slot, received_ns):
                    continue
                self.emit({
                    "schema": "8ycb-canonical-pump-evidence", "schema_version": "v1",
                    "dedupe_key": f"{create_target['canonical_ordinal']}|{signature}|create",
                    "canonical_ordinal": create_target["canonical_ordinal"], "launch_signature": create_target["signature"],
                    "mint": mint or create_target["mint"], "evidence_kind": "CREATE", "transaction_signature": signature,
                    "slot": slot, "transaction_index": tx_index, "instruction_coordinate": coord,
                    "pump_instruction_kind": kind, "side": None, "wallet": None, "quote_lamports": None,
                    "token_amount_raw": None, "event_timestamp_s": None, "virtual_sol_reserves_after": None,
                    "virtual_token_reserves_after": None, "real_sol_reserves_after": None, "real_token_reserves_after": None,
                    "source_received_ns": received_ns, "source_evidence_id": metadata.get("evidenceId"),
                    "source_path": str(path), "raw_sha256": metadata.get("rawSha256") or hashlib.sha256(raw).hexdigest(),
                    "exact_ordering": f"slot:{slot}|tx:{tx_index}|create", "created_at_utc": canonical_now(),
                })
        self.state[str(path)] = marker

    def save(self, targets: dict[str, dict], source: pathlib.Path) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(stable(self.state) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)
        health = {
            "schema": "8ycb-canonical-pump-tape-health-v1", "updated_at_utc": canonical_now(),
            "schema_version": "v1", "source_root": str(source),
            "canonical_targets": len({id(v) for v in targets.values()}), "dedupe_entries": len(self.seen),
            "source_files_known": len(list(source.rglob("evidence.frames*"))) if source.exists() else 0,
            "source_files_scanned": len(self.state),
            "source_scan_complete": bool(source.exists() and len(self.state) >= len(list(source.rglob("evidence.frames*")))),
            "max_source_received_ns": self.max_source_received_ns,
            "max_source_slot": self.max_source_slot,
            **self.stats, "real_market_submission_count": 0, "real_market_submission_enabled": False,
        }
        tmp = self.health_path.with_suffix(".tmp")
        tmp.write_text(stable(health) + "\n", encoding="utf-8")
        os.replace(tmp, self.health_path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    source = pathlib.Path(args.source)
    root = pathlib.Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    tape = Tape(root, pathlib.Path(args.ledger))
    while True:
        targets = load_targets(pathlib.Path(args.ledger))
        # Process newest promoted/current windows first so recently finalized
        # canonical launches become visible promptly; older partitions are
        # still scanned in the same pass and remain restart-safe via state.
        files = sorted(
            (p for p in source.rglob("evidence.frames*") if p.is_file()),
            key=lambda p: (0 if "promoted-windows" in str(p) else 1, -p.stat().st_mtime_ns, str(p)),
        ) if source.exists() else []
        for path in files:
            try:
                tape.process(path, targets)
            except (OSError, ValueError, struct.error) as exc:
                tape.stats["bad_frames"] += 1
                tape.stats["last_error"] = f"{path}: {type(exc).__name__}: {exc}"
            # Persist the completed-file watermark after each file. This makes
            # a restart resume from the next partition instead of replaying a
            # multi-gigabyte source scan.
            tape.save(targets, source)
        tape.save(targets, source)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
