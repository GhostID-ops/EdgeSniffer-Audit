import hashlib
import json
import subprocess
import sys


TOOL = __file__.replace("test_kefi_rich_preentry_reconstruction_v1.py", "kefi_rich_preentry_reconstruction_v1.py")


def row(signature, at, pool="pool", quote=100, event=0):
    return {"candidate_signature": signature, "received_at_unix_ns": at, "slot": 1, "event_index": event,
            "pool": pool, "mint": "mint", "source_partition": "2026/10/05/03", "pool_quote_raw": quote,
            "pool_base_raw": 10_000, "user_quote_amount": 10, "lp_fee_bps": 1, "protocol_fee_bps": 2,
            "coin_creator_bps": 3}


def selected(signature, at):
    value = row(signature, at)
    value.pop("candidate_signature")
    value["signature"] = signature
    value["selected_mint"] = value.pop("mint")
    value["side"] = "BUY"
    return value


def run(tmp, output):
    return subprocess.run([sys.executable, TOOL, "--selected", str(tmp / "selected.jsonl"), "--shard-dir", str(tmp / "shards"),
                           "--shards", "03", "--output", str(output), "--manifest", str(output.with_suffix(".manifest.json")),
                           "--sample-threshold-per-million", "1000000"], check=True, capture_output=True, text=True)


def test_snapshot_is_strictly_preentry_and_repeatable(tmp_path):
    (tmp_path / "shards").mkdir()
    (tmp_path / "selected.jsonl").write_text(json.dumps(selected("teacher", 20_000_000_000)) + "\n")
    # The selected transaction is intentionally absent from the candidate universe.
    rows = [row("before", 10_000_000_000), row("after", 30_000_000_000)]
    (tmp_path / "shards" / "03.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    run(tmp_path, first); run(tmp_path, second)
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    emitted = [json.loads(line) for line in first.read_text().splitlines()]
    teacher = next(item for item in emitted if item["label"] == 1)
    assert teacher["preentry"]["prior_buy_count_20s"] == 1
    assert teacher["preentry"]["prior_buy_quote_20s"] == 10
    after = next(item for item in emitted if item["identity"].startswith("after:"))
    assert after["preentry"]["prior_buy_count_20s"] == 2
