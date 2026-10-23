"""Independent offline probes. No network, credentials, or trading operations.

Run with --source-root and --output; no default workspace paths are used.
These are synthetic counterexamples, not estimates of frequency in production.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--source-root", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
SOURCE = args.source_root.resolve()
sys.path.insert(0, str(SOURCE / "scripts"))
import materializeNativePumpCurveStateV1 as replay
import normalizeNativePumpCurveAccountSeedsV1 as normalizer
import materialize2CQEventDrivenIndependentWindowsV1 as eventwin
import runIndependentStrategyLabV1 as lab
import adapt2CQFrozenScorerNativeV1 as adapter
import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqDecisionSystemDecompositionV1 as decision
import materialize2CQNativeLaunchLineageV1 as launch


def jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def base_seed(ts=900, slot=9):
    return dict(bondingCurve="curve", virtualSolReserves=30_000_000_000,
                virtualTokenReserves=1_073_000_000_000_000,
                realSolReserves=10_000_000_000, realTokenReserves=793_100_000_000_000,
                observedAtMs=ts, slot=slot, source="synthetic-native-account-state",
                write_version=1, account_txn_signature="seed-tx")


def flow(ts=1000, slot=10, index=0, signature="a", side="buy", quote=1_000_000_000, sell=None):
    return dict(mint="mint", side=side, quote_lamports=quote, sell_token_amount_raw=sell,
                wallet="wallet", transaction_signature=signature, observation_id=signature,
                source_slot=slot, source_transaction_index=index, local_receive_ts_ms=ts,
                raw_record=dict(nativeKind="flow", kind="teacherImpulseNativeObservation",
                                bondingCurve="curve", instructionIndex=0, signature=signature))


def run_replay(rows, seeds):
    with tempfile.TemporaryDirectory() as temp:
        d = Path(temp)
        jsonl(d / "input.jsonl", rows)
        if seeds is not None:
            jsonl(d / "seeds.jsonl", seeds)
        args = argparse.Namespace(input=d / "input.jsonl", output=d / "output.jsonl",
                                  state=d / "state.json", account_seed_input=(d / "seeds.jsonl") if seeds is not None else None,
                                  account_seed_delta_input=None, once=True, poll_ms=1,
                                  start_at_end=False, start_at_receive_ms=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            replay.run(args)
        result = [json.loads(x) for x in (d / "output.jsonl").read_text().splitlines() if x]
        return result, json.loads(out.getvalue().strip())


def run():
    results = {}
    create = dict(mint="mint", side="create", source_slot=9, source_transaction_index=0,
                  local_receive_ts_ms=900, creation_proven=True, creation_ts_ms=900,
                  raw_record=dict(kind="teacherImpulseNativeObservation", nativeKind="creation", bondingCurve="curve"))
    rows, stats = run_replay([create, flow()], None)
    assert stats["seeds"] == 0 and stats["skipped_no_seed"] == 1
    results["upstream_create_vocabulary_mismatch"] = stats

    failed = flow()
    failed.update(execution={"success": False, "err": "synthetic-failure"}, failed=True)
    rows, stats = run_replay([failed], [base_seed()])
    assert len(rows) == 1 and rows[0]["native_economics_state_complete"] is True
    results["failed_intent_applied_as_state"] = dict(stats=stats, pre=rows[0]["canonical_state"]["pre_state"],
                                                   post=rows[0]["canonical_state"]["post_state"],
                                                   declared_complete=rows[0]["native_economics_state_complete"])

    rows, stats = run_replay([flow(signature="batch1-a"), flow(ts=1100, signature="batch2-b")], [base_seed()])
    assert len(rows) == 1 and stats["duplicates"] == 1
    results["distinct_signatures_same_slot_local_index_dropped"] = stats

    rows, stats = run_replay([flow(index=2, signature="later-chain-first-arrival"),
                              flow(ts=1100, index=1, signature="earlier-chain-later-arrival")], [base_seed()])
    assert len(rows) == 1 and stats["duplicates"] == 1
    results["out_of_order_chain_transaction_dropped"] = stats

    rows, stats = run_replay([flow(signature="a"), flow(ts=1100, slot=11, signature="b")],
                             [base_seed(), base_seed(ts=1050, slot=9)])
    last = rows[-1]["canonical_state"]["post_state"]["virtual_sol"]
    expected = 30_000_000_000 + 2 * 987_500_000
    assert last == 30_987_500_000 and last != expected
    results["late_older_slot_seed_erases_intervening_trade"] = dict(actual_virtual_sol=last,
                                                                   expected_under_same_replay_math=expected,
                                                                   lost_lamports=expected-last, stats=stats)

    rows, stats = run_replay([flow(ts=86_400_900)], [base_seed()])
    assert rows[0]["native_state_bridge"]["state_age_ms"] == 0
    results["one_day_old_seed_stamped_zero_age"] = dict(actual_seed_age_ms=86_400_000,
                                                       declared_state_age_ms=0, stats=stats)

    rows, stats = run_replay([flow(ts=1000, side="sell", quote=None, sell=10_000_000_000_000, signature="sell"),
                              flow(ts=1100, slot=11, quote=100_000_000, signature="buy1"),
                              flow(ts=1200, slot=12, quote=100_000_000, signature="buy2")], [base_seed()])
    features = eventwin.build_features(rows, rows[-1])
    gross_sold = rows[0]["canonical_state"]["pre_state"]["virtual_sol"] - rows[0]["canonical_state"]["post_state"]["virtual_sol"]
    actual_net = 0.2 - gross_sold / 1e9
    assert features["gross_sell_sol_250ms"] == 0 and actual_net < 0 and eventwin.core_gate(features)
    results["native_sell_sol_flow_lost"] = dict(declared_net_flow_sol=features["net_flow_sol_250ms"],
                                               declared_sell_sol=features["gross_sell_sol_250ms"],
                                               replay_gross_sell_sol=gross_sold/1e9,
                                               signed_buy_gross_minus_sell_gross_sol=actual_net,
                                               core_gate_passes=eventwin.core_gate(features))

    raw = bytearray(81)
    raw[8:16]=(1_073_000_000_000_000).to_bytes(8,"little")
    raw[16:24]=(30_000_000_000).to_bytes(8,"little")
    raw[24:32]=(793_100_000_000_000).to_bytes(8,"little")
    raw[32:40]=(10_000_000_000).to_bytes(8,"little")
    raw[40:48]=(1_000_000_000_000_000).to_bytes(8,"little")
    raw[48]=1
    parsed=normalizer.extract(dict(accountPubkey="curve", data=base64.b64encode(raw).decode(), slot=9, observedAtMs=900))
    assert parsed is not None and parsed["complete"] is True
    rows, stats=run_replay([flow()], [parsed])
    assert len(rows)==1 and rows[0]["native_economics_state_complete"]
    results["completed_raw_curve_accepted_for_trade_replay"] = dict(normalizer_complete=parsed["complete"],
                                                                  replay_output_rows=len(rows), economics_complete=True)

    state=dict(virtual_sol=30_000_000_000,virtual_token=1_073_000_000_000_000,
               real_sol=10_000_000_000,real_token=793_100_000_000_000)
    future=dict(signal_receive_ts_ms=1000,entry_state=state,lineage={"source_receive_clock":True,
                "entry_state_source":"finalized-rpc-future", "entry_state_receive_ts_ms":900_000,
                "entry_delay_ms":1000,"native_state_source":"FINALIZED_PUMP_TRADE_EVENT"})
    assert lab.has_native_receipt_boundary(future)
    results["native_boundary_accepts_future_finalized_state"] = dict(signal_ms=1000, nominal_entry_ms=2000,
                                                                    state_available_ms=900_000, accepted=True)

    # Metadata lacking any decoder evidence or explicit provenance is elevated to exact lineage.
    metadata, reasons=launch.candidate_metadata("mint", {}, {"mint": {"mint":"mint", "creator":"creator",
                    "create_receive_ts_ms":0, "initial_state":state}})
    assert reasons==[]
    results["launch_metadata_without_evidence_or_provenance_accepted"] = dict(reasons=reasons,
        has_signature=bool(metadata.get("create_signature")), has_provenance=bool(metadata.get("provenance_status")))

    # Compare training's post-external-event vector to adapter's pre-current-event vector.
    pre=lab.state(state)
    post=dataclasses.replace(pre, virtual_sol=31_000_000_000, virtual_token=1_040_000_000_000_000,
                             real_sol=11_000_000_000,real_token=760_100_000_000_000)
    current=timing.Event(mint="mint",signature="trigger",slot=10,transaction_index=0,instruction_index=0,
                        observed_ms=1200,evidence_id="trigger",is_buy=True,user="external",creator="creator",
                        quote_lamports=1_000_000_000,token_raw=33_000_000_000_000,pre_state=pre,post_state=post)
    native_features=timing.state_features(current, [], 1000, pre, excluded_wallets=set())
    postcurrent=dataclasses.replace(current,pre_state=post,post_state=post)
    training_features=timing.state_features(postcurrent,[current],1000,pre,excluded_wallets=set())
    keys=["curve_progress","gross_buy_sol_250ms","buy_count_250ms","log_event_index","distance_from_launch_log_price"]
    assert native_features["gross_buy_sol_250ms"]==0 and training_features["gross_buy_sol_250ms"]==1
    results["training_post_event_vs_adapter_pre_event"] = {k:dict(adapter=native_features[k],frozen_training=training_features[k]) for k in keys}

    return results


if __name__ == "__main__":
    results=run()
    target=args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results,indent=2)+"\n")
    print(json.dumps(results,indent=2))
