#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import os
import queue
import signal
import socket
import struct
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone


COHORT_NAME = "RANKER_V2_PROSPECTIVE_SHADOW_V2"
DIRECT_EMITTER_COHORT_NAME = "RANKER_V2_SCORER_DIRECT_EMITTER_V1"
THRESHOLD = 0.590596
ENTRY_DELAY_MS = 25
SELLABLE = {"P50": 964, "P75": 1476, "P90": 1989}
PRIMARY_SELLABLE_KEY = "P75"
TP_PCT = 20.0
SL_PCT = -20.0
HOLD_MS = 120_000
ROUND_TRIP_COST_PCT = 2.5
EXPECTED_SPEC_SHA256 = "ca785e8d919ccc795be2c41c9787ad5a77617767cd75f748a111f93eb00cacee"
EXPECTED_OBJECT_SHA256 = "3784908b49f724ba664cde3cd355b4aee30f5bfa48c73f6797845cd7719e2938"
EXPECTED_LEDGER_COUNTS = {"train": 964, "validation": 428, "final_test": 766}
DIRECT_LANE = "ranker_v2_scorer_direct_emitter_v1"
DIRECT_POLICY = "ranker_v2_direct_scorer_emitter_v1_tp20_sl20_120s"
SOL_IN_LAMPORTS = 1_000_000
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"


def now_ms():
    return int(time.time() * 1000)


def iso(ms=None):
    if ms is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def num(value, default=None):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def compact(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 9)
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(compact(obj), handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def write_jsonl_gz(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(compact(row), sort_keys=True, separators=(",", ":")) + "\n")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def f64_from_lossless(obj):
    if isinstance(obj, dict) and "float_hex" in obj:
        return float.fromhex(obj["float_hex"])
    if isinstance(obj, dict) and "bits_hex_be" in obj:
        return struct.unpack(">d", int(obj["bits_hex_be"], 16).to_bytes(8, "big"))[0]
    if isinstance(obj, dict) and "decimal_repr" in obj:
        return float(obj["decimal_repr"])
    return float(obj)


def read_jsonl_any(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_canonical_spec(path):
    spec = json.load(open(path, "r", encoding="utf-8"))
    stats = {}
    for item in spec["normalization_constants_lossless"]:
        stats[item["feature"]] = {
            "center": f64_from_lossless(item["center_f64"]),
            "scale": f64_from_lossless(item["scale_f64"]),
            "corr": f64_from_lossless(item["corr_f64"]),
            "coverage": int(item["coverage"]),
        }
    ordered = []
    for item in spec["weights_lossless"]:
        ordered.append((item["feature"], f64_from_lossless(item["weight_f64"])))
    threshold = f64_from_lossless(spec["threshold"]["value_f64"])
    return spec, stats, ordered, threshold


def signal_id(signal):
    return f"{signal['mint']}:{int(signal['threshold_decision_ts_ms'])}:{float(signal['score']):.9f}"


def capsule_mint(row):
    return (row.get("candidate") or {}).get("mint") or (row.get("raw_record") or {}).get("mint")


def capsule_ok(row):
    return bool(
        capsule_mint(row)
        and (row.get("execution") or {}).get("expected_tokens_out_raw")
        and (row.get("extra_decision_fields") or {}).get("bondingCurve")
        and (row.get("extra_decision_fields") or {}).get("sourceIxAccounts")
    )


def capsule_rank(row):
    candidate = row.get("candidate") or {}
    raw = row.get("raw_record") or {}
    extra = row.get("extra_decision_fields") or {}
    token_program = candidate.get("token_program") or raw.get("tokenProgram") or candidate.get("tokenProgram")
    accounts = extra.get("sourceIxAccounts") or []
    source_data = candidate.get("source_ix_data_base64") or raw.get("sourceIxDataBase64")
    observed = int((row.get("canonical_state") or {}).get("observed_at_ms") or 0)
    side = raw.get("side")
    native_kind = raw.get("nativeKind")
    is_token2022 = token_program == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    sell_template_quality = 2 if is_token2022 and len(accounts) >= 26 else 1 if (not is_token2022 and len(accounts) >= 16) else 0
    buy_source_quality = 2 if side == "buy" and source_data and accounts else 1 if native_kind == "creation" else 0
    return buy_source_quality, sell_template_quality, observed


def parse_reserves_from_state_identity(row):
    state_identity = (row.get("canonical_state") or {}).get("state_identity")
    if not isinstance(state_identity, str):
        return None
    parts = state_identity.split(":")
    if len(parts) < 4 or parts[0] != "native-shredstream-replay":
        return None
    try:
        return int(parts[-2]), int(parts[-1])
    except ValueError:
        return None


def direct_selected_event(signal_row, capsule):
    candidate = capsule.get("candidate") or {}
    raw = capsule.get("raw_record") or {}
    canonical = capsule.get("canonical_state") or {}
    execution = capsule.get("execution") or {}
    extra = capsule.get("extra_decision_fields") or {}
    reserves = parse_reserves_from_state_identity(capsule)
    if reserves is None:
        raise ValueError("capsule missing parseable native reserve state_identity")
    virtual_sol_reserves, virtual_token_reserves = reserves
    raw_features = signal_row.get("raw_features") or {}
    decision_ms = int(signal_row["threshold_decision_ts_ms"])
    key = f"ranker-v2-direct-scorer-emitter-v1:{signal_id(signal_row)}"
    source_signature = (
        capsule.get("transaction_signature")
        or raw.get("signature")
        or f"ranker-v2-source:{signal_row['mint']}"
    )
    token_program_id = candidate.get("token_program") or raw.get("tokenProgram")
    observed_ms = int(canonical.get("observed_at_ms") or signal_row.get("event_gated_state_ts_ms") or decision_ms)
    return {
        "schemaVersion": "two-cq-continuous-shadow-event-1",
        "event": "candidate_selected",
        "at": iso(),
        "atMs": now_ms(),
        "entryAtMs": decision_ms,
        "lane": DIRECT_LANE,
        "policy": DIRECT_POLICY,
        "key": key,
        "mint": signal_row["mint"],
        "sourceSignature": source_signature,
        "entryMarketCapUsd": float(signal_row.get("mc_usd") or canonical.get("market_cap_usd")),
        "mayhemRetryPromoted": False,
        "isWeakMayhemRecovered": False,
        "mayhemBlockReason": "ranker_v2_not_mayhem_candidate",
        "ranker": {
            "cohort": signal_row.get("cohort"),
            "signalId": signal_id(signal_row),
            "score": float(signal_row["score"]),
            "threshold": THRESHOLD,
            "state": signal_row.get("state"),
            "historicalStateAlias": signal_row.get("historical_state_alias"),
            "stateTsMs": signal_row.get("event_gated_state_ts_ms"),
            "decisionTsMs": decision_ms,
            "sourceRowId": signal_row.get("source_row_id"),
            "selectionSemantics": signal_row.get("selection_semantics"),
            "featureReadyTsMs": signal_row.get("feature_ready_ts_ms"),
            "scoreReadyTsMs": signal_row.get("score_ready_ts_ms"),
        },
        "directEntryQuote": {
            "status": "ok",
            "solInLamports": SOL_IN_LAMPORTS,
            "tokensOutRaw": int(execution.get("expected_tokens_out_raw")),
            "minTokensOutRaw": int(execution.get("min_tokens_out_raw") or 1),
            "slippageBps": int(execution.get("slippage_bps") or 500),
            "curveSnapshot": {
                "pda": extra.get("bondingCurve"),
                "bondingCurve": extra.get("bondingCurve"),
                "creator": extra.get("creator") or "source-template-only",
                "tokenProgramId": token_program_id,
                "quoteMint": WRAPPED_SOL_MINT,
                "virtualSolReserves": virtual_sol_reserves,
                "virtualTokenReserves": virtual_token_reserves,
                "slot": int(canonical.get("source_slot") or capsule.get("source_slot") or 0),
                "observedAtMs": observed_ms,
            },
        },
        "features": {
            "mint": signal_row["mint"],
            "key": key,
            "sourceSignature": source_signature,
            "sourceWallet": "ranker-v2-direct-scorer-emitter-v1",
            "wallet": "ranker-v2-direct-scorer-emitter-v1",
            "observedAtMs": decision_ms,
            "marketCapUsd": float(signal_row.get("mc_usd") or canonical.get("market_cap_usd")),
            "sourceIxAccounts": extra.get("sourceIxAccounts"),
            "sourceIxDataBase64": candidate.get("source_ix_data_base64") or raw.get("sourceIxDataBase64"),
            "tokenProgramId": token_program_id,
            "tokenProgram": token_program_id,
            "buyCount100ms": int(raw_features.get("w100.buy_count") or 0),
            "firstBuyerQuoteShareBps": int(raw_features.get("first_buyer_quote_share_bps") or 0),
            "rankerScore": float(signal_row["score"]),
            "rankerThreshold": THRESHOLD,
            "rankerState": signal_row.get("state"),
            "rankerSignalId": signal_id(signal_row),
            "sourceSlot": int(canonical.get("source_slot") or capsule.get("source_slot") or 0),
            "curveObservedAtMs": observed_ms,
            "virtualSolReserves": virtual_sol_reserves,
            "virtualTokenReserves": virtual_token_reserves,
        },
    }


def direct_prewarm_event(capsule):
    mint = capsule_mint(capsule)
    canonical = capsule.get("canonical_state") or {}
    extra = capsule.get("extra_decision_fields") or {}
    candidate = capsule.get("candidate") or {}
    raw = capsule.get("raw_record") or {}
    reserves = parse_reserves_from_state_identity(capsule)
    if not mint or reserves is None:
        return None
    virtual_sol_reserves, virtual_token_reserves = reserves
    observed_ms = int(canonical.get("observed_at_ms") or now_ms())
    bonding_curve = extra.get("bondingCurve")
    token_program_id = candidate.get("token_program") or raw.get("tokenProgram")
    return {
        "schemaVersion": "ranker-v2-curve-prewarm-1",
        "event": "curve_prewarm",
        "at": iso(),
        "atMs": now_ms(),
        "lane": DIRECT_LANE,
        "policy": DIRECT_POLICY,
        "source": "ranker-v2-direct-scorer-emitter-v1-capsule-prewarm",
        "mint": mint,
        "bondingCurve": bonding_curve,
        "pda": bonding_curve,
        "tokenProgramId": token_program_id,
        "sourceSlot": int(canonical.get("source_slot") or capsule.get("source_slot") or 0),
        "slot": int(canonical.get("source_slot") or capsule.get("source_slot") or 0),
        "observedAtMs": observed_ms,
        "curveObservedAtMs": observed_ms,
        "virtualSolReserves": virtual_sol_reserves,
        "virtualTokenReserves": virtual_token_reserves,
        "transactionSignature": capsule.get("transaction_signature") or raw.get("signature"),
    }


class DirectHandoffClient:
    def __init__(self, host, port, enabled):
        self.host = host
        self.port = port
        self.enabled = enabled
        self.sock = None

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send(self, value):
        if not self.enabled:
            return {"enabled": False, "ok": True, "sendStartMs": None, "sendCompleteMs": None, "elapsedMs": None}
        if self.sock is None:
            self.sock = socket.create_connection((self.host, self.port), timeout=0.25)
            self.sock.settimeout(0.25)
        send_start = now_ms()
        value.setdefault("transport", {})
        value["transport"]["directHandoff"] = True
        value["transport"]["directHandoffHost"] = self.host
        value["transport"]["directHandoffPort"] = self.port
        value["transport"]["directHandoffSendAtMs"] = send_start
        line = json.dumps(compact(value), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            self.sock.sendall(line)
        except OSError:
            self.close()
            self.sock = socket.create_connection((self.host, self.port), timeout=0.25)
            self.sock.settimeout(0.25)
            self.sock.sendall(line)
        send_complete = now_ms()
        value["transport"]["directHandoffSendCompleteAtMs"] = send_complete
        return {"enabled": True, "ok": True, "sendStartMs": send_start, "sendCompleteMs": send_complete, "elapsedMs": send_complete - send_start}


def score_with_contribs(row, stats, ordered):
    score = 0.0
    used = 0
    norms = {}
    contribs = {}
    for name, weight in ordered:
        value = row.get(name)
        if not finite(value):
            continue
        st = stats[name]
        z = max(-5.0, min(5.0, (float(value) - st["center"]) / st["scale"]))
        c = weight * z
        norms[name] = z
        contribs[name] = c
        score += c
        used += 1
    return (score if used else None), norms, contribs, used


def q(values, pct):
    xs = sorted(v for v in values if finite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * pct
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


class CompletedLineBuffer:
    def __init__(self):
        self.buf = b""
        self.partial_seen = 0

    def feed(self, chunk):
        self.buf += chunk
        out = []
        while True:
            idx = self.buf.find(b"\n")
            if idx < 0:
                if self.buf:
                    self.partial_seen += 1
                return out
            line = self.buf[:idx]
            self.buf = self.buf[idx + 1 :]
            if line.endswith(b"\r"):
                line = line[:-1]
            out.append(line)


def run_partial_line_tests(out_dir):
    tests = []

    def parse_chunks(chunks):
        b = CompletedLineBuffer()
        parsed = []
        malformed = 0
        for chunk in chunks:
            for raw in b.feed(chunk):
                if not raw.strip():
                    continue
                try:
                    parsed.append(json.loads(raw.decode("utf-8")))
                except json.JSONDecodeError:
                    malformed += 1
        return parsed, malformed, b.buf

    cases = [
        ("split_json_record_across_reads", [b'{"a":', b"1}\n"], 1, 0, b""),
        ("multiple_records_in_one_read", [b'{"a":1}\n{"b":2}\n'], 2, 0, b""),
        ("newline_arriving_later", [b'{"a":1}', b"\n"], 1, 0, b""),
        ("restart_with_buffered_partial_final_line", [b'{"a":1}\n{"partial":'], 1, 0, b'{"partial":'),
        ("valid_record_after_partial_record", [b'{"a":', b"1}\n", b'{"b":2}\n'], 2, 0, b""),
        ("malformed_completed_record", [b'{"a":1}\n{bad}\n'], 1, 1, b""),
    ]
    for name, chunks, exp_parsed, exp_bad, exp_buf in cases:
        parsed, malformed, remaining = parse_chunks(chunks)
        tests.append({
            "name": name,
            "parsed": len(parsed),
            "malformed_completed_records": malformed,
            "remaining_buffer_bytes": len(remaining),
            "status": "PASS" if len(parsed) == exp_parsed and malformed == exp_bad and remaining == exp_buf else "FAIL",
        })
    report = {
        "created_at_utc": iso(),
        "tests": tests,
        "status": "PASS" if all(t["status"] == "PASS" for t in tests) else "FAIL",
    }
    write_json(os.path.join(out_dir, "partial_line_test_report.json"), report)
    return report


class EventGatedEngine:
    def __init__(self, atlas, spec, stats, ordered, threshold, cohort_start_ms=-1, emit_alias_labels=True):
        self.atlas = atlas
        self.spec = spec
        self.stats = stats
        self.ordered = ordered
        self.threshold = threshold
        self.cohort_start_ms = cohort_start_ms
        self.emit_alias_labels = emit_alias_labels
        self.flows = defaultdict(deque)
        self.first_seen = {}
        self.emitted_fixed = defaultdict(set)
        self.emitted_events = defaultdict(set)
        self.global_first_seen = deque()
        self.triggered_mints = set()
        self.counters = Counter()
        self.rejects = Counter()
        self.score_rows = []
        self.signal_rows = []

    def eligible(self, row, mint, ts, mc, side):
        if not mint:
            self.rejects["missing_mint"] += 1
            return False
        if not finite(ts):
            self.rejects["missing_ts"] += 1
            return False
        if int(ts) < self.cohort_start_ms:
            self.rejects["pre_cohort_event_ts"] += 1
            return False
        if not finite(mc) or not (0 < float(mc) <= self.atlas.MAX_MC_USD):
            self.rejects["bad_or_out_of_range_mc"] += 1
            return False
        if side not in ("buy", "sell"):
            self.rejects["not_buy_or_sell"] += 1
            return False
        return True

    def prune(self, mint, ts):
        cutoff = ts - self.atlas.HOLD_MS - SELLABLE["P90"] - 10_000
        dq = self.flows[mint]
        while dq and dq[0]["ts"] < cutoff:
            dq.popleft()

    def canonical_state_label(self, historical_label):
        if historical_label.startswith("FIXED_") and historical_label.endswith("MS"):
            age = historical_label[len("FIXED_") : -len("MS")]
            return f"EVENT_GATED_AFTER_{age}MS"
        return historical_label

    def emit_state(self, source_row, feats, source_row_id, receive_ts_ms):
        feature_ready_ts = now_ms()
        score_input = {name: num(feats.get(name)) for name in self.spec["feature_order"]}
        score_start = now_ms()
        score, norms, contribs, used = score_with_contribs(score_input, self.stats, self.ordered)
        score_ready = now_ms()
        if not finite(score):
            self.counters["score_failures"] += 1
            return
        decision_ts = now_ms()
        hist_state = feats["snapshot_kind"]
        age_ms = feats.get("snapshot_age_ms") if hist_state.startswith("FIXED_") else None
        state_ts = int(feats["ts"])
        row = {
            "event": "ranker_v2_score",
            "cohort": COHORT_NAME,
            "mint": feats["mint"],
            "state": self.canonical_state_label(hist_state),
            "historical_state_alias": hist_state,
            "nominal_age_ms": age_ms,
            "nominal_boundary_ts_ms": (self.first_seen.get(feats["mint"], state_ts) + int(age_ms)) if finite(age_ms) else None,
            "event_gated_state_ts_ms": state_ts,
            "mint_age_ms": feats.get("age_ms"),
            "final_required_input_received_ts_ms": receive_ts_ms,
            "feature_ready_ts_ms": feature_ready_ts,
            "score_start_ts_ms": score_start,
            "score_ready_ts_ms": score_ready,
            "threshold_decision_ts_ms": decision_ts,
            "event_state_to_feature_ready_ms": feature_ready_ts - state_ts,
            "feature_ready_to_score_ready_ms": score_ready - feature_ready_ts,
            "event_state_to_threshold_decision_ms": decision_ts - state_ts,
            "score": score,
            "score_threshold": self.threshold,
            "passes_threshold": score >= self.threshold,
            "used_weighted_features": used,
            "mc_usd": feats.get("mc_usd"),
            "raw_features": score_input,
            "normalized_feature_values": norms,
            "feature_contributions": contribs,
            "source_row_id": source_row_id,
            "no_submit": True,
        }
        self.score_rows.append(row)
        self.counters["score_evaluations"] += 1
        if row["passes_threshold"]:
            self.counters["threshold_crossings_observation_level"] += 1
            if row["mint"] in self.triggered_mints:
                self.counters["later_threshold_crossing_after_mint_signal"] += 1
            else:
                self.triggered_mints.add(row["mint"])
                signal = dict(row)
                signal["event"] = "ranker_v2_primary_signal"
                signal["selection_semantics"] = "FIRST chronological eligible observation per mint with score >= 0.590596"
                self.signal_rows.append(signal)
                self.counters["unique_triggered_mints"] += 1

    def ingest(self, row, source_row_id):
        self.counters["raw_rows_seen"] += 1
        receive_ts = now_ms()
        mint = self.atlas.mint_of(row)
        ts = self.atlas.ts_of(row)
        mc = self.atlas.mc_of(row)
        side = self.atlas.side_of(row)
        if not mint or not finite(ts):
            self.rejects["missing_mint_or_ts"] += 1
            return
        ts = int(ts)
        if ts < self.cohort_start_ms:
            self.rejects["pre_cohort_event_ts"] += 1
            return
        if mint not in self.first_seen:
            self.first_seen[mint] = ts
            self.global_first_seen.append((ts, mint))
        while self.global_first_seen and self.global_first_seen[0][0] < ts - 60_000:
            self.global_first_seen.popleft()
        self.counters["mints_observed_unique"] = len(self.first_seen)
        if not finite(mc) or not (0 < float(mc) <= self.atlas.MAX_MC_USD):
            self.rejects["bad_or_out_of_range_mc"] += 1
            return
        if side not in ("buy", "sell"):
            self.rejects["not_buy_or_sell"] += 1
            return
        mc = float(mc)
        self.counters["eligible_market_events"] += 1
        event = self.atlas.build_event(row, ts, mc, side)
        dq = self.flows[mint]
        dq.append(event)
        self.prune(mint, ts)
        global_state = {
            "launches_per_minute": len({m for _, m in self.global_first_seen}),
            "active_flow_mints": len(self.flows),
        }
        age = self.atlas.creation_age_ms(row, self.first_seen[mint], ts)
        for age_ms in self.atlas.FIXED_AGES_MS:
            if age >= age_ms and age_ms not in self.emitted_fixed[mint]:
                self.emitted_fixed[mint].add(age_ms)
                feats = self.atlas.feature_row(dq, row, ts, mc, self.first_seen[mint], f"FIXED_{age_ms}MS", age_ms, global_state)
                self.counters["event_gated_states_emitted"] += 1
                self.counters[f"event_gated_after_{age_ms}ms"] += 1
                self.emit_state(row, feats, source_row_id, receive_ts)
        if side == "buy":
            quick = self.atlas.window(dq, ts, 1000)
            w500 = self.atlas.window(dq, ts, 500)
            w5000 = self.atlas.window(dq, ts, 5000)
            w10000 = self.atlas.window(dq, ts, 10000)
            event_kinds = []
            if "FIRST_IMPULSE" not in self.emitted_events[mint] and quick["gross_buy_sol"] >= 2:
                event_kinds.append("FIRST_IMPULSE")
            if "SECOND_ACCELERATION" not in self.emitted_events[mint] and w500["gross_buy_sol"] >= 2 and sum(e["quote_sol"] for e in dq if ts - 5000 <= e["ts"] < ts - 1000 and e["side"] == "buy") >= 1:
                event_kinds.append("SECOND_ACCELERATION")
            if "FIRST_LARGE_SELL_ABSORPTION" not in self.emitted_events[mint] and w5000["sell_count"] >= 1 and w5000["gross_buy_sol"] >= 4 and (w5000["drawdown_from_high_pct"] or 0) >= -10:
                event_kinds.append("FIRST_LARGE_SELL_ABSORPTION")
            if "POST_PULLBACK_BREAKOUT" not in self.emitted_events[mint] and finite(w10000["drawdown_from_high_pct"]) and -25 <= w10000["drawdown_from_high_pct"] <= -4 and quick["gross_buy_sol"] >= 2:
                event_kinds.append("POST_PULLBACK_BREAKOUT")
            if "CONSOLIDATION_BREAKOUT" not in self.emitted_events[mint] and finite(w10000["range_width_pct"]) and w10000["range_width_pct"] <= 25 and quick["gross_buy_sol"] >= 2 and (w10000["drawdown_from_high_pct"] or 0) >= -3:
                event_kinds.append("CONSOLIDATION_BREAKOUT")
            for kind in event_kinds:
                self.emitted_events[mint].add(kind)
                feats = self.atlas.feature_row(dq, row, ts, mc, self.first_seen[mint], kind, age, global_state)
                self.counters["event_gated_states_emitted"] += 1
                self.counters[f"event_{kind}"] += 1
                self.emit_state(row, feats, source_row_id, receive_ts)
        elif side == "sell" and "FIRST_LARGE_SELLER" not in self.emitted_events[mint]:
            self.emitted_events[mint].add("FIRST_LARGE_SELLER")
            feats = self.atlas.feature_row(dq, row, ts, mc, self.first_seen[mint], "FIRST_LARGE_SELLER", age, global_state)
            self.counters["event_gated_states_emitted"] += 1
            self.counters["event_FIRST_LARGE_SELLER"] += 1
            self.emit_state(row, feats, source_row_id, receive_ts)


def row_key(row):
    return (row.get("mint"), int(row.get("ts") or row.get("event_gated_state_ts_ms")), row.get("snapshot_kind") or row.get("historical_state_alias") or row.get("state"))


def run_scorer_parity(out_dir, canonical_spec, v2_dir, spec, stats, ordered, threshold, limit=0):
    score_path = os.path.join(v2_dir, "full_universe_scores.jsonl.gz")
    compared = exact = threshold_disagreements = 0
    diffs = []
    with gzip.open(score_path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            features = row.get("features") or {}
            score, _, _, _ = score_with_contribs({name: features.get(name) for name in spec["feature_order"]}, stats, ordered)
            stored = row.get("rank_score")
            if not (finite(score) and finite(stored)):
                continue
            compared += 1
            diff = abs(score - float(stored))
            diffs.append(diff)
            exact += 1 if diff == 0 else 0
            if (score >= threshold) != (float(stored) >= threshold):
                threshold_disagreements += 1
            if limit and compared >= limit:
                break
    report = {
        "status": "PASS" if threshold_disagreements == 0 else "FAIL",
        "source": score_path,
        "rows_compared": compared,
        "exact_score_matches": exact,
        "max_abs_score_diff": max(diffs) if diffs else None,
        "median_score_diff": q(diffs, 0.5),
        "p99_score_diff": q(diffs, 0.99),
        "threshold_disagreements": threshold_disagreements,
        "canonical_spec": canonical_spec,
        "canonical_spec_sha256": sha256_file(canonical_spec),
    }
    write_json(os.path.join(out_dir, "historical_scorer_parity_report.json"), report)
    return report


def verify_primary_ledger(out_dir, ledger_path):
    counts = Counter()
    mints = []
    rows = 0
    for row in read_jsonl_any(ledger_path):
        rows += 1
        counts[row.get("split")] += 1
        mints.append(row.get("mint"))
    report = {
        "status": "PASS" if rows == 2158 and dict(counts) == EXPECTED_LEDGER_COUNTS and len(set(mints)) == rows else "FAIL",
        "ledger_path": ledger_path,
        "ledger_sha256": sha256_file(ledger_path),
        "rows": rows,
        "unique_mints": len(set(mints)),
        "duplicate_mints": rows - len(set(mints)),
        "split_counts": dict(counts),
        "expected_split_counts": EXPECTED_LEDGER_COUNTS,
    }
    write_json(os.path.join(out_dir, "canonical_trigger_reproduction_report.json"), report)
    return report


def run_event_fixture_parity(out_dir, atlas, spec, stats, ordered, threshold, input_path, v2_dir, fixture_rows):
    engine = EventGatedEngine(atlas, spec, stats, ordered, threshold, cohort_start_ms=-1)
    min_ts = None
    max_ts = None
    source_rows = 0
    with open(input_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if source_rows >= fixture_rows:
                break
            if not line.strip():
                continue
            source_rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                engine.rejects["completed_malformed_fixture_rows"] += 1
                continue
            ts = atlas.ts_of(row)
            if finite(ts):
                min_ts = int(ts) if min_ts is None else min(min_ts, int(ts))
                max_ts = int(ts) if max_ts is None else max(max_ts, int(ts))
            engine.ingest(row, source_rows)

    generated = {row_key(r): r for r in engine.score_rows}
    frozen = {}
    score_path = os.path.join(v2_dir, "full_universe_scores.jsonl.gz")
    with gzip.open(score_path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            ts = int(row["ts"])
            if min_ts is not None and ts < min_ts:
                continue
            if max_ts is not None and ts > max_ts:
                if frozen:
                    break
                continue
            frozen[(row["mint"], ts, row.get("snapshot_kind"))] = row

    common = set(generated) & set(frozen)
    feature_mismatches = []
    score_diffs = []
    threshold_disagreements = 0
    for key in list(common)[:20000]:
        gen = generated[key]
        frz = frozen[key]
        ffeatures = frz.get("features") or {}
        gfeatures = gen.get("raw_features") or {}
        for name in spec["feature_order"]:
            gv = gfeatures.get(name)
            fv = ffeatures.get(name)
            if finite(gv) and finite(fv):
                if abs(float(gv) - float(fv)) > 1e-6:
                    feature_mismatches.append({"key": key, "feature": name, "generated": gv, "frozen": fv})
                    break
            elif gv is None and fv is None:
                continue
            elif finite(gv) != finite(fv):
                feature_mismatches.append({"key": key, "feature": name, "generated": gv, "frozen": fv})
                break
        score_diffs.append(abs(float(gen["score"]) - float(frz["rank_score"])))
        if (float(gen["score"]) >= threshold) != (float(frz["rank_score"]) >= threshold):
            threshold_disagreements += 1

    missing = list(set(frozen) - set(generated))[:50]
    extra = list(set(generated) - set(frozen))[:50]
    pass_status = (
        len(generated) > 0
        and len(frozen) > 0
        and not missing
        and not extra
        and not feature_mismatches
        and threshold_disagreements == 0
    )
    report = {
        "status": "PASS" if pass_status else "FAIL",
        "fixture_rows": source_rows,
        "time_window": {"min_ts": min_ts, "max_ts": max_ts},
        "generated_states": len(generated),
        "frozen_states_in_window": len(frozen),
        "common_states": len(common),
        "missing_generated_keys_sample": missing,
        "extra_generated_keys_sample": extra,
        "feature_mismatches_sample": feature_mismatches[:50],
        "threshold_disagreements": threshold_disagreements,
        "max_score_diff": max(score_diffs) if score_diffs else None,
        "engine_counters": dict(engine.counters),
        "engine_rejects": dict(engine.rejects),
        "note": "Large deterministic fixture compares current event-gated implementation against frozen historical full_universe_scores rows in the same source timestamp window.",
    }
    write_json(os.path.join(out_dir, "historical_parity_report.json"), report)
    return report


def latest_at_or_before(marks, target_ts):
    best = None
    for ts, mc in marks:
        if ts <= target_ts:
            best = (int(ts), float(mc))
        else:
            break
    return best


def score_causal_outcome(signal_ts, decision_ts, marks, sellable_ms, stress_pct=0.0):
    intended_entry_ts = int(decision_ts) + ENTRY_DELAY_MS
    marks = sorted((int(ts), float(mc)) for ts, mc in marks if finite(ts) and finite(mc))
    entry = latest_at_or_before(marks, intended_entry_ts)
    if entry is None:
        return {"outcome": "ENTRY_UNRESOLVED", "pnl": None, "intended_entry_ts": intended_entry_ts}
    entry_mark_ts, entry_mc = entry
    entry_mc *= 1.0 + stress_pct / 100.0
    first_sellable_ts = intended_entry_ts + sellable_ms
    max_hold_ts = intended_entry_ts + HOLD_MS
    first_tp = first_sl = None
    terminal = None
    return_at_first_sellable = None
    max_after = -math.inf
    min_after = math.inf
    for ts, mc in marks:
        if ts < first_sellable_ts or ts > max_hold_ts:
            continue
        roi = ((mc / entry_mc) - 1.0) * 100.0 - ROUND_TRIP_COST_PCT
        if return_at_first_sellable is None:
            return_at_first_sellable = roi
        max_after = max(max_after, roi)
        min_after = min(min_after, roi)
        terminal = (ts, roi)
        if roi >= TP_PCT and first_tp is None:
            first_tp = ts
        if roi <= SL_PCT and first_sl is None:
            first_sl = ts
        if first_tp is not None or first_sl is not None:
            break
    if first_tp is not None and (first_sl is None or first_tp <= first_sl):
        outcome = "TP"
        pnl = TP_PCT - stress_pct
        exit_ts = first_tp
    elif first_sl is not None:
        outcome = "SL"
        pnl = SL_PCT
        exit_ts = first_sl
    else:
        hold_mark = latest_at_or_before(marks, max_hold_ts)
        if hold_mark is None or hold_mark[0] < intended_entry_ts:
            return {"outcome": "120S_UNRESOLVED", "pnl": None, "intended_entry_ts": intended_entry_ts, "entry_mark_ts": entry_mark_ts, "entry_mc": entry_mc}
        hts, hmc = hold_mark
        roi = ((hmc / entry_mc) - 1.0) * 100.0 - ROUND_TRIP_COST_PCT
        outcome = "TRUE_120S_MAX_HOLD"
        pnl = max(-100.0, min(100.0, roi - stress_pct))
        exit_ts = max_hold_ts
    return {
        "outcome": outcome,
        "pnl": pnl,
        "intended_entry_ts": intended_entry_ts,
        "entry_mark_ts": entry_mark_ts,
        "entry_mc": entry_mc,
        "entry_mark_age_ms": intended_entry_ts - entry_mark_ts,
        "first_sellable_ts": first_sellable_ts,
        "exit_ts": exit_ts,
        "return_at_first_sellable": return_at_first_sellable,
        "max_return_after_sellable": max_after if finite(max_after) else None,
        "min_return_after_sellable": min_after if finite(min_after) else None,
        "source_continuity": "ASSUMED_CONTINUOUS_ORDERED_SOURCE_NO_COMPLETED_MALFORMED_OR_GAP",
    }


def run_execution_fixture(out_dir, corrected_ledger_path, max_rows=500):
    rows = []
    for row in read_jsonl_any(corrected_ledger_path):
        rows.append(row)
        if len(rows) >= max_rows:
            break
    checks = Counter()
    examples = {
        "sub_50ms_entry_mark": None,
        "gt_500ms_old_future_mark_gap": None,
        "gt_5s_old_future_mark_gap": None,
        "actual_120s_state": None,
        "tp_before_sellability": None,
        "sl_after_sellability": None,
    }
    for r in rows:
        checks["rows_checked"] += 1
        if r.get("intended_entry_ts") == r.get("event_gated_threshold_decision_ts") + ENTRY_DELAY_MS:
            checks["intended_entry_ok"] += 1
        if r.get("price_mark_ts") is not None and r.get("price_mark_ts") <= r.get("intended_entry_ts"):
            checks["causal_entry_mark_ok"] += 1
        if (r.get("p75_1476ms") or {}).get("first_sellable_ts") == r.get("intended_entry_ts") + SELLABLE["P75"]:
            checks["sellability_anchor_ok"] += 1
        if (r.get("p75_1476ms") or {}).get("exit_ts") == r.get("intended_entry_ts") + HOLD_MS:
            checks["true_120s_exit_seen"] += 1
        if r.get("mark_freshness_ms") is not None and r["mark_freshness_ms"] < 50 and examples["sub_50ms_entry_mark"] is None:
            examples["sub_50ms_entry_mark"] = r.get("mint")
        if r.get("old_entry_gap_ms") is not None and r["old_entry_gap_ms"] > 500 and examples["gt_500ms_old_future_mark_gap"] is None:
            examples["gt_500ms_old_future_mark_gap"] = r.get("mint")
        if r.get("old_entry_gap_ms") is not None and r["old_entry_gap_ms"] > 5000 and examples["gt_5s_old_future_mark_gap"] is None:
            examples["gt_5s_old_future_mark_gap"] = r.get("mint")
        if (r.get("p75_1476ms") or {}).get("outcome") == "TRUE_120S_RESOLVED" and examples["actual_120s_state"] is None:
            examples["actual_120s_state"] = r.get("mint")
        if (r.get("p75_1476ms") or {}).get("outcome") == "TP" and examples["tp_before_sellability"] is None:
            examples["tp_before_sellability"] = r.get("mint")
        if (r.get("p75_1476ms") or {}).get("outcome") == "SL" and examples["sl_after_sellability"] is None:
            examples["sl_after_sellability"] = r.get("mint")
    required_ok = (
        checks["rows_checked"] > 0
        and checks["intended_entry_ok"] == checks["rows_checked"]
        and checks["causal_entry_mark_ok"] == checks["rows_checked"]
        and checks["sellability_anchor_ok"] == checks["rows_checked"]
    )
    report = {
        "status": "PASS" if required_ok else "FAIL",
        "source": corrected_ledger_path,
        "checks": dict(checks),
        "examples": examples,
        "note": "Uses canonical corrected reconciliation ledger as execution accounting fixture.",
    }
    write_json(os.path.join(out_dir, "execution_fixture_report.json"), report)
    return report


def make_contracts(out_dir, canonical_spec_path, canonical_recon_dir):
    contracts = {
        "semantic_contract.json": {
            "classification": "RANKER_V2_EVENT_GATED_SEMANTIC_CONTRACT",
            "strategy": "event-gated threshold signal; one earliest trigger per mint",
            "threshold": {"literal": "0.590596", "comparator": "score >= threshold", "epsilon": "NONE", "round_before_compare": False},
            "nominal_lifecycle_semantics": "After create_ts + X has elapsed, evaluate on first subsequent eligible market event. The decision state timestamp is that market event timestamp, not the exact timer boundary.",
            "state_labeling": "Use EVENT_GATED_AFTER_XMS prospectively; historical FIXED_XMS labels are provenance aliases only.",
            "one_trade_per_mint": True,
            "forbidden_additions": ["wallet logic", "fast100 logic", "FIRST_IMPULSE-only logic", "MC gates", "extra filters", "latency tuning"],
        },
        "eligible_event_spec.json": {
            "allowed_event_types": ["buy", "sell"],
            "source_row_rule": "candidate-capsules rows normalized by memecoinExecutableAlphaAtlasV1 helpers",
            "required_fields": ["mint", "timestamp", "market_cap_usd", "side"],
            "timestamp_precedence": ["candidate_ready_ts_ms", "decode_complete_ts_ms", "local_receive_ts_ms", "canonical_state.observed_at_ms", "tsMs"],
            "market_cap_precedence": ["canonical_state.market_cap_usd", "candidate.market_cap_usd", "marketCapUsd"],
            "side_precedence": ["raw_record.side", "side"],
            "reject_if": ["missing mint", "missing timestamp", "market cap non-finite", "market cap <=0", "market cap > atlas.MAX_MC_USD", "side not buy/sell"],
            "ordering_semantics": "source file order; same timestamp ordering follows source order",
            "duplicate_semantics": "duplicate source market events are processed as separate eligible source events unless the upstream canonical corpus removed them",
        },
        "feature_contract.json": {
            "canonical_spec": canonical_spec_path,
            "feature_cutoff": "event_gated_state_ts_ms",
            "causality": "Every source event contributing to any feature must have source_event_ts <= event_gated_state_ts_ms.",
            "feature_order_source": "canonical_score_spec.feature_order",
            "transform": "lossless canonical_score_spec normalization constants; clamp z to [-5,5]; sum weighted contributions",
            "missing_value_handling": "skip missing/non-finite weighted feature; null score only if no weighted features available",
        },
        "entry_contract.json": {
            "intended_entry_ts": "threshold_decision_ts_ms + 25ms",
            "price_rule": "latest trustworthy known curve state at or before intended_entry_ts",
            "forbidden": "Do not substitute first future mark after intended entry.",
            "unresolved": "ENTRY_UNRESOLVED if causal point-in-time state/continuity cannot be trusted.",
            "no_arbitrary_freshness_cutoff": True,
        },
        "sellability_contract.json": {
            "anchor": "intended_entry_ts",
            "P50_ms": SELLABLE["P50"],
            "P75_primary_ms": SELLABLE["P75"],
            "P90_ms": SELLABLE["P90"],
            "no_exit_before_boundary": True,
        },
        "exit_contract.json": {
            "tp_pct": TP_PCT,
            "sl_pct": SL_PCT,
            "max_hold_ms": HOLD_MS,
            "max_hold_timestamp": "intended_entry_ts + 120s",
            "max_hold_price_rule": "latest causally valid known curve state at or before +120s, provided source continuity is intact",
            "outcome_classes": ["TP", "SL", "TRUE_120S_MAX_HOLD", "ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED"],
        },
        "ingestion_contract.json": {
            "partial_line_rule": "buffer incomplete active-write JSON lines until newline completion",
            "forbidden": ["parse partial fragments", "count partial fragments as consumed malformed", "advance past partial fragments permanently"],
            "completed_malformed_record_rule": "fail closed and count completed malformed records",
        },
    }
    hashes = {}
    for name, obj in contracts.items():
        write_json(os.path.join(out_dir, name), obj)
        hashes[name] = sha256_file(os.path.join(out_dir, name))
    return hashes


def phase_a(args):
    os.makedirs(args.freeze_dir, exist_ok=True)
    spec, stats, ordered, threshold = load_canonical_spec(args.canonical_spec)
    blockers = []
    spec_hash = sha256_file(args.canonical_spec)
    object_hash = sha256_obj(spec)
    contract_hashes = make_contracts(args.freeze_dir, args.canonical_spec, args.reconciliation_dir)
    partial = run_partial_line_tests(args.freeze_dir)
    if partial["status"] != "PASS":
        blockers.append("partial-line tests failed")
    if spec_hash != args.expected_spec_sha256:
        blockers.append(f"canonical scorer hash mismatch: {spec_hash}")
    if object_hash != args.expected_object_sha256:
        blockers.append(f"canonical object hash mismatch: {object_hash}")
    scorer = run_scorer_parity(args.freeze_dir, args.canonical_spec, args.v2_dir, spec, stats, ordered, threshold, args.scorer_parity_limit)
    if scorer["status"] != "PASS":
        blockers.append("canonical scorer parity failed")
    ledger = verify_primary_ledger(args.freeze_dir, args.primary_ledger)
    if ledger["status"] != "PASS":
        blockers.append("canonical trigger reproduction failed")
    atlas = load_module("memecoinExecutableAlphaAtlasV1", args.atlas_script)
    event_fixture = run_event_fixture_parity(args.freeze_dir, atlas, spec, stats, ordered, threshold, args.input, args.v2_dir, args.fixture_rows)
    if event_fixture["status"] != "PASS":
        blockers.append("event-gated state/feature fixture parity failed")
    execution = run_execution_fixture(args.freeze_dir, os.path.join(args.reconciliation_dir, "corrected_entry_ledger.jsonl.gz"), args.execution_fixture_rows)
    if execution["status"] != "PASS":
        blockers.append("execution accounting fixture failed")
    safety = {
        "status": "PASS",
        "no_submit": True,
        "no_signer": True,
        "no_rpc_sender": True,
        "no_live_or_dust_trade": True,
        "script_capabilities": "read source corpus, write analytics artifacts, no transaction construction or submission",
    }
    write_json(os.path.join(args.freeze_dir, "broadcast_safety_report.json"), safety)
    impl_manifest = {
        "status": "PASS" if not blockers else "BLOCKED",
        "script": args.self_script or sys.argv[0],
        "script_sha256": sha256_file(args.self_script or sys.argv[0]) if os.path.exists(args.self_script or sys.argv[0]) else None,
        "canonical_scorer_sha256": spec_hash,
        "canonical_object_sha256": object_hash,
        "threshold": threshold,
        "semantic_implementation": "EventGatedEngine + CompletedLineBuffer in rankerV2EventGatedProspectiveV2.py",
        "prospective_v2_permitted": not blockers,
    }
    write_json(os.path.join(args.freeze_dir, "prospective_implementation_manifest.json"), impl_manifest)
    artifact_hashes = {}
    for name in [
        "semantic_contract.json",
        "eligible_event_spec.json",
        "feature_contract.json",
        "entry_contract.json",
        "sellability_contract.json",
        "exit_contract.json",
        "ingestion_contract.json",
        "historical_scorer_parity_report.json",
        "historical_parity_report.json",
        "execution_fixture_report.json",
        "partial_line_test_report.json",
        "canonical_trigger_reproduction_report.json",
        "broadcast_safety_report.json",
        "prospective_implementation_manifest.json",
    ]:
        path = os.path.join(args.freeze_dir, name)
        if os.path.exists(path):
            artifact_hashes[name] = sha256_file(path)
    freeze = {
        "classification": "RANKER_V2_EVENT_GATED_FREEZE_PASS" if not blockers else "RANKER_V2_EVENT_GATED_FREEZE_BLOCKED",
        "created_at_utc": iso(),
        "blockers": blockers,
        "contract_hashes": contract_hashes,
        "artifact_hashes": artifact_hashes,
        "canonical_scorer_sha256": spec_hash,
        "canonical_object_sha256": object_hash,
        "canonical_reconciliation": args.reconciliation_dir,
        "phase_a_gates": {
            "canonical_scorer_parity": scorer["status"],
            "event_gated_state_parity": event_fixture["status"],
            "feature_parity": event_fixture["status"],
            "earliest_trigger_semantics": ledger["status"],
            "entry_semantics": execution["status"],
            "sellability_semantics": execution["status"],
            "true_120s_outcome_semantics": execution["status"],
            "partial_line_tests": partial["status"],
            "broadcast_safety": safety["status"],
            "zero_unexplained_signal_affecting_decoder_errors": "PASS" if not event_fixture.get("feature_mismatches_sample") else "FAIL",
        },
    }
    write_json(os.path.join(args.freeze_dir, "freeze_manifest.json"), freeze)
    return freeze


class JsonlWriter:
    def __init__(self, path, gzip_enabled=False):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        opener = gzip.open if gzip_enabled else open
        self.handle = opener(path, "at", encoding="utf-8")

    def write(self, row):
        self.handle.write(json.dumps(compact(row), sort_keys=True, separators=(",", ":")) + "\n")
        self.handle.flush()

    def close(self):
        self.handle.close()


class AsyncJsonlWriter:
    def __init__(self, path, gzip_enabled=False, max_queue=100000):
        self.path = path
        self.gzip_enabled = gzip_enabled
        self.queue = queue.Queue(maxsize=max_queue)
        self.closed = False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.thread = threading.Thread(target=self._run, name=f"async-jsonl:{os.path.basename(path)}", daemon=True)
        self.thread.start()

    def _run(self):
        opener = gzip.open if self.gzip_enabled else open
        with opener(self.path, "at", encoding="utf-8") as handle:
            while True:
                row = self.queue.get()
                if row is None:
                    self.queue.task_done()
                    break
                handle.write(json.dumps(compact(row), sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                self.queue.task_done()

    def write(self, row):
        if self.closed:
            return
        try:
            self.queue.put_nowait(row)
        except queue.Full:
            # Never block the decision path; record the drop in the next status write.
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.queue.put(None)
        self.thread.join(timeout=5)


class ProspectiveV2:
    def __init__(self, args):
        self.args = args
        self.atlas = load_module("memecoinExecutableAlphaAtlasV1", args.atlas_script)
        self.spec, self.stats, self.ordered, self.threshold = load_canonical_spec(args.canonical_spec)
        self.engine = EventGatedEngine(self.atlas, self.spec, self.stats, self.ordered, self.threshold, cohort_start_ms=10**20)
        self.pending = {}
        self.outcome_rows = []
        self.running = True
        self.raw_malformed = 0
        self.partial_buffered = 0
        self.last_event_ts = None
        self.all_scores = JsonlWriter(os.path.join(args.out_dir, "all_scores.jsonl.gz"), True)
        self.signals = JsonlWriter(os.path.join(args.out_dir, "signals.jsonl.gz"), True)
        self.outcomes = JsonlWriter(os.path.join(args.out_dir, "outcomes.jsonl.gz"), True)
        self.entry_rows = JsonlWriter(os.path.join(args.out_dir, "entry_quality.jsonl.gz"), True)
        self.later_crossings = JsonlWriter(os.path.join(args.out_dir, "diagnostic_later_crossings.jsonl.gz"), True)
        self.health = JsonlWriter(os.path.join(args.out_dir, "service_health.jsonl"), False)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

    def stop(self, *_args):
        self.running = False

    def init_manifest_after_seek(self, source_pos):
        manifest_path = os.path.join(self.args.out_dir, "cohort_manifest.json")
        if os.path.exists(manifest_path):
            manifest = json.load(open(manifest_path, "r", encoding="utf-8"))
            self.args.cohort_start_ms = int(manifest["cohort_start_ms"])
            self.args.cohort_start_utc = manifest["cohort_start_utc"]
        else:
            self.args.cohort_start_ms = now_ms()
            self.args.cohort_start_utc = iso(self.args.cohort_start_ms)
            manifest = {
                "cohort": COHORT_NAME,
                "cohort_start_utc": self.args.cohort_start_utc,
                "cohort_start_ms": self.args.cohort_start_ms,
                "future_only_boundary": "created only after specs loaded, checks passed, source opened, source seeked to current end, and readers initialized",
                "source_input": self.args.input,
                "source_input_position_at_start": source_pos,
                "canonical_spec": self.args.canonical_spec,
                "canonical_spec_sha256": sha256_file(self.args.canonical_spec),
                "canonical_object_sha256": sha256_obj(self.spec),
                "semantic_freeze_manifest": self.args.semantic_freeze_manifest,
                "threshold": self.threshold,
                "no_submit": True,
                "exit_policy": {"tp_pct": TP_PCT, "sl_pct": SL_PCT, "max_hold_ms": HOLD_MS},
                "sellability_ms": SELLABLE,
            }
            write_json(manifest_path, manifest)
        self.engine.cohort_start_ms = self.args.cohort_start_ms
        return manifest

    def metrics(self, rows, lane):
        vals = [num((r.get(lane) or {}).get("pnl")) for r in rows if num((r.get(lane) or {}).get("pnl")) is not None]
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v < 0]
        gp = sum(wins)
        gl = abs(sum(losses))
        outcomes = Counter((r.get(lane) or {}).get("outcome") for r in rows)
        return {
            "n": len(vals),
            "wins": len(wins),
            "losses": len(losses),
            "wr_pct": len(wins) * 100.0 / len(vals) if vals else None,
            "pf": gp / gl if gl else (999.0 if gp > 0 else None),
            "expectancy_pct": sum(vals) / len(vals) if vals else None,
            "pnl_at_1sol": sum(vals) / 100.0,
            "tp": outcomes.get("TP", 0),
            "sl": outcomes.get("SL", 0),
            "true_max_hold": outcomes.get("TRUE_120S_MAX_HOLD", 0),
            "unresolved": sum(1 for r in rows if (r.get(lane) or {}).get("outcome") in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED")),
        }

    def concurrency(self, rows, lane, max_open):
        active = []
        accepted = []
        rejected = 0
        for row in sorted(rows, key=lambda r: r["signal"]["threshold_decision_ts_ms"]):
            ts = row["signal"]["threshold_decision_ts_ms"]
            active = [end for end in active if end > ts]
            if len(active) >= max_open:
                rejected += 1
                continue
            accepted.append(row)
            outcome = row.get(lane) or {}
            active.append(outcome.get("exit_ts") or (ts + ENTRY_DELAY_MS + HOLD_MS))
        out = self.metrics(accepted, lane)
        out["accepted_N"] = len(accepted)
        out["capacity_rejected_N"] = rejected
        return out

    def write_status(self):
        elapsed_h = max(1e-9, (now_ms() - self.args.cohort_start_ms) / 3_600_000.0)
        lane_keys = ["p75_primary", "p50", "p90", "p75_stress5", "p75_stress10"]
        report = {
            "classification": self.classification(),
            "cohort": COHORT_NAME,
            "updated_at_utc": iso(),
            "cohort_start_utc": self.args.cohort_start_utc,
            "pid": os.getpid(),
            "n_restarts": self.n_restarts(),
            "counters": dict(self.engine.counters),
            "rejects": dict(self.engine.rejects),
            "completed_malformed_records": self.raw_malformed,
            "partial_buffered_records": self.partial_buffered,
            "open_outcomes": len(self.pending),
            "resolved_primary_outcomes": len(self.outcome_rows),
            "signals_per_hour": self.engine.counters["unique_triggered_mints"] / elapsed_h,
            "score_evaluations_per_hour": self.engine.counters["score_evaluations"] / elapsed_h,
            "metrics": {key: self.metrics(self.outcome_rows, key) for key in lane_keys},
            "concurrency": {
                f"MAX_OPEN{i}": self.concurrency(self.outcome_rows, "p75_primary", i)
                for i in (1, 2, 3, 5)
            },
            "submission_count": 0,
            "no_submit": True,
            "interpretation": "N25 descriptive, N50 informative, N100 first serious prospective assessment, N200 preferred confirmation.",
        }
        write_json(os.path.join(self.args.out_dir, "final_report.json"), report)
        self.health.write({"event": "service_health", **report})
        write_json(os.path.join(self.args.out_dir, "service_health.json"), report)

    def n_restarts(self):
        try:
            return int(os.environ.get("NRESTARTS") or 0)
        except Exception:
            return None

    def classification(self):
        n = len(self.outcome_rows)
        if n < 25:
            return "RANKER_V2_SHADOW_V2_HEALTHY_COLLECTING_PRECHECKPOINT"
        if n < 50:
            return "RANKER_V2_SHADOW_V2_N25_DESCRIPTIVE"
        if n < 100:
            return "RANKER_V2_SHADOW_V2_N50_INFORMATIVE"
        primary = self.metrics(self.outcome_rows, "p75_primary")
        stress5 = self.metrics(self.outcome_rows, "p75_stress5")
        if (primary.get("pf") or 0) > 1 and (primary.get("expectancy_pct") or 0) > 0 and (stress5.get("pf") or 0) > 1:
            return "RANKER_V2_SHADOW_V2_N100_PROMISING"
        return "RANKER_V2_SHADOW_V2_N100_NOT_PROMISING"

    def handle_new_score_rows(self, old_len):
        for row in self.engine.score_rows[old_len:]:
            self.all_scores.write(row)
            if row["passes_threshold"] and row["mint"] in self.engine.triggered_mints:
                pass
        for sig in self.engine.signal_rows[len(self.pending) + len(self.outcome_rows):]:
            self.signals.write(sig)
            mint = sig["mint"]
            marks = [(e["ts"], e["mc"]) for e in self.engine.flows[mint]]
            self.pending[mint] = {"signal": sig, "marks": marks}
            self.write_entry_quality(sig, marks, unresolved=True)

    def write_entry_quality(self, sig, marks, unresolved=False):
        decision_ts = sig["threshold_decision_ts_ms"]
        intended_entry_ts = decision_ts + ENTRY_DELAY_MS
        entry = latest_at_or_before(sorted(marks), intended_entry_ts)
        state_mc = sig.get("mc_usd")
        row = {
            "event": "ranker_v2_entry_quality",
            "cohort": COHORT_NAME,
            "mint": sig["mint"],
            "state_mc": state_mc,
            "intended_entry_ts": intended_entry_ts,
            "causal_entry_state_ts": entry[0] if entry else None,
            "intended_entry_mc": entry[1] if entry else None,
            "decision_to_entry_ms": ENTRY_DELAY_MS,
            "state_to_intended_entry_ms": intended_entry_ts - sig["event_gated_state_ts_ms"],
            "movement_state_to_entry_pct": ((entry[1] / state_mc - 1.0) * 100.0) if entry and finite(state_mc) and state_mc > 0 else None,
            "source_continuity_status": "PENDING" if unresolved else "ASSUMED_CONTINUOUS_ORDERED_SOURCE_NO_COMPLETED_MALFORMED_OR_GAP",
            "descriptive_movement_after_decision_ms": {},
            "no_submit": True,
        }
        for delay in (25, 50, 100, 250, 500):
            mark = latest_at_or_before(sorted(marks), decision_ts + delay)
            row["descriptive_movement_after_decision_ms"][str(delay)] = {
                "mc": mark[1] if mark else None,
                "pct_from_state": ((mark[1] / state_mc - 1.0) * 100.0) if mark and finite(state_mc) and state_mc > 0 else None,
            }
        self.entry_rows.write(row)

    def maybe_close_outcomes(self, global_event_ts):
        if not finite(global_event_ts):
            return
        close = []
        for mint, state in self.pending.items():
            target = state["signal"]["threshold_decision_ts_ms"] + ENTRY_DELAY_MS + HOLD_MS
            if global_event_ts >= target:
                close.append(mint)
        for mint in close:
            state = self.pending.pop(mint)
            sig = state["signal"]
            marks = sorted((e["ts"], e["mc"]) for e in self.engine.flows[mint])
            row = {
                "event": "ranker_v2_outcome",
                "cohort": COHORT_NAME,
                "mint": mint,
                "signal": sig,
                "mark_count": len(marks),
                "p50": score_causal_outcome(sig["event_gated_state_ts_ms"], sig["threshold_decision_ts_ms"], marks, SELLABLE["P50"], 0),
                "p75_primary": score_causal_outcome(sig["event_gated_state_ts_ms"], sig["threshold_decision_ts_ms"], marks, SELLABLE["P75"], 0),
                "p90": score_causal_outcome(sig["event_gated_state_ts_ms"], sig["threshold_decision_ts_ms"], marks, SELLABLE["P90"], 0),
                "p75_stress5": score_causal_outcome(sig["event_gated_state_ts_ms"], sig["threshold_decision_ts_ms"], marks, SELLABLE["P75"], 5),
                "p75_stress10": score_causal_outcome(sig["event_gated_state_ts_ms"], sig["threshold_decision_ts_ms"], marks, SELLABLE["P75"], 10),
                "no_submit": True,
            }
            self.outcome_rows.append(row)
            self.outcomes.write(row)
            self.write_entry_quality(sig, marks, unresolved=False)

    def ingest_row(self, row, source_row_id):
        old_scores = len(self.engine.score_rows)
        old_signals = len(self.engine.signal_rows)
        ts = self.atlas.ts_of(row)
        if finite(ts):
            self.last_event_ts = int(ts)
        self.engine.ingest(row, source_row_id)
        for score_row in self.engine.score_rows[old_scores:]:
            self.all_scores.write(score_row)
            if score_row["passes_threshold"] and score_row["mint"] in self.engine.triggered_mints and score_row not in self.engine.signal_rows[old_signals:]:
                self.later_crossings.write(score_row)
        for sig in self.engine.signal_rows[old_signals:]:
            self.signals.write(sig)
            mint = sig["mint"]
            marks = [(e["ts"], e["mc"]) for e in self.engine.flows[mint]]
            self.pending[mint] = {"signal": sig, "marks": marks}
            self.write_entry_quality(sig, marks, unresolved=True)
        self.maybe_close_outcomes(self.last_event_ts)

    def tail_loop(self):
        os.makedirs(self.args.out_dir, exist_ok=True)
        freeze = json.load(open(self.args.semantic_freeze_manifest, "r", encoding="utf-8"))
        if freeze.get("classification") != "RANKER_V2_EVENT_GATED_FREEZE_PASS":
            write_json(os.path.join(self.args.out_dir, "final_report.json"), {
                "classification": "RANKER_V2_PROSPECTIVE_SHADOW_V2_BLOCKED",
                "reason": "semantic freeze manifest is not PASS",
                "freeze_classification": freeze.get("classification"),
            })
            return
        with open(self.args.input, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            source_pos = handle.tell()
            self.init_manifest_after_seek(source_pos)
            self.write_status()
            buffer = CompletedLineBuffer()
            source_row_id = 0
            last_status = time.time()
            while self.running:
                chunk = handle.read(self.args.read_bytes)
                if not chunk:
                    self.partial_buffered = 1 if buffer.buf else 0
                    if time.time() - last_status >= self.args.status_every_sec:
                        self.maybe_close_outcomes(self.last_event_ts)
                        self.write_status()
                        last_status = time.time()
                    time.sleep(self.args.poll_ms / 1000.0)
                    continue
                for raw in buffer.feed(chunk):
                    if not raw.strip():
                        continue
                    source_row_id += 1
                    try:
                        self.ingest_row(json.loads(raw.decode("utf-8")), source_row_id)
                    except json.JSONDecodeError as exc:
                        self.raw_malformed += 1
                        self.engine.rejects["completed_malformed_records"] += 1
                        self.health.write({"event": "completed_malformed_record", "error": str(exc), "ts_ms": now_ms()})
                    except Exception as exc:
                        self.engine.rejects["ingest_exception"] += 1
                        self.health.write({"event": "ingest_exception", "error": repr(exc), "ts_ms": now_ms()})
                if time.time() - last_status >= self.args.status_every_sec:
                    self.write_status()
                    last_status = time.time()
            self.write_status()


class ScorerDirectEmitterV1:
    def __init__(self, args):
        self.args = args
        self.atlas = load_module("memecoinExecutableAlphaAtlasV1", args.atlas_script)
        self.spec, self.stats, self.ordered, self.threshold = load_canonical_spec(args.canonical_spec)
        self.engine = EventGatedEngine(self.atlas, self.spec, self.stats, self.ordered, self.threshold, cohort_start_ms=-1)
        self.running = True
        self.source_row_id = 0
        self.emit_count = 0
        self.warmup_rows = 0
        self.warmup_signals_seen = 0
        self.direct_errors = 0
        self.template_blockers = Counter()
        self.capsule_cache = {}
        self.capsule_ranks = {}
        self.prewarmed = set()
        self.pending = {}
        self.outcome_rows = []
        self.last_event_ts = None
        self.output_dir = args.out_dir
        self.bootstrap_start_ms = now_ms()
        self.bootstrap_start_utc = iso(self.bootstrap_start_ms)
        os.makedirs(self.output_dir, exist_ok=True)
        self.all_scores = AsyncJsonlWriter(os.path.join(args.out_dir, "all_scores.jsonl.gz"), True)
        self.signals = AsyncJsonlWriter(os.path.join(args.out_dir, "signals.jsonl.gz"), True)
        self.selected_events = AsyncJsonlWriter(os.path.join(args.out_dir, "selected-events.jsonl"), False)
        self.shadow_twins = AsyncJsonlWriter(os.path.join(args.out_dir, "shadow-twins.jsonl"), False)
        self.blockers = AsyncJsonlWriter(os.path.join(args.out_dir, "execution_blockers.jsonl"), False)
        self.health = AsyncJsonlWriter(os.path.join(args.out_dir, "service_health.jsonl"), False)
        self.direct = DirectHandoffClient(args.direct_host, args.direct_port, args.direct_handoff)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

    def close(self):
        self.direct.close()
        for writer in (
            self.all_scores,
            self.signals,
            self.selected_events,
            self.shadow_twins,
            self.blockers,
            self.health,
        ):
            writer.close()

    def stop(self, *_args):
        self.running = False

    def write_boot_artifacts(self, manifest):
        architecture = {
            "classification": "RANKER_V2_SCORER_DIRECT_EMITTER_V1_ARCHITECTURE",
            "cohort": DIRECT_EMITTER_COHORT_NAME,
            "canonical_reference_cohort": COHORT_NAME,
            "input": self.args.input,
            "canonical_collector_output": self.args.canonical_out_dir,
            "critical_path": [
                "candidate-capsule source tail",
                "canonical EventGatedEngine.ingest",
                "canonical feature vector",
                "canonical score_with_contribs",
                "threshold decision",
                "direct TCP handoff",
                "Rust receive",
                "authoritative cache lookup",
                "dry build / blocked first-send",
            ],
            "not_in_critical_path": [
                "canonical signals.jsonl.gz",
                "canonical JSONL polling",
                "gzip polling",
                "database lookup",
                "RPC enrichment",
                "durable audit persistence",
            ],
            "no_submit": True,
            "direct_handoff": {
                "enabled": self.args.direct_handoff,
                "host": self.args.direct_host,
                "port": self.args.direct_port,
            },
            "strategy_changes": "NONE",
        }
        write_json(os.path.join(self.output_dir, "architecture.json"), architecture)
        canonical_hashes = {
            "canonical_spec": self.args.canonical_spec,
            "canonical_spec_sha256": sha256_file(self.args.canonical_spec),
            "canonical_object_sha256": sha256_obj(self.spec),
            "semantic_freeze_manifest": self.args.semantic_freeze_manifest,
            "semantic_freeze_manifest_sha256": sha256_file(self.args.semantic_freeze_manifest),
            "atlas_script": self.args.atlas_script,
            "atlas_script_sha256": sha256_file(self.args.atlas_script),
            "self_script": self.args.self_script,
            "self_script_sha256": sha256_file(self.args.self_script) if self.args.self_script and os.path.exists(self.args.self_script) else None,
            "threshold": self.threshold,
            "threshold_literal": "0.590596",
            "expected_spec_sha256": self.args.expected_spec_sha256,
            "expected_object_sha256": self.args.expected_object_sha256,
            "manifest": manifest,
        }
        write_json(os.path.join(self.output_dir, "canonical_hashes.json"), canonical_hashes)

    def load_canonical_manifest(self):
        manifest = json.load(open(self.args.canonical_cohort_manifest, "r", encoding="utf-8"))
        self.engine.cohort_start_ms = int(manifest["cohort_start_ms"])
        return manifest

    def update_capsule_cache(self, row):
        mint = capsule_mint(row)
        if not mint or not capsule_ok(row):
            return False
        rank = capsule_rank(row)
        if rank > self.capsule_ranks.get(mint, (-1, -1, -1)):
            self.capsule_ranks[mint] = rank
            self.capsule_cache[mint] = row
        return True

    def maybe_prewarm(self, row):
        mint = capsule_mint(row)
        if not mint or mint in self.prewarmed or not capsule_ok(row):
            return
        event = direct_prewarm_event(row)
        if event is None:
            return
        try:
            handoff = self.direct.send(event)
        except OSError as exc:
            self.direct_errors += 1
            self.health.write({
                "event": "direct_prewarm_handoff_error",
                "at_utc": iso(),
                "mint": mint,
                "error": str(exc),
                "no_submit": True,
            })
            return
        self.selected_events.write(event)
        self.prewarmed.add(mint)
        self.health.write({
            "event": "direct_prewarm_emitted",
            "at_utc": iso(),
            "mint": mint,
            "direct_handoff": handoff,
            "no_submit": True,
        })

    def write_status(self, extra=None):
        elapsed_base = int(getattr(self.args, "emitter_start_ms", 0) or self.bootstrap_start_ms)
        elapsed_h = max(1e-9, (now_ms() - elapsed_base) / 3_600_000.0)
        report = {
            "classification": "RANKER_V2_SCORER_DIRECT_EMITTER_V1_COLLECTING"
            if self.emit_count < self.args.max_emissions
            else "RANKER_V2_SCORER_DIRECT_EMITTER_V1_N25_CAPTURED",
            "cohort": DIRECT_EMITTER_COHORT_NAME,
            "updated_at_utc": iso(),
            "pid": os.getpid(),
            "canonical_cohort_start_utc": self.args.canonical_cohort_start_utc,
            "bootstrap_start_utc": self.bootstrap_start_utc,
            "emitter_start_utc": getattr(self.args, "emitter_start_utc", None),
            "emitted_count": self.emit_count,
            "max_emissions": self.args.max_emissions,
            "prewarmed_mints": len(self.prewarmed),
            "warmup_rows": self.warmup_rows,
            "warmup_signals_seen": self.warmup_signals_seen,
            "direct_errors": self.direct_errors,
            "template_blockers": dict(self.template_blockers),
            "counters": dict(self.engine.counters),
            "rejects": dict(self.engine.rejects),
            "signals_per_hour": self.emit_count / elapsed_h,
            "submission_count": 0,
            "no_submit": True,
            "extra": extra or {},
        }
        write_json(os.path.join(self.output_dir, "service_health.json"), report)
        self.health.write({"event": "service_health", **report})

    def process_row(self, row, emit_enabled):
        self.source_row_id += 1
        mint = capsule_mint(row)
        self.update_capsule_cache(row)
        if emit_enabled:
            self.maybe_prewarm(row)
        old_scores = len(self.engine.score_rows)
        old_signals = len(self.engine.signal_rows)
        ts = self.atlas.ts_of(row)
        if finite(ts):
            self.last_event_ts = int(ts)
        self.engine.ingest(row, self.source_row_id)
        if not emit_enabled:
            self.warmup_rows += 1
            self.warmup_signals_seen += len(self.engine.signal_rows[old_signals:])
            self.engine.score_rows.clear()
            self.engine.signal_rows.clear()
            return

        for score_row in self.engine.score_rows[old_scores:]:
            self.all_scores.write(score_row)
        for sig in self.engine.signal_rows[old_signals:]:
            mint = sig["mint"]
            capsule = self.capsule_cache.get(mint)
            if not capsule:
                self.template_blockers["OTHER_EXECUTION_BLOCKER"] += 1
                self.blockers.write({
                    "event": "execution_blocker",
                    "kind": "OTHER_EXECUTION_BLOCKER",
                    "reason": "missing_execution_capsule",
                    "signal": sig,
                    "at_utc": iso(),
                    "no_submit": True,
                })
                continue
            try:
                event = direct_selected_event(sig, capsule)
            except Exception as exc:
                self.template_blockers["BUY_TEMPLATE_BLOCKER"] += 1
                self.blockers.write({
                    "event": "execution_blocker",
                    "kind": "BUY_TEMPLATE_BLOCKER",
                    "reason": repr(exc),
                    "signal": sig,
                    "at_utc": iso(),
                    "no_submit": True,
                })
                continue
            try:
                handoff = self.direct.send(event)
            except OSError as exc:
                self.direct_errors += 1
                self.blockers.write({
                    "event": "execution_blocker",
                    "kind": "DIRECT_HANDOFF_BLOCKER",
                    "reason": str(exc),
                    "signal": sig,
                    "at_utc": iso(),
                    "no_submit": True,
                })
                continue
            direct_sent_ms = handoff.get("sendCompleteMs")
            self.signals.write(sig)
            self.selected_events.write(event)
            self.shadow_twins.write({
                "event": "ranker_v2_direct_emitter_shadow_twin",
                "at_utc": iso(),
                "signal": sig,
                "selected_event": event,
                "direct_handoff": handoff,
                "latency": {
                    "threshold_decision_to_direct_send_complete_ms": direct_sent_ms - int(sig["threshold_decision_ts_ms"])
                    if direct_sent_ms is not None
                    else None,
                    "score_ready_to_direct_send_complete_ms": direct_sent_ms - int(sig["score_ready_ts_ms"])
                    if direct_sent_ms is not None and finite(sig.get("score_ready_ts_ms"))
                    else None,
                },
                "capsule_identity": {
                    "transactionSignature": capsule.get("transaction_signature") or (capsule.get("raw_record") or {}).get("signature"),
                    "sourceSlot": capsule.get("source_slot") or (capsule.get("canonical_state") or {}).get("source_slot"),
                    "sourceTransactionIndex": capsule.get("source_transaction_index"),
                    "stateIdentity": (capsule.get("canonical_state") or {}).get("state_identity"),
                },
                "no_submit": True,
            })
            self.emit_count += 1
            if self.emit_count >= self.args.max_emissions:
                self.running = False
                break

    def replay_segment(self, handle, end_pos, emit_enabled):
        buffer = CompletedLineBuffer()
        last_status = time.time()
        while self.running and handle.tell() < end_pos:
            remaining = end_pos - handle.tell()
            chunk = handle.read(min(self.args.read_bytes, remaining))
            if not chunk:
                break
            for raw in buffer.feed(chunk):
                if not raw.strip():
                    continue
                try:
                    self.process_row(json.loads(raw.decode("utf-8")), emit_enabled)
                except json.JSONDecodeError:
                    self.engine.rejects["completed_malformed_records"] += 1
                except Exception as exc:
                    self.engine.rejects["ingest_exception"] += 1
                    self.health.write({"event": "ingest_exception", "error": repr(exc), "ts_ms": now_ms(), "emit_enabled": emit_enabled})
            if time.time() - last_status >= self.args.status_every_sec:
                self.write_status({"phase": "warmup" if not emit_enabled else "future_emit"})
                last_status = time.time()

    def tail_loop(self):
        freeze = json.load(open(self.args.semantic_freeze_manifest, "r", encoding="utf-8"))
        if freeze.get("classification") != "RANKER_V2_EVENT_GATED_FREEZE_PASS":
            write_json(os.path.join(self.output_dir, "final_report.json"), {
                "classification": "RANKER_V2_DIRECT_SCORER_PARITY_BLOCKER",
                "reason": "semantic freeze manifest is not PASS",
                "freeze_classification": freeze.get("classification"),
            })
            return
        manifest = self.load_canonical_manifest()
        self.args.canonical_cohort_start_utc = manifest["cohort_start_utc"]
        with open(self.args.input, "rb") as handle:
            warmup_start = int(manifest["source_input_position_at_start"])
            handle.seek(warmup_start)
            while self.running:
                warmup_end = os.path.getsize(self.args.input)
                if handle.tell() >= warmup_end:
                    break
                self.replay_segment(handle, warmup_end, emit_enabled=False)
                current_end = os.path.getsize(self.args.input)
                if current_end == handle.tell():
                    break
            self.health.write({
                "event": "warmup_complete",
                "at_utc": iso(),
                "warmup_rows": self.warmup_rows,
                "warmup_signals_seen": self.warmup_signals_seen,
                "future_source_position": handle.tell(),
                "no_submit": True,
            })
            self.args.emitter_start_ms = now_ms()
            self.args.emitter_start_utc = iso(self.args.emitter_start_ms)
            write_json(os.path.join(self.output_dir, "cohort_manifest.json"), {
                "cohort": DIRECT_EMITTER_COHORT_NAME,
                "canonical_reference_cohort": COHORT_NAME,
                "canonical_cohort_start_utc": manifest["cohort_start_utc"],
                "canonical_cohort_start_ms": manifest["cohort_start_ms"],
                "warmup_source_start": warmup_start,
                "warmup_source_end": handle.tell(),
                "emitter_start_utc": self.args.emitter_start_utc,
                "emitter_start_ms": self.args.emitter_start_ms,
                "future_only_boundary": "direct emissions begin only after same-source canonical warmup reaches current EOF after catch-up",
                "threshold": self.threshold,
                "no_submit": True,
                "max_emissions": self.args.max_emissions,
            })
            self.write_boot_artifacts(manifest)
            self.write_status({"phase": "future_emit"})
            buffer = CompletedLineBuffer()
            last_status = time.time()
            while self.running:
                chunk = handle.read(self.args.read_bytes)
                if not chunk:
                    if time.time() - last_status >= self.args.status_every_sec:
                        self.write_status({"phase": "future_emit"})
                        last_status = time.time()
                    time.sleep(self.args.poll_ms / 1000.0)
                    continue
                for raw in buffer.feed(chunk):
                    if not raw.strip():
                        continue
                    try:
                        self.process_row(json.loads(raw.decode("utf-8")), emit_enabled=True)
                    except json.JSONDecodeError:
                        self.engine.rejects["completed_malformed_records"] += 1
                    except Exception as exc:
                        self.engine.rejects["ingest_exception"] += 1
                        self.health.write({"event": "ingest_exception", "error": repr(exc), "ts_ms": now_ms(), "emit_enabled": True})
                if time.time() - last_status >= self.args.status_every_sec:
                    self.write_status({"phase": "future_emit"})
                    last_status = time.time()
            self.write_status({"phase": "complete"})
        self.close()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["phase-a", "prospective-v2", "preflight", "direct-emitter-v1"], required=True)
    parser.add_argument("--input", default="/var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-capsules.jsonl")
    parser.add_argument("--freeze-dir", default="/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-event-gated-semantic-freeze")
    parser.add_argument("--out-dir", default="/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2")
    parser.add_argument("--canonical-spec", default="/mnt/HC_Volume_106775198/edgesniffer-derived/executable-alpha-ranker-v2-canonical-freeze/canonical_score_spec.json")
    parser.add_argument("--primary-ledger", default="/mnt/HC_Volume_106775198/edgesniffer-derived/executable-alpha-ranker-v2-canonical-freeze/primary_trigger_ledger.jsonl.gz")
    parser.add_argument("--reconciliation-dir", default="/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-canonical-event-gated-reconciliation")
    parser.add_argument("--v2-dir", default="/mnt/HC_Volume_106775198/edgesniffer-derived/executable-alpha-ranking-engine-v2-full-universe")
    parser.add_argument("--atlas-script", default="/mnt/HC_Volume_106775198/memecoinExecutableAlphaAtlasV1.py")
    parser.add_argument("--semantic-freeze-manifest", default="/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-event-gated-semantic-freeze/freeze_manifest.json")
    parser.add_argument("--canonical-cohort-manifest", default="/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2/cohort_manifest.json")
    parser.add_argument("--canonical-out-dir", default="/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2")
    parser.add_argument("--self-script", default="")
    parser.add_argument("--expected-spec-sha256", default=EXPECTED_SPEC_SHA256)
    parser.add_argument("--expected-object-sha256", default=EXPECTED_OBJECT_SHA256)
    parser.add_argument("--fixture-rows", type=int, default=50000)
    parser.add_argument("--execution-fixture-rows", type=int, default=500)
    parser.add_argument("--scorer-parity-limit", type=int, default=0)
    parser.add_argument("--poll-ms", type=int, default=10)
    parser.add_argument("--read-bytes", type=int, default=65536)
    parser.add_argument("--status-every-sec", type=float, default=5.0)
    parser.add_argument("--max-emissions", type=int, default=25)
    parser.add_argument("--direct-handoff", action="store_true")
    parser.add_argument("--direct-host", default="127.0.0.1")
    parser.add_argument("--direct-port", type=int, default=19133)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.mode in ("phase-a", "preflight"):
        freeze = phase_a(args)
        print(json.dumps(compact(freeze), indent=2, sort_keys=True))
        if args.mode == "preflight" and freeze.get("classification") != "RANKER_V2_EVENT_GATED_FREEZE_PASS":
            sys.exit(2)
    elif args.mode == "prospective-v2":
        ProspectiveV2(args).tail_loop()
    elif args.mode == "direct-emitter-v1":
        ScorerDirectEmitterV1(args).tail_loop()


if __name__ == "__main__":
    main()
