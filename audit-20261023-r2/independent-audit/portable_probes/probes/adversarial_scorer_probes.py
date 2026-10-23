"""Offline, synthetic probes of the supplied EdgeSniffer source; no network or trading.

Usage: python adversarial_scorer_probes.py source_root --output output.json
Results demonstrate implementation behavior, not market PnL or its magnitude.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.source_root.resolve()
    sys.path.insert(0, str(root / "scripts"))
    import adapt2CQFrozenScorerNativeV1 as adapter
    import buildIndependentMarketControlsV1 as controls
    import materialize2CQFrozenScorerWindowsV1 as bridge
    import twoCqCausalEntryTimingFingerprintV1 as timing
    import twoCqDecisionSystemDecompositionV1 as decision
    from exactCurveEconomicsV1 import CurveState

    scorer = json.loads((root / "research/2cq-event-driven-fingerprint-freeze-v1/frozen-scorer.json").read_text())
    model = scorer["timingModel"]
    features = dict(model["means"])
    name = max(model["coefficients"], key=lambda k: abs(model["coefficients"][k]))
    features[name] -= model["intercept"] * model["scales"][name] / model["coefficients"][name]
    raw = adapter.scorer_value(features, scorer)
    probability = timing.sigmoid(raw)
    threshold = scorer["timingThreshold"]
    results = {
        "score_units": {
            "synthetic_vector_purpose": "Actual frozen model mean vector adjusted to zero linear predictor; scoring-unit parity only.",
            "adapter_linear_predictor": raw,
            "frozen_probability_score": probability,
            "frozen_threshold": threshold,
            "adapter_accepts": raw >= threshold,
            "frozen_model_accepts": probability >= threshold,
            "correct_logit_cutoff": math.log(threshold / (1 - threshold)),
            "accidental_probability_cutoff": timing.sigmoid(threshold),
        }
    }

    tied_episode = {"controlN": 9, "states": [{"label": False, "features": {"x": 0.0}} for _ in range(9)] + [{"label": True, "features": {"x": 0.0}}]}
    results["ranking_ties"] = timing.ranking_metrics([tied_episode], ("x",), [0.0], [1.0], (0.0, [0.0]))
    results["average_precision_ties"] = {
        "positive_n": 1,
        "negative_n": 9,
        "scores": "all 0.5",
        "reported_ap": timing.average_precision([0] * 9 + [1], [0.5] * 10),
        "threshold_grouped_ap": 0.1,
        "reported_auc": timing.auc([0] * 9 + [1], [0.5] * 10),
    }

    train_example = {"eligibleOrdinal": 1, "mint": "A", "states": [{"mint": "A", "label": True}, {"mint": "C", "label": False}]}
    assess_example = {"eligibleOrdinal": 246, "mint": "B", "states": [{"mint": "B", "label": True}, {"mint": "A", "label": False}, {"mint": "C", "label": False}]}
    train, assessment, dropped = decision.internal_split([train_example, assess_example])
    results["cross_mint_split"] = {
        "train_teacher_mints": [row["mint"] for row in train],
        "assessment_teacher_mints": [row["mint"] for row in assessment],
        "candidate_mint_intersection": sorted({s["mint"] for row in train for s in row["states"]} & {s["mint"] for row in assessment for s in row["states"]}),
        "dropped": dropped,
    }

    s0 = CurveState(30_000_000_000, 1_073_000_000_000_000, 0, 793_100_000_000_000)
    s1 = CurveState(31_000_000_000, 1_038_000_000_000_000, 1_000_000_000, 758_100_000_000_000)
    def causal_event(signature: str, received_ms: int, index: int, user: str):
        return decision.CausalEvent(mint="M", signature=signature, slot=1, transaction_index=index,
                                    instruction_index=0, received_ns=received_ms*1_000_000, is_buy=True,
                                    user=user, creator="C", quote_lamports=1_000_000_000, token_raw=1,
                                    pre_state=s0, post_state=s1)
    external = [causal_event("e0", 1000, 0, "w0"), causal_event("e1", 1100, 1, "w1")]
    teacher = causal_event("teacher", 2000, 2, decision.WALLET)
    temporal, tape = decision.build_temporal_examples(
        [{"eligibleOrdinal": 1, "mint": "M", "signature": "teacher", "route": "PUMP_CURVE", "acquisitionClass": "synthetic"}],
        {"M": external + [teacher]}, {"M": 1500*1_000_000}, {"teacher": teacher})
    chosen = next(row for row in temporal[0]["states"] if row["label"])
    results["create_after_selected_state"] = {
        "selected_receive_ms": chosen["receivedNs"]//1_000_000,
        "create_receive_ms": 1500,
        "teacher_receive_ms": 2000,
        "raw_selected_age_ms": chosen["receivedNs"]//1_000_000 - 1500,
        "reported_log_age_ms": chosen["features"]["log_age_ms"],
        "rejected": False,
        "note": "Synthetic receipt reorder: CREATE was known before teacher but unavailable at selected state.",
    }
    event_clock = decision.state_features(s1, external[-1].received_ns, "M", "C", external, 500*1_000_000)
    tick_clock = decision.state_features(s1, 5000*1_000_000, "M", "C", external, 500*1_000_000)
    results["stale_autonomous_ranking_clock"] = {
        "last_event_ms": 1100, "common_tick_ms": 5000,
        "features_using_last_event_clock_as_emit_does": {name: event_clock[name] for name in ("log_age_ms", "log_ms_since_last_external", "gross_buy_sol_250ms")},
        "features_using_common_decision_clock": {name: tick_clock[name] for name in ("log_age_ms", "log_ms_since_last_external", "gross_buy_sol_250ms")},
    }

    with tempfile.TemporaryDirectory(prefix="edgesniffer_scorer_probe_") as directory:
        work = Path(directory)
        state = {"virtual_sol": 30_000_000_000, "virtual_token": 1_073_000_000_000_000, "real_sol": 0, "real_token": 793_100_000_000_000}
        rows = [{
            "mint": "M", "side": "buy", "local_receive_ts_ms": ts,
            "transaction_signature": f"s{i}", "observation_id": f"o{i}",
            "wallet": f"w{i}", "source_slot": 1, "source_transaction_index": i,
            "canonical_state": {"pre_state": state, "post_state": state},
            "route": "PUMP_CURVE", "quote_lamports": 100_000_000,
            "fee_basis_points": 125, "protocol_fee_basis_points": 95, "creator_fee_basis_points": 30,
        } for i, ts in enumerate((1000, 1100, 2200, 7000))]
        source = work / "source.jsonl"
        source.write_text("".join(json.dumps(row) + "\n" for row in rows))
        enrichment = work / "enrichment.jsonl"
        enrichment.write_text(json.dumps({"mint": "M", "create_receive_ts_ms": 500, "creator": "creator", "initial_state": state}) + "\n")
        toy_model = {"featureNames": ["log_age_ms"], "means": {"log_age_ms": 0}, "scales": {"log_age_ms": 1}, "coefficients": {"log_age_ms": 0}, "intercept": 1}
        toy_scorer = work / "scorer.json"
        toy_scorer.write_text(json.dumps({"timingModel": toy_model, "timingThreshold": .5}))
        ns = argparse.Namespace(input=source, scorer=toy_scorer, enrichment=enrichment, create_registry=None,
                                signals_output=work/"signals.jsonl", controls_output=work/"controls.jsonl", report=work/"report.json")
        summary = adapter.materialize(ns)
        signal_rows = [json.loads(line) for line in ns.signals_output.read_text().splitlines()]
        control_rows = [json.loads(line) for line in ns.controls_output.read_text().splitlines()]
        signal_window, _ = bridge.build_window(signal_rows[0], rows, 7000)
        control_window, _ = bridge.build_window(control_rows[0], rows, 7000)
        results["high_score_overlapping_controls"] = {
            "toy_scorer_purpose": "Constant above-threshold score isolates crossing/control routing.",
            "signals": summary["exact_candidate_signals"],
            "controls": summary["same_period_control_events"],
            "above_threshold_control_n": sum(row["score"] >= row["threshold"] for row in control_rows),
            "signal_mint": signal_rows[0]["mint"], "control_mint": control_rows[0]["mint"],
            "signal_time": signal_rows[0]["signal_receive_ts_ms"], "control_time": control_rows[0]["signal_receive_ts_ms"],
            "signal_mark_signatures": [row["transaction_signature"] for row in signal_window["marks"]],
            "control_mark_signatures": [row["transaction_signature"] for row in control_window["marks"]],
        }

        baseline_rows = [{"record_type": "INDEPENDENT_MARKET_WINDOW", "route": "PUMP_CURVE", "mint": mint,
                          "signal_receive_ts_ms": ts, "signal_signature": sig}
                         for mint, ts, sig in [("A", 1000, "a"), ("B", 2000, "b"), ("C", 3000, "c"), ("B", 4000, "b2")]]
        baseline_source = work/"baseline.jsonl"
        baseline_source.write_text("".join(json.dumps(row) + "\n" for row in baseline_rows))
        controls.build(baseline_source, work/"baseline_controls.jsonl", work/"baseline_report.json")
        emitted = [json.loads(line) for line in (work/"baseline_controls.jsonl").read_text().splitlines()]
        results["legacy_control_overlap"] = {
            "control_n": len(emitted), "control_mints": [row["mint"] for row in emitted],
            "control_signatures": [row["signal_signature"] for row in emitted],
            "candidate_control_signature_intersection": sorted({row["signal_signature"] for row in baseline_rows} & {row["signal_signature"] for row in emitted}),
            "input_candidates_retained": len([json.loads(line) for line in baseline_source.read_text().splitlines()]),
        }

    report = json.loads((root / "research/2cq-event-driven-fingerprint-freeze-v1/report.json").read_text())
    event_rows = report["eventDrivenReplay"]["rows"]
    results["preserved_report_recalculation"] = {
        "assessment_episodes": len(event_rows), "unique_assessment_mints": len({row["mint"] for row in event_rows}),
        "selected_state_pass_n": sum(row["selectedPass"] for row in event_rows),
        "any_crossing_n": sum(row["firstCrossingToTeacherMs"] is not None for row in event_rows),
        "first_crossing_is_selected_n": sum(row["firstCrossingIsSelectedState"] for row in event_rows),
        "selected_state_lead_at_least_1000ms_n": sum(row["selectedStateToTeacherMs"] >= 1000 for row in event_rows),
        "first_crossing_lead_at_least_1000ms_n": sum(row["firstCrossingToTeacherMs"] is not None and row["firstCrossingToTeacherMs"] >= 1000 for row in event_rows),
        "above_threshold_state_n": sum(row["crossingStateN"] for row in event_rows),
        "maximum_above_threshold_states_per_episode": max(row["crossingStateN"] for row in event_rows),
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
