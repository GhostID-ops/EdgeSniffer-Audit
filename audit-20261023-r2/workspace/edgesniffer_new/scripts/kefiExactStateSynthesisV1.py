#!/usr/bin/env python3
"""Summarize exact PumpSwap event/state fields from candidate-context output.

Descriptive, outcome-blind research only. This consumes the bounded Rust
extractor output and never reads outcomes, PnL, or live service state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, int((len(values) - 1) * q)))
    return values[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_jsonl", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("output_md", type=Path)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.context_jsonl.read_text().splitlines() if line.strip()]
    assert len({row["ordinal"] for row in rows}) == len(rows), "duplicate ordinals"
    assert all(row["exact_event_decode_failures"] == 0 for row in rows), "exact decode failures"

    events = [row["exact_pump_swap_events"] for row in rows]
    buys = [row["exact_pump_swap_buys"] for row in rows]
    sells = [row["exact_pump_swap_sells"] for row in rows]
    pools = [row["distinct_pools"] for row in rows]
    pre_base = [row["last_exact_pre_pool_base"] for row in rows if row["last_exact_pre_pool_base"] is not None]
    pre_quote = [row["last_exact_pre_pool_quote"] for row in rows if row["last_exact_pre_pool_quote"] is not None]
    base_amount = [row["last_exact_base_amount"] for row in rows if row["last_exact_base_amount"] is not None]
    quote_delta = [row["last_exact_pool_quote_delta"] for row in rows if row["last_exact_pool_quote_delta"] is not None]
    entry = [row["entry_exact_events"] for row in rows]
    entry_base = [row["entry_exact_pre_pool_base"] for row in rows if row["entry_exact_pre_pool_base"] is not None]
    entry_quote = [row["entry_exact_pre_pool_quote"] for row in rows if row["entry_exact_pre_pool_quote"] is not None]

    report = {
        "schema": "KEFI_EXACT_STATE_SYNTHESIS_V1",
        "source_context_jsonl": str(args.context_jsonl),
        "source_context_sha256": sha256(args.context_jsonl),
        "rows": len(rows),
        "ordinal_min": min(row["ordinal"] for row in rows) if rows else None,
        "ordinal_max": max(row["ordinal"] for row in rows) if rows else None,
        "exact_event_decode_failures": sum(row["exact_event_decode_failures"] for row in rows),
        "windows_with_exact_events": sum(value > 0 for value in events),
        "windows_with_exact_buy": sum(value > 0 for value in buys),
        "windows_with_exact_sell": sum(value > 0 for value in sells),
        "windows_with_selected_entry_exact_event": sum(value == 1 for value in entry),
        "windows_with_ambiguous_selected_entry_exact_events": sum(value > 1 for value in entry),
        "windows_without_selected_entry_exact_event": sum(value == 0 for value in entry),
        "exact_event_total": sum(events),
        "exact_buy_total": sum(buys),
        "exact_sell_total": sum(sells),
        "distinct_pool_distribution": {
            "min": min(pools) if pools else None,
            "median": statistics.median(pools) if pools else None,
            "max": max(pools) if pools else None,
        },
        "exact_events_per_window": {
            "min": min(events) if events else None,
            "median": statistics.median(events) if events else None,
            "p90": percentile(events, 0.90),
            "max": max(events) if events else None,
        },
        "exact_pre_pool_base_raw": {
            "windows": len(pre_base),
            "median": statistics.median(pre_base) if pre_base else None,
            "p90": percentile(pre_base, 0.90),
        },
        "exact_pre_pool_quote_raw": {
            "windows": len(pre_quote),
            "median": statistics.median(pre_quote) if pre_quote else None,
            "p90": percentile(pre_quote, 0.90),
        },
        "exact_base_amount_raw": {
            "windows": len(base_amount),
            "median": statistics.median(base_amount) if base_amount else None,
            "p90": percentile(base_amount, 0.90),
        },
        "exact_pool_quote_delta_raw": {
            "windows": len(quote_delta),
            "median": statistics.median(quote_delta) if quote_delta else None,
            "p90": percentile(quote_delta, 0.90),
        },
        "selected_entry_pre_pool_base_raw": {
            "windows": len(entry_base),
            "median": statistics.median(entry_base) if entry_base else None,
            "p90": percentile(entry_base, 0.90),
        },
        "selected_entry_pre_pool_quote_raw": {
            "windows": len(entry_quote),
            "median": statistics.median(entry_quote) if entry_quote else None,
            "p90": percentile(entry_quote, 0.90),
        },
        "scientific_scope": "STRUCTURAL_ENTRY_CONTEXT_ONLY_NO_OUTCOMES_OR_PNL",
    }
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = [
        "# kEFi exact PumpSwap state synthesis v1",
        "",
        f"Rows: {report['rows']} (ordinals {report['ordinal_min']}–{report['ordinal_max']})",
        f"Exact event decode failures: {report['exact_event_decode_failures']}",
        f"Windows with exact events: {report['windows_with_exact_events']}",
        f"Exact events: {report['exact_event_total']} (BUY {report['exact_buy_total']}, SELL {report['exact_sell_total']})",
        f"Selected-entry exact event: {report['windows_with_selected_entry_exact_event']} unique, {report['windows_with_ambiguous_selected_entry_exact_events']} ambiguous, {report['windows_without_selected_entry_exact_event']} missing",
        f"Distinct pools/window: {report['distinct_pool_distribution']}",
        f"Exact events/window: {report['exact_events_per_window']}",
        f"Pre-event pool base raw: {report['exact_pre_pool_base_raw']}",
        f"Pre-event pool quote raw: {report['exact_pre_pool_quote_raw']}",
        f"Base amount raw: {report['exact_base_amount_raw']}",
        f"Pool quote delta raw: {report['exact_pool_quote_delta_raw']}",
        f"Selected-entry pre-pool base raw: {report['selected_entry_pre_pool_base_raw']}",
        f"Selected-entry pre-pool quote raw: {report['selected_entry_pre_pool_quote_raw']}",
        "",
        "Scope: structural entry-context decoding only; no outcomes, PnL, model fitting, or live-service changes.",
    ]
    args.output_md.write_text("\n".join(lines) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
