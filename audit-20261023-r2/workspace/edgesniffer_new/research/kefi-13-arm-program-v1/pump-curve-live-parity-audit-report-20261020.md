# Pump curve live-parity economics audit

Status: read-only audit. The frozen policy, prospective cohort, and collectors were not changed. Trading remains disabled.

## Finding

The current model is strong venue-native causal research, but it is not yet full live-execution parity. The most important gap is that the one-second delay changes the exit target but does not reconstruct the Pump reserve state at the delayed entry. The current model quotes the buy from the wallet anchor's pre-event reserve state.

The model also assumes an immediate sell at the first finalized mark at or after the target. It does not model the additional time between observing that mark and a submitted sell being included.

## What is already modeled correctly

- exact Pump bonding-curve TradeEvent admission and top-level route separation;
- native-SOL quote filtering;
- exact integer curve price impact and reserve arithmetic;
- decoded protocol and creator fee fields;
- explicit 0.5 SOL notional;
- one-second delay represented in the exit target;
- 50 bps per-leg stress assumption;
- fixed network-fee assumption;
- missed/failed quote outcomes;
- contemporaneous same-mint, same-quote non-wallet BUY controls;
- receive-only evidence preservation and immutable hashes.

## Quantified delayed-entry audit

The audit covered 97 native-SOL Pump top-level prospective windows available at the audit point.

- Current anchor-state model: 96 actionable, `+6.6663 SOL`.
- Tape-derived delayed-state proxy: 96 actionable, `+5.4557 SOL`.
- Difference: `-1.2106 SOL` under the delayed-state proxy.
- 20/97 anchors had safe same-second post-anchor state evidence.
- 34/97 had events in the one-second entry target second whose exact receive/order timing cannot be proved from finalized `block_time` alone.

The delayed-state number is not itself live truth. It is a diagnostic lower/alternative replay using available finalized tape state. It demonstrates that the current model can materially overstate economics when the reserve changes during the entry delay.

## Remaining live-parity gaps

1. **Entry state:** native ingestion needs receive-clock and ordered state observations, or a conservative interval model, so the quote is made from the state actually available after the delay.
2. **Exit inclusion:** a live sell is not included at the exact finalized mark. The model needs a measured or bounded exit-observation/submission delay and should report price movement during that interval.
3. **Clock resolution:** finalized `block_time` is one-second granularity; it cannot prove ordering inside a second.
4. **Transaction execution:** receive-only data cannot measure actual inclusion failures, priority fees, RPC latency, stale account state, or submitted-transaction slippage.
5. **Stress calibration:** 50 bps is a robustness assumption in the fee model, not an observed slippage distribution.

## Decision

- Existing sealed and prospective evidence remains valid as decoder/economics research evidence.
- Current PnL must not be described as reproducible live PnL.
- Do not promote the policy or enable dust/live execution from this evaluator.
- Keep overnight receive-only collection running because the raw tape is needed for the repaired parity replay.
- Next required build: a separate parity-v2 evaluator using ordered delayed-entry state, an exit-inclusion delay band, explicit priority/network fee inputs, and a live-parity audit report. Re-run the same 350-actionable-trade comparison under that evaluator without changing the frozen policy.

Audit artifact SHA-256: `17660381d8405950c8956c62278b56e59e1f384a50e13d36afc74ba640437324`.
