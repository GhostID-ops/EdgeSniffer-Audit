# Execution Parity Baseline

Date: 2026-08-26

Scope: in-place EdgeSniffer reset for one strategy path, `teacher-impulse-parity-v1`.

## Objective

Build one reproducible early-token strategy path where the same Rust decision code and the same decision-boundary inputs are used for historical replay, prospective no-submit shadow, dust execution calibration, and eventual live execution.

This baseline does not validate profitability. It defines the new trust boundary: old reports can suggest hypotheses, but only Rust `DecisionInput -> DecisionReceipt` output can validate a strategy.

## Non-Negotiables Preserved

- One named strategy: `teacher-impulse-parity-v1`.
- No new clean-room project.
- No new paid provider.
- No Jupiter fallback.
- No HTTP/API calls in the buy/sell hot path.
- Rust-only transaction construction/execution remains the target.
- Mayhem is fail-closed safety, not alpha.
- Source slot is provenance/grouping only, never timing/order/inventory proof.
- No live trading was started during this reset.

## Current Workspace State

The local reset workspace has been reduced to the Rust parity surface:

- `rust-sidecar/`
- `reports/EXECUTION_PARITY_BASELINE.md`

Old local strategy/report/systemd sprawl is no longer active in this workspace. VPS data/services were not deleted.

## Existing Path Map

| Area | Current Owner | Reset Classification |
|---|---|---|
| ShredStream ingestion | Rust sidecar / older TS wrappers | Preserve as raw earliest-observation source |
| Yellowstone/gRPC | Rust sidecar / older TS wrappers | Preserve as execution/account-state truth |
| RabbitStream | Redundant provider path | Preserve as redundancy, never primary blocker |
| Candidate construction | Historically JS and Rust | Must move to one Rust decision capsule path |
| Source-slot handling | Candidate/provider metadata | Grouping/provenance only |
| Market cap / curve state | Duplicated JS/Rust logic historically | Must be canonicalized inside Rust receipt path |
| Shadow simulation | Historically JS report scripts | Category C until rebuilt from Rust receipts |
| Rust buy construction | Rust executor | Preserve, but call through receipt boundary later |
| Rust sell construction | Rust executor/exit watcher | Preserve, but executable exit state must be explicit |
| Inventory proof | Rust balance/cache/account streams | Preserve, record provenance and timing |
| Fee/tip/slippage | Rust executor plus legacy reports | Must be included in receipts |
| Mayhem classification | Safety veto | Fail closed on unavailable/unknown |
| Replay/reporting | Historically many scripts | Must consume receipts, not reinvent semantics |

## Evidence Classes

| Class | Meaning | Can Validate Profitability? |
|---|---|---|
| A | Raw provider/on-chain records with provenance | Yes, after replay through Rust core |
| B | Deterministically recomputed fields from A | Yes, if produced by shared Rust core |
| C | Legacy shadow/report/summary values | No; hypothesis-only |

## Divergent Logic / Failure Modes

- Strategy gates existed in many JS report/shadow scripts and separate Rust selectors.
- Market-cap checks differed by source, timing, and SOL-price assumptions.
- Shadow exits could occur before live inventory was provable.
- Missing/failed buys were previously easy to confuse with losses or successful attempts.
- Source-slot parity was overtrusted in some reports.
- Mayhem unavailable was not always treated with the same fail-closed semantics.
- One recent launch-shadow bug rejected the new launch-universe source profile before lane gates ran.
- Full legacy sidecar binary was initially out of sync across `main.rs`, `executor.rs`, and `exit_watcher.rs`; it has now been reconciled locally by restoring the coherent August 20 `executor.rs`/`exit_watcher.rs` pair from the reset quarantine and fixing deterministic tests.

## What Was Built

- Rust library module: `rust-sidecar/src/parity_core.rs`
- Rust producer module: `rust-sidecar/src/decision_input_producer.rs`
- Library export: `rust-sidecar/src/lib.rs`
- Candidate capsule producer CLI: `rust-sidecar/src/bin/teacher_impulse_candidate_producer.rs`
- No-submit receipt CLI: `rust-sidecar/src/bin/teacher_impulse_receipt.rs`
- Receipt-only report CLI: `rust-sidecar/src/bin/teacher_impulse_receipt_report.rs`
- Epoch-scoped report CLI: `rust-sidecar/src/bin/teacher_impulse_epoch_report.rs`
- Fixture: `rust-sidecar/fixtures/teacher_impulse/accepted.jsonl`
- Candidate-row fixture: `rust-sidecar/fixtures/teacher_impulse/candidate-row.jsonl`
- Reconciled Rust sidecar modules: `rust-sidecar/src/executor.rs`, `rust-sidecar/src/exit_watcher.rs`
- Test hardening in `rust-sidecar/src/main.rs`: env-dependent ATA proof tests now run under a test-only lock so `PRIVATE_KEY_BASE58` cannot race across parallel tests.
- Proposed service runbook: `reports/TEACHER_IMPULSE_PARITY_NO_SUBMIT_RUNBOOK.md`
- Spool-safe no-submit mode: `teacher_impulse_receipt --create-input-if-missing`
- Proposed producer unit: `systemd/edgesniffer-teacher-impulse-parity-v1-producer.service`
- Proposed epoch report unit/timer:
  `systemd/edgesniffer-teacher-impulse-parity-v1-epoch-report.service`
  and `systemd/edgesniffer-teacher-impulse-parity-v1-epoch-report.timer`

## Core Contract

`DecisionInput` preserves:

- Provider/source.
- Raw record identity/hash.
- Source slot and transaction index.
- Provider sequence.
- Local receive/decode/candidate-ready timestamps.
- Transaction signature.
- Candidate features available at decision time.
- Canonical curve/pool state with provenance.
- Explicit unavailable/unproven fields.
- Mayhem status.
- Execution quote/slippage/fee model.

`DecisionReceipt` preserves:

- Strategy/config/code/input hashes.
- Deterministic event ID.
- Accepted/rejected/unavailable status.
- Exact reason codes.
- Unavailable fields.
- Market cap and units.
- Expected entry.
- Fee model.
- Observed execution placeholders for later submit/landing/inventory/exit.
- Receipt hash.

## Tests Added

Permanent parity-core tests cover:

- Complete accepted teacher impulse input.
- Deterministic receipt hash stability.
- Mayhem unavailable fail-closed.
- Source slot not treated as order/inventory proof.
- Canonical state required for executable entry.
- Canonical market cap overrides candidate market cap.
- Later canonical state cannot leak into decision.
- Missing source transaction/signature/instruction data is unavailable, not accepted.
- Incomplete execution quote is unavailable, not accepted.
- Selected-candidate ATA/balance proof cache tests for wallet-owned balances, owner-slice updates, first nonzero exact balance preservation, submitted-buy signature proof, and exact post-token-balance proof.
- Fee-aware PumpFun sell quote regression aligned to constant-product plus protocol-fee math.
- Candidate-row conversion into `DecisionInput`.
- Missing Mayhem status defaults to unavailable/fail-closed.
- Candidate source rows are hashed when no raw hash is provided.
- Later canonical state is preserved for evaluator rejection.
- Old nested frozen-signal rows map provider/signature/mint/context fields.
- Old `contextSnapshot` pocket-parity rows map canonical candidate identity,
  sequence, timing, source instruction index, and snapshot features.

## Validation Results

Local reset workspace:

- `cargo test --manifest-path .\rust-sidecar\Cargo.toml --lib parity_core`
  - Passed: `9/9`.
- `cargo build --manifest-path .\rust-sidecar\Cargo.toml --bin teacher_impulse_receipt`
  - Passed.
- `cargo build --manifest-path .\rust-sidecar\Cargo.toml --release --bin teacher_impulse_receipt`
  - Passed.
- `cargo run --manifest-path .\rust-sidecar\Cargo.toml --bin teacher_impulse_receipt -- --input .\rust-sidecar\fixtures\teacher_impulse\accepted.jsonl --max-receipts 1`
  - Passed; emitted an accepted deterministic receipt.
- `cargo test --manifest-path .\rust-sidecar\Cargo.toml`
  - Passed: `15` library tests, `86` sidecar binary tests, no-submit CLI tests,
    and doc tests.
- Producer to receipt to report pipeline
  - Passed: candidate row converted to one accepted receipt; receipt-only report showed `1` accepted, `0` rejected, `0` unavailable.
- Epoch report pipeline
  - Passed: source row, `DecisionInput`, and `DecisionReceipt` spool produced
    one scoped report with `1` source row, `1` input, `1` accepted receipt, and
    no legacy PnL dependency.
- Missing-spool epoch report
  - Passed with `--allow-missing`: reports zero counts instead of failing before
    the prospective source is connected.
- Producer to receipt to report pipeline with representative legacy-shaped rows
  - Passed: old frozen-signal shape produced an explicit unavailable receipt
    because required executable evidence was absent; complete pocket-parity
    shape produced an accepted receipt.

VPS:

- Uploaded reset parity surface into `/root/bot/EdgeSniffer/rust-sidecar`; the existing `/root/bot/EdgeSniffer/rust-engine` was not overwritten.
- `cargo fmt --check`
  - Passed.
- `cargo test`
  - Passed after the latest translation/report hardening: `15` library tests,
    `86` sidecar tests, no-submit CLI tests, epoch-report CLI tests, and doc
    tests.
- `cargo build --release --bin teacher_impulse_candidate_producer --bin teacher_impulse_receipt --bin teacher_impulse_receipt_report --bin teacher_impulse_epoch_report`
  - Passed.
- `cargo run --bin teacher_impulse_receipt -- --input fixtures/teacher_impulse/accepted.jsonl --max-receipts 1`
  - Passed; emitted the same deterministic receipt hash as local.
- Producer to receipt to report fixture pipeline
  - Passed: `candidate-row.jsonl` converted to one accepted receipt and report showed `1` accepted, `0` rejected, `0` unavailable.
- `target/release/teacher_impulse_receipt --input /tmp/.../decision-inputs.jsonl --output /tmp/.../decision-receipts.jsonl --follow --start-at-end --poll-ms 1 --max-receipts 0 --create-input-if-missing`
  - Passed; created empty input and output spool files without processing candidates.
- `systemd-analyze verify` for proposed producer, receipt, and epoch-report units
  - Passed.
- No trading or shadow service was started/restarted.
- Epoch report missing-spool mode on `/var/lib/edgesniffer-teacher-impulse-parity-v1/*`
  - Passed with zero counts and no service start.

## Smallest Next Implementation Plan

1. Connect the producer to the real prospective candidate source file.
2. Add fixture replay from trusted raw records into `DecisionInput`.
3. Make prospective no-submit capture write only candidate-centric capsules plus receipts.
4. Update reports to consume receipts only.
5. Only then consider enabling the bounded no-submit VPS service.

## Proposed Bounded No-Submit Service

Name: `edgesniffer-teacher-impulse-parity-v1-no-submit.service`

Inputs:

- Immutable `DecisionInput` JSONL.
- Small bounded source rings for pre-decision context.
- Follow-through evidence only for selected candidates.

Outputs:

- `DecisionReceipt` JSONL.
- Health counters.
- Reject/unavailable summaries.

Expected storage:

- Target capsule size: 3-10 KB.
- At 1,000 candidates/hour: roughly 3-10 MB/hour before compression.
- No firehose archive.

Deployment gate:

- Done: full no-submit binary builds on VPS.
- Done: receipt hash tests pass on VPS.
- Done: empty-spool no-submit mode works on VPS.
- Done: epoch report missing-spool mode works locally and on VPS.
- Pending: replay and prospective no-submit produce identical receipts from identical inputs.
- Done: producer converts candidate rows into candidate-centric `DecisionInput` capsules.
- Pending: producer is connected to the real prospective candidate source.
- Pending: latest old-shape parsing hardening is synced and re-tested on VPS.
- Required always: Mayhem unavailable remains fail-closed.
- Required always: submit/live flags remain disabled.
