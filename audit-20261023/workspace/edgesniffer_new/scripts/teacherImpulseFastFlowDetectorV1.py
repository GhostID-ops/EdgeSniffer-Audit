#!/usr/bin/env python3
import argparse
import json
import math
import os
import time
from collections import defaultdict, deque


RULES = [
    "grossbuy_1s_ge5_fastdetector_v1",
    "grossbuy_2s_ge5_fastdetector_v1",
    "bc50_ge2_grossbuy5s_ge5_fastdetector_v1",
]


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def now_ms():
    return int(time.time() * 1000)


def read_tail(path, start_at_end, poll_ms):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        if start_at_end:
            handle.seek(0, os.SEEK_END)
        while True:
            pos = handle.tell()
            line = handle.readline()
            if not line:
                handle.seek(pos)
                time.sleep(poll_ms / 1000.0)
                continue
            yield line


def ts_of(row):
    return (
        row.get("local_receive_ts_ms")
        or row.get("decode_complete_ts_ms")
        or row.get("candidate_ready_ts_ms")
        or (row.get("canonical_state") or {}).get("observed_at_ms")
    )


def mint_of(row):
    return row.get("mint") or (row.get("raw_record") or {}).get("mint") or (row.get("candidate") or {}).get("mint")


def quote_sol(row):
    value = row.get("quote_sol")
    if finite(value):
        return float(value)
    value = row.get("quote_lamports")
    if finite(value):
        return float(value) / 1_000_000_000
    rr = row.get("raw_record") or {}
    value = rr.get("quoteLamports")
    if finite(value):
        return float(value) / 1_000_000_000
    return 0.0


def mc_of(row):
    state = row.get("canonical_state") or {}
    candidate = row.get("candidate") or {}
    value = state.get("market_cap_usd")
    if finite(value):
        return float(value)
    value = candidate.get("market_cap_usd")
    if finite(value):
        return float(value)
    return None


def authority_snapshot(row, qualified_at):
    state = row.get("canonical_state") or {}
    source = state.get("source") or row.get("source")
    observed = state.get("observed_at_ms")
    slot = state.get("source_slot") or row.get("source_slot")
    return {
        "source": source,
        "slot": slot,
        "observedAtMs": observed,
        "ageMs": qualified_at - observed if finite(qualified_at) and finite(observed) else None,
        "marketCapUsd": mc_of(row),
        "curveComplete": state.get("curve_complete"),
        "unavailableReason": state.get("unavailable_reason"),
        "available": bool(state) and state.get("unavailable_reason") is None and state.get("curve_complete") is not True,
    }


def side_of(row):
    return row.get("side") or (row.get("raw_record") or {}).get("side")


def wallet_of(row):
    return row.get("wallet") or (row.get("raw_record") or {}).get("wallet")


class Writer:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.handle = open(path, "a", encoding="utf-8")

    def write(self, row):
        self.handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        self.handle.flush()


class FastDetector:
    def __init__(self, args):
        self.args = args
        self.flows = defaultdict(lambda: defaultdict(deque))
        self.sums = defaultdict(lambda: defaultdict(float))
        self.buy_counts = defaultdict(lambda: defaultdict(int))
        self.seen_mints = defaultdict(set)
        self.qualified = defaultdict(int)
        self.seen = 0
        self.qual_writers = {
            rule: Writer(os.path.join(args.base_out_dir, rule, "qualifications.jsonl"))
            for rule in RULES
        }
        self.status = Writer(os.path.join(args.base_out_dir, "status.jsonl"))

    def add_event(self, mint, ts, amount, side):
        for win in [50, 1000, 2000, 5000]:
            dq = self.flows[mint][win]
            dq.append((ts, amount, side))
            self.sums[mint][win] += amount
            if side == "buy":
                self.buy_counts[mint][win] += 1
            cutoff = ts - win
            while dq and dq[0][0] < cutoff:
                old_ts, old_amount, old_side = dq.popleft()
                self.sums[mint][win] -= old_amount
                if old_side == "buy":
                    self.buy_counts[mint][win] -= 1

    def snapshot(self, mint):
        return {
            "grossBuySol1s": self.sums[mint][1000],
            "grossBuySol2s": self.sums[mint][2000],
            "grossBuySol5s": self.sums[mint][5000],
            "buyCount50ms": self.buy_counts[mint][50],
        }

    def qualify(self, rule, mint, row, ts, feature_ready_at, observed_at, snap):
        if mint in self.seen_mints[rule]:
            return
        self.seen_mints[rule].add(mint)
        qualified_at = now_ms()
        # No-submit detector: assume authority is already warm when the direct
        # flow event includes Pump route/template fields. It records the timing
        # target; it does not buy, reserve, or call the sender.
        has_route_fields = bool((row.get("extra") or {}).get("sourceIxAccounts") or row.get("source_ix_data_base64"))
        authority_ready_at = qualified_at if has_route_fields else feature_ready_at
        would_send_at = max(qualified_at, authority_ready_at)
        authority = authority_snapshot(row, qualified_at)
        execution = row.get("execution") or {}
        out = {
            "event": "fast_flow_detector_qualification",
            "rule": rule,
            "mint": mint,
            "ruleTruthAtMs": ts,
            "sourceFlowObservedAtMs": observed_at,
            "flowEventObservedAtMs": observed_at,
            "flowFeatureReadyAtMs": feature_ready_at,
            "fastDetectorQualifiedAtMs": qualified_at,
            "prospectiveQualificationAtMs": qualified_at,
            "candidateSelectedAtMs": qualified_at,
            "authoritativeStateReadyAtMs": authority_ready_at,
            "fastDetectorWouldSendAtMs": would_send_at,
            "shadowWouldFirstSendAtMs": would_send_at,
            "latencyComponentsMs": {
                "ruleTruthToDetectorQualification": qualified_at - ts,
                "ruleTruthToFeatureReady": feature_ready_at - ts,
                "featureReadyToQualification": qualified_at - feature_ready_at,
                "qualificationToSelected": 0,
                "selectedToAuthoritativeReady": max(0, authority_ready_at - qualified_at),
                "authoritativeReadyToWouldSend": would_send_at - max(qualified_at, authority_ready_at),
                "ruleTruthToWouldSendTotal": would_send_at - ts,
            },
            "windows": snap,
            "mcUsd": authority["marketCapUsd"],
            "curveState": authority,
            "authoritySource": authority["source"],
            "authoritySlot": authority["slot"],
            "authorityAgeMs": authority["ageMs"],
            "authorityAvailable": authority["available"],
            "execution": {
                "positionSizeLamports": execution.get("position_size_lamports"),
                "expectedTokensOutRaw": execution.get("expected_tokens_out_raw"),
                "minTokensOutRaw": execution.get("min_tokens_out_raw"),
                "slippageBps": execution.get("slippage_bps"),
            },
            "productionEquivalentNoSubmit": {
                "candidateReservationSimulated": True,
                "fillFirstSimulated": True,
                "submittedMinTokensOutRawWouldBe": 1,
                "signSerializeTimingSimulated": True,
                "actualNetworkSend": False,
                "actualSubmit": False,
            },
            "source": row.get("source"),
            "sourceSlot": row.get("source_slot"),
            "sourceTransactionIndex": row.get("source_transaction_index"),
            "transactionSignature": row.get("transaction_signature"),
            "tokenProgram": row.get("token_program"),
            "wallet": wallet_of(row),
            "noSubmit": True,
            "routeFieldsPresent": has_route_fields,
            "detector": "teacherImpulseFastFlowDetectorV1",
        }
        self.qual_writers[rule].write(out)
        self.qualified[rule] += 1

    def ingest(self, row):
        self.seen += 1
        mint = mint_of(row)
        ts = ts_of(row)
        side = side_of(row)
        if not mint or not finite(ts) or side not in ("buy", "sell"):
            return
        ts = int(ts)
        amount = quote_sol(row) if side == "buy" else 0.0
        observed_at = row.get("local_receive_ts_ms") or row.get("decode_complete_ts_ms") or ts
        feature_ready_at = now_ms()
        self.add_event(mint, ts, amount, side)
        snap = self.snapshot(mint)
        if snap["grossBuySol1s"] >= 5.0:
            self.qualify("grossbuy_1s_ge5_fastdetector_v1", mint, row, ts, feature_ready_at, observed_at, snap)
        if snap["grossBuySol2s"] >= 5.0:
            self.qualify("grossbuy_2s_ge5_fastdetector_v1", mint, row, ts, feature_ready_at, observed_at, snap)
        if snap["buyCount50ms"] >= 2 and snap["grossBuySol5s"] >= 5.0:
            self.qualify("bc50_ge2_grossbuy5s_ge5_fastdetector_v1", mint, row, ts, feature_ready_at, observed_at, snap)
        if self.seen % self.args.status_every == 0:
            self.status.write({
                "event": "fast_flow_detector_status",
                "seen": self.seen,
                "qualified": dict(self.qualified),
                "tsMs": now_ms(),
                "noSubmit": True,
            })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--base-out-dir", required=True)
    parser.add_argument("--start-at-end", action="store_true")
    parser.add_argument("--poll-ms", type=int, default=1)
    parser.add_argument("--status-every", type=int, default=10000)
    args = parser.parse_args()
    detector = FastDetector(args)
    for line in read_tail(args.input, args.start_at_end, args.poll_ms):
        if not line.strip():
            continue
        try:
            detector.ingest(json.loads(line))
        except Exception:
            continue


if __name__ == "__main__":
    main()
