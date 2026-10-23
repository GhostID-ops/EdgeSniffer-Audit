#!/usr/bin/env python3
"""Read-only validation gate for the new-wallet trailing-32-byte adapters."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import exactCurveEconomicsV1 as curve
import pumpSwapExactEconomicsV1 as pumpswap
from materializeWalletRouteCensusV1 import curve_events_with_extension, pumpswap_tx_with_extension

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raw = {str(row["signature"]): row for row in (json.loads(line) for line in args.raw.read_text().splitlines() if line.strip())}
    events = [json.loads(line) for line in args.events.read_text().splitlines() if line.strip()]
    checked = passed = sol_economics = non_sol_exact = 0
    failures: list[dict] = []
    for row in events:
        checked += 1
        source = raw.get(str(row.get("signature")))
        try:
            if not source:
                raise ValueError("RAW_SOURCE_SIGNATURE_MISSING")
            tx = source["transaction"]
            if row.get("underlying_venue") == "PUMP_BONDING_CURVE":
                decoded, variant = curve_events_with_extension(tx)
                event = decoded[int(row["event_index"])]
                observations = curve.extract_pump_source_observations(tx, str(row["signature"]), event.mint, int(source.get("block_time") or 0) * 1000, "rpc_block_time")
                candidates = [item for item in observations if item.side.upper() == ("BUY" if event.is_buy else "SELL")]
                if len(candidates) != 1:
                    raise ValueError(f"PUMP_INSTRUCTION_MATCH_COUNT:{len(candidates)}")
                if event.quote_mint in curve.SOL_QUOTE_MINTS:
                    fees = curve.effective_event_fees(curve.Fees(protocol_bps=event.fee_basis_points, creator_bps=event.creator_fee_basis_points + event.cashback_fee_basis_points, lp_bps=0), event)
                    curve.validate_actual_trade_transition(candidates[0], event, fees)
                    sol_economics += 1
                else:
                    if event.quote_amount <= 0 or event.token_amount <= 0:
                        raise ValueError("NON_SOL_EXACT_EVENT_NONPOSITIVE_AMOUNT")
                    non_sol_exact += 1
            else:
                tx_for_decode, _ = pumpswap_tx_with_extension(tx)
                decoded = pumpswap.validate_transaction(tx_for_decode)
                transition = decoded[int(row["event_index"])]
                if transition.get("user") != row.get("user", transition.get("user")):
                    raise ValueError("PUMPSWAP_WALLET_JOIN_MISMATCH")
            passed += 1
        except Exception as error:
            if len(failures) < 100:
                failures.append({"signature": row.get("signature"), "route": row.get("route"), "error": type(error).__name__ + ":" + str(error)})
    result = {
        "schema": "WALLET_ROUTE_EXTENSION_VALIDATION_GATE_V1",
        "read_only": True, "trading_enabled": False, "policy_frozen": False,
        "raw_signatures": len(raw), "events_checked": checked, "events_passed": passed,
        "events_failed": checked - passed, "sol_economics_eligible_events": sol_economics,
        "non_sol_exact_events": non_sol_exact,
        "eligible_for_canonical_census": checked > 0 and checked == passed,
        "eligible_for_sol_economics": sol_economics > 0,
        "adapter_variant": "TRAILING_32_BYTES_EXTENSION", "failures": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))

if __name__ == "__main__":
    main()
