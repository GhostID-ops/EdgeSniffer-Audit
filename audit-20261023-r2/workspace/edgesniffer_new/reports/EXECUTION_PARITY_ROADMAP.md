# Execution Parity Roadmap

Date: 2026-08-26

| Phase | Goal | Status | Progress | Verification |
|---|---|---:|---:|---|
| A | Read-only parity inventory | Complete | 100% | `reports/EXECUTION_PARITY_BASELINE.md` written |
| A | Clean local strategy/report surface | Complete locally | 100% | Reset workspace now contains Rust parity surface, not strategy farm |
| B | Shared Rust `DecisionInput -> DecisionReceipt` core | Complete initial core | 80% | Core exists and unit tests pass; not wired into provider/replay yet |
| B | Rust candidate-row -> `DecisionInput` producer | Complete initial CLI | 90% | Converts clean candidate JSONL plus known old nested shapes into capsules locally; real upstream source still pending |
| B | No-submit receipt CLI | Complete initial CLI | 80% | Fixture emits deterministic accepted receipt |
| B | Bounded no-submit spool behavior | Complete locally | 100% | CLI can create missing input/output files without processing trades |
| B | Receipt-only report CLI | Complete initial CLI | 85% | Summarizes receipts only locally/VPS; no legacy PnL inputs |
| B | Epoch-scoped no-submit report CLI | Complete initial CLI | 95% | Reports source/input/receipt funnel, reason counts, Mayhem source counts, and diagnostic arms; local/VPS missing-spool smoke passes |
| B | Replay/shadow/live all call same evaluator | Not wired yet | 20% | Core is ready; ingestion wrappers still needed |
| C | Regression tests for historical bugs | Partial | 60% | 15 library tests now cover core parity plus old frozen/pocket row translation; raw replay fixture equality still needed |
| C | Full legacy sidecar build clean | Complete locally | 100% | Restored coherent `executor.rs`/`exit_watcher.rs`; full `cargo test` passes |
| D | Bounded no-submit VPS service | Build/smoke verified, not enabled | 80% | Producer/receipt/report units verify; VPS binaries build; empty-spool and fixture pipeline work; services not started |
| E | Prospective profitability proof | Not started | 0% | Requires no-submit receipts from fresh candidate capsules |

## Next Gate

The next meaningful gate is not another strategy lane. It is:

1. Connect the producer to the real prospective candidate source file.
2. Feed the no-submit parity binary trusted raw replay fixtures.
3. Prove replay and prospective shadow produce identical receipt hashes for identical inputs.
4. Only then rebuild strategy reports from receipts.
5. Replace the old mixed watchdog with the epoch report timer only after the
   source path is connected and receipt files exist.

## Current Blocker

There is still no confirmed live prospective candidate source file feeding
`/var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-source.jsonl`.
Until that source exists, the no-submit receipt path is validated as software but
cannot collect fresh profitability evidence.
