# Independent execution calibration

## Native account-state seeding for existing curves (2026-10-21)

- Built a receive-only account-state seed path so an already-launched Pump curve can be initialized from native YellowStone reserves; a fresh creation event is no longer required.
- Added `NATIVE_PUMP_CURVE_ACCOUNT_SEED_V1` normalization and Rust `rustNativePumpCurveAccountState` telemetry. The replay only accepts a seed observed no later than the flow receive timestamp and never substitutes RPC state.
- Verification: replay tests `3/3`, seed-normalizer tests `2/2`, and `cargo check --bin pumpfun-sidecar` passed.
- The empirical parity gate remains blocked at zero native rows until the live stream supplies seed records. No collector, cohort, frozen policy, or execution service was changed.
- **Live checkpoint after enabling the separate observer:** owner-wide account observer `active`, `ExecMainStatus=0`, `NRestarts=0`; seed normalizer `active`, `ExecMainStatus=0`, `NRestarts=0`; replay `active`, `ExecMainStatus=0`, `NRestarts=0`. Valid normalized seeds: `285`. Native replay rows: `2`. Native/finalized overlaps: `0/20` required. Current gate remains `BLOCKED_INSUFFICIENT_NATIVE_REPLAY_PARITY`; economics input remains disabled.

Status: `INCOMPLETE — awaiting a fresh compatible legacy Pump candidate`

This lane is separate from teacher-conditioned strategy evaluation. It exists only to verify whether the Rust/Jito executor can submit a bounded buy, prove inclusion, reconcile the resulting position, and submit/prove an exit. It does not promote a policy or alter the frozen research cohorts.

## Attempt v1

- Namespace: `independent-execution-calibration-v1`
- Notional: `0.001 SOL`
- Candidate: `2cJwf8VoVyUzEcayKfqgKoedwiugxWE7V5UJKAeHpump`
- Token program: legacy SPL Token (`Tokenkeg...`)
- Source observation age: approximately `210 seconds` at selection; calibration-only and not a valid live-parity strategy observation
- Buy signature: `mUXGRjdmQs8rmTMu9rRCxvxpPQgtzmV22ZMZu5cyt8c5VtWwix32MofbuJpVf5V9pPhdqDWEJwQqzSH3XLw4fx6`
- Executor result: signature generated and broadcast attempts recorded; `status_timeout`, `slot=null`, `confirmationStatus=null`
- Chain inclusion: not confirmed by the configured RPC checks
- Exact balance proof: failed with `post-buy token balance rpc send failed`; later wallet reconciliation remained zero
- Exit submission: none; correctly withheld because no sellable position was proven
- Interpretation: a real submission attempt, but **not** genuine inclusion telemetry and not a completed calibration trade

The audit is preserved on the VPS at:

`/mnt/HC_Volume_106775198/edgesniffer-derived/independent-execution-calibration-v1/rust-selected-candidate-audit.jsonl`

## Retry lane v2

- Namespace: `independent-execution-calibration-v2`
- Cap: one `0.001 SOL` attempt; Rust-only; Jupiter fallback disabled; no broad strategy lane
- Freshness gate: candidate observation must be no older than `10 seconds`
- Token-2022 candidates remain fail-closed because their canonical sell-template adapter is not certified
- Current state: executor active and emitter waiting; no fresh compatible legacy Pump buy has appeared during the observed window
- v2 audit path: `/mnt/HC_Volume_106775198/edgesniffer-derived/independent-execution-calibration-v2/rust-selected-candidate-audit.jsonl`

## Delay audit update

- The normalized source is advancing and the exact-state bridge is active with `0` RPC errors.
- In the recent five-minute source sample, there were `23` buys and `0` legacy-SPL buys; the observed buys were Token-2022.
- The v2 emitter therefore has no eligible candidate, rather than failing to hand data to the executor.
- Its wait diagnostics now report fresh-buy counts, rejected Token-2022 counts, and the 10-second freshness threshold. The emitter is configured as a normal active service while waiting instead of appearing stuck in `activating` state.

No submitted-exit telemetry is written from an intent, timeout, or paper result. The native timing gate remains unchanged and cannot be marked passed until a buy and a sell are both chain-observed with their signatures/slots.

## Token-2022 route adapter (2026-10-21)

- Built and deployed a separate `token2022-v2` candidate adapter and calibration namespace; the legacy lane remains unchanged.
- Admission is strict: recognized Pump V2 buy discriminator, Token-2022 at the canonical account position, WSOL/USDC quote mint, and the full 27-account source layout required by the Rust sell-template builder.
- “Legacy-SPL” describes the token program, not the market mechanism. A Pump bonding-curve market can use either legacy SPL Token or Token-2022.
- Recent Token-2022 observations are 18-account legacy-shaped rows, not the certified 27-account V2 layout. The adapter has emitted no candidate and submitted no transaction; this is intentional fail-closed behavior.
- Token-2022 transfer-fee extension state is not yet captured and verified in the candidate contract, so this is not promotion-ready economics coverage.
- VPS status: both Token-2022 units are active and the emitter reports `WAITING_FOR_ELIGIBLE_CANDIDATE`. No cohort was reset and no policy changed.
- Transfer-fee/state capture is now complete in the receive-only native bridge: it fetches finalized Token-2022 mint bytes, decodes the TLV extension table and both transfer-fee schedules, records withheld amount and observed Token-2022 balance deltas, and rejects incomplete state.
- Verification: local Python compilation and `testEnrichNativePumpCurveStateV1.py` passed `4/4`; VPS bridge compilation passed; the bridge, refresh service, Legacy-SPL calibration lane, and Token-2022 calibration lane are active with `ExecMainStatus=0` and `NRestarts=0`. This is bridge verification, not live execution testing.
- The separate append-only Token-2022 state stream now contains `84` deduplicated observations; all `84/84` have complete mint TLV parsing and the expected Token-2022 account owner. None currently declares a TransferFeeConfig extension, which is a valid captured “no transfer fee configured” result—not missing state. Current observations still do not satisfy the canonical 27-account Pump V2 execution shape, so no candidate was emitted.
- The Token-2022 candidate emitter now requires `token2022_state.complete == true` in addition to the 27-account/source-instruction checks. Its VPS restart and compile verification passed; it remains waiting with no submission.
- **Canonical trace result:** one real Token-2022 observation passed the 27-account Pump V2 shape, recognized buy discriminator, complete mint-state parse, and no-transfer-fee result. It also joined to the finalized exact Pump curve state. It was not emitted because finalized enrichment completed after the 10-second freshness gate. This identifies the next blocker as native pre-finality state/candidate construction, not an adapter-layout failure.
- The freshness gate was not widened. A delayed candidate would measure a different execution condition and would not be valid native live-parity evidence.
- **Native pre-finality replay built:** added a separate receive-only materializer that seeds Pump curves from native creation observations and applies ordered buys/sells using the same 125-bps Pump fee and constant-product math as the Rust replay path. It emits causal pre/post reserves at the native receive timestamp, without RPC. It starts at the live tail, caps retained curve state, and does not feed execution automatically.
- Verification: focused replay tests passed `2/2`; VPS Python compilation passed; `edgesniffer-independent-native-pump-replay-v1.service` is active with approximately `7 MB` memory and no restart loop. Its current output is `0` rows because it is correctly waiting for a new creation seed after starting at the live tail.
- **Parity gate deployed:** `auditNativePumpReplayParityV1.py` compares native and finalized virtual reserves with a preregistered minimum of 20 overlaps, 99% match rate, and a two-lamport maximum error. Current report: `BLOCKED_INSUFFICIENT_NATIVE_REPLAY_PARITY`, `0` native rows, `0` overlaps, `economicsInputAllowed=false`. A separate historical backfill was stopped after it produced no rows while consuming excessive memory; it did not alter the live-tail service or source evidence.
- **Source-separation finding:** the dedicated 2CQ wallet receipt collector is active and has observed `5` wallet transactions, including `2` fee-payer transactions. The native replay consumes the market-wide ShredStream flow file, not that wallet receipt file. Its recent tail contained `1,163` buys but `0` creation events, so the replay is waiting for a market creation seed—not waiting for 2CQ to trade.
