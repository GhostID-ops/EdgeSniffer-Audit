# Pump curve parity-v2 bounded replay

Status: diagnostic research only. The frozen rule, prospective cohort, and receive-only collectors were not changed. Trading remains disabled.

## What happened to the ambiguous rows

Rows with events in the one-second target second are not discarded and are not assigned an invented exact order. The replay keeps them and calculates a bounded PnL interval using:

- the last safe tape-derived state before the target second;
- the post-state after the target-second event(s);
- the first exit mark at the five-second target;
- a one-second-later exit mark as an inclusion-delay stress case.

This fixes the research treatment of the rows, but it cannot recreate the exact historical receive-clock ordering. That requires native ingestion timestamps and ordered live observations that were not stored in the finalized RPC tape.

## Current replay checkpoint

The replay covered 104 native-SOL Pump top-level windows, with 103 actionable under each reported bound at the audit point.

| Replay | PnL SOL | PF | Win rate | Max DD SOL |
|---|---:|---:|---:|---:|
| Existing anchor-state model | 6.963 | 3.27 | 62.1% | 0.438 |
| Safe delayed-state proxy | 5.626 | 2.45 | 53.4% | 0.684 |
| Bounded low case | 3.247 | 1.63 | 40.8% | 1.298 |
| Bounded high case | 6.240 | 2.78 | 53.4% | 0.554 |

Timing classes in this checkpoint were 38 ambiguous target-second windows, 3 with safe same-second state evidence, and 63 with no observed state change in the captured second. The counts differ from the earlier 34/97 audit because the receive-only cohort continued growing.

## Meaning of the difference

The old point estimate is optimistic as a live-parity representation because it assumes the buy happened at the anchor reserve state and the sell was included immediately at the first mark. The bounded replay shows that the current lead remains positive across this small diagnostic checkpoint, but the economic magnitude is materially lower and uncertain. This is not yet the final 350-trade validation and not a live-profit claim.

## What can and cannot be repaired

- **Can repair now:** retain ambiguous rows, calculate entry-state and exit-inclusion bounds, run the equal-350 comparison under parity-v2, and prevent promotion from a single optimistic point estimate.
- **Can improve retrospectively:** recover on-chain transaction ordering for selected slots with ordered block data where available. This still will not recover the bot's receive time or actual submission/inclusion latency.
- **Cannot recover exactly from the existing tape:** the precise receive-clock order inside a finalized second, RPC delay, priority fee, stale account state, failed submitted transaction, or actual sell inclusion.
- **Must capture prospectively:** native ingestion timestamp, slot/block ordering metadata, observed reserve state, submit timestamp, inclusion timestamp, and execution error/fee telemetry if actual execution is ever authorized.

## Decision

Keep the overnight receive-only collection running. Do not change the frozen policy or enable execution. The next valid economics result is the equal-350-actionable comparison run through parity-v2, with ambiguous rows reported as bounds and live-execution gaps explicitly separated.

Bounded replay artifact SHA-256: `719300e200e12b7f74d2aeb669d3cef31513033f9125813cb82427ba17e8297f`.
