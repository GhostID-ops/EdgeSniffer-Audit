# 8YCB Production Copytrade Translation V1

Cutoff: 2026-09-04 00:46:18 UTC. Scope: no-submit engineering only. No transaction was broadcast, no wallet was funded, and V3 was neither modified nor restarted.

## Result

- Signal parity: **PASS** — 12/12 required positive fixtures, 0 false negatives, 0 false positives, 0 duplicate intents, exact mint/signature/target agreement.
- Buy `V3 signal ready -> signed transaction ready`: **P50 0.452 ms, P75 0.507 ms, P90 0.641 ms, P95 0.949 ms, max 3.209 ms** across 240 fixture builds.
- Sell `exit decision -> signed transaction ready`: **P50 0.128 ms, P75 0.139 ms, P90 0.153 ms, P95 0.180 ms, max 2.187 ms** across 720 fixture probes.
- Buy readiness at or below 25 ms: **realistic for local construction/signing**. Fixture hit rate was 240/240 at <=10 ms, <=25 ms, <=50 ms, and <=100 ms. This is not a landing claim, and there are not yet any latency-eligible future real-time V3 signals.
- Exact local bottlenecks: append-only V3 ledger boundary, 2 ms reader poll, JSON validation/pipe handoff, and approximately 0.30–0.35 ms transaction build/sign work. The dominant unresolved bottleneck is downstream landing/ordering, which no-submit work cannot measure.
- Broadcast safety: **PASS** — zero reachable broadcaster symbols, zero `sendTransaction`/`sendBundle` strings, an ephemeral unfunded signer, no sender endpoints, and submission count 0.
- V3 at cutoff: active, PID 621336, NRestarts 0, cohort start `2026-09-03T21:20:47.141763Z`, N=5, completed=5, submissions=0.

All timing distributions in the first four bullets are deterministic local fixture measurements. The twelve fixtures are parity/engineering tests and are not prospective economics.

## 1. Real execution-path map

Deployed production source audited: `/root/bot/FinalBot-cya/rust-sidecar`. Existing deployed sidecar binary SHA-256: `6fa8640a47324dd4b8d9416f19c345f1d9b525f6f38fa3d89d665f00e549f2b3`. Source hashes at audit were:

- `src/main.rs`: `3bdd2d496d824e8ce573d04a6bff7f1a63541d9e930462a4199807e99a6b1c99`
- `src/executor.rs`: `7850ef1c9ab9c013f0bd68f0219475ea91e36f4d5fcc10313a93bf3f5c1cec23`
- `src/exit_watcher.rs`: `e85a936c31c2768cd320c8f672b6ee7d7a6bced4d322f4187a0d087b9ebf9316`

The existing production-equivalent no-submit service is `edgesniffer-teacher-impulse-production-rule-a-v1.service` (PID 562930, NRestarts 0 at inspection). It runs the deployed `pumpfun-sidecar`, accepts selected candidates through the selected-candidate listener at `127.0.0.1:19101`, and has submission disabled. It was inspected, not changed.

### Existing production buy path

1. `src/main.rs:5043 run_selected_candidate_sniper` owns selected-candidate inputs and queues.
2. `src/main.rs:5393 handle_selected_candidate_line` decodes the candidate.
3. `src/main.rs:5459 submit_selected_candidate_buy` applies the existing execution guard and creates `ExecutePumpfunBuyCommand`; `rustSelectedCandidateBuyStart` is emitted around line 6498.
4. The current production no-submit branch calls `src/executor.rs:1439 dry_build_pumpfun_buy_command` at `src/main.rs:6577`.
5. The live branch would call `src/executor.rs:1918 execute_pumpfun_buy_command` at `src/main.rs:6662`.
6. Both branches use `src/executor.rs:2202 build_pumpfun_buy_tx`, `:2541 build_pumpfun_buy_ix`, and the canonical derived-V2 instruction at `:3240 build_derived_v2_buy_ix`.
7. Signing and serialization occur in `src/executor.rs:2522 compile_and_sign`. Completion of that function is the transaction-ready/signing-ready boundary.
8. Only the live execute branch continues to `src/executor.rs:3424 broadcast_signed_tx`, then `:3552 broadcast_candidates`, `:3613 race_broadcast`, and `:3782 send_candidate`. This is the submission boundary.
9. `src/main.rs:3765 run_hot_blockhash_refresher` maintains the production hot blockhash. The isolated probe uses the same `src/executor.rs:1932 fetch_latest_blockhash` call against a read-only public RPC and never calls a sender.

### Existing production inventory and sell path

1. Token-2022 ATA discovery is `src/main.rs:1367 selected_candidate_ata_for_mint`.
2. `SelectedCandidateBalanceCache` watches the derived ATA through the local Yellowstone/token-account balance stream. The exact-balance path waits for an exact cached balance; optional fallback `getTokenAccountBalance` begins around `src/main.rs:4100`.
3. `src/main.rs:4231 run_selected_candidate_early_balance_activator` refuses to arm the exit from a non-exact inferred balance. An exact nonzero balance updates/activates inventory.
4. `src/main.rs:9163 register_selected_candidate_exit_watch` creates the watcher. `src/exit_watcher.rs:1571 register`, `:2140 on_curve_state`, and `:1818 ack` implement state transitions.
5. Exit actions cross the in-process Tokio `UnboundedSender<ExitAction>` queue into `src/main.rs:9929 run_exit_action_loop`.
6. `src/exit_watcher.rs:1850 guard_exit_action_before_submit` rejects stale/terminal duplicate actions.
7. `ExecutePumpfunSellCommand` is created around `src/main.rs:10435`; construction is `src/executor.rs:2375 build_pumpfun_sell_tx` and `:2758 build_pumpfun_sell_ix`; signing is again `:2522 compile_and_sign`.
8. The actual live call at `src/main.rs:10475` goes through `src/executor.rs:1925 execute_pumpfun_sell_command` to the same broadcast boundary. The isolated probe calls only `src/executor.rs:1519 dry_build_pumpfun_sell_command`.

### New isolated V3 translation path

`edgesniffer-8ycb-prospective-shadow-v3.service` -> V3 `shadow_observer.py:705 atomic_matches` -> `:726 register_signal` -> append-only `cohort_ledger.jsonl` -> `production_shadow.py` 2 ms read-only tail -> immutable adapter -> anonymous stdin pipe -> `eight_ycb_no_submit_builder` -> real deployed Rust build/sign functions -> stdout audit record -> new translation artifacts.

The only network boundary reachable by the isolated Rust process is HTTPS `getLatestBlockhash`. A runtime socket sample showed its child connected to `74.63.229.125:443`; the binary contains `getLatestBlockhash` and contains neither `sendTransaction` nor `sendBundle`.

## 2. Frozen signal interface

Schema: `8ycb-production-signal-v1` in `frozen_signal_interface.schema.json`.

The adapter accepts only a row already admitted by the canonical detector with:

- source schema `8ycb-prospective-signal-v3`
- decision `CREATE_AND_SAME_TRANSACTION_8YCB_ATOMIC_BUY_PROVEN`
- frozen hash `6f42c24221a50341c8c5626ae3a987b564065ba0c9de9a1e3d948fa5ea432817`
- exact target wallet `8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ`

It maps mint, CREATE signature, slot, provider-local transaction index when available, target, atomic target-buy lamports, optional target tokens-out, CREATE line-receive nanoseconds, final required input receive nanoseconds, signal-ready nanoseconds, and canonical event ID. An interface SHA-256 seals the mapped record. It does not re-run CREATE/buy matching, add wallet/metadata filters, or reinterpret outcomes.

`atomicTargetTokensOutRaw` is intentionally nullable because it is not part of the frozen qualifying rule. Its absence cannot cause a production false negative. Rust uses it only for a transaction-construction estimate; if absent it derives a conservative curve-state estimate from the observed target-buy lamports. The actual fill remains unknowable without submission.

The transaction index is the provider-local V3 index, not a later finalized block-order index. That preserves the emitted V3 interface exactly.

## 3. Fixture parity

Required fixture coverage:

- all four V3 signals present at the task cutoff
- all six V2 diagnostic signals
- EP1B handoff-gap launch
- tahKR4 historical regression launch

Result: 12 unique intents from 12 fixtures, zero FN, zero FP, zero identity mismatch, zero duplicate intents. Presenting every positive twice produced 12 suppressed duplicates and still exactly 12 intents. Four negative controls (wrong target, wrong frozen hash, nonqualifying decision, generic control) all failed closed.

All 12 fixtures built a real direct Pump.fun V2 buy and three real sell transactions with the deployed executor construction/signing code. Repeating each 20 times produced 240 buys and 720 sells (960 signed, zero submitted). Fixture times were rebased solely for build-latency measurement; no fixture was added to V3 or prospective economics.

## 4. Future no-submit production shadow

New service: `edgesniffer-8ycb-production-translation-v1.service`.

- separate installation and output directory
- dedicated unprivileged user `edgeshadow8ycbtx`, read group `edgeshadow8ycb`
- read-only V3 path and write access only to the new `prospective/` directory
- `RUST_ONLY_BUYS=true`, `RUST_ONLY_SELLS=true`, `JUPITER_FALLBACK_ENABLED=false`
- executor and selected-candidate submission disabled
- production `.env`, `/root/bot`, `/root/.ssh`, and home data inaccessible
- empty capability set, `NoNewPrivileges`, strict filesystem protection
- systemd security exposure score 3.0 (`OK`)

Current service health after commissioning: active, current PID 648469, NRestarts 0, build errors 0, duplicate intents 0, submissions 0. Commissioning attempts initially failed before reading a signal because the new user lacked the V3 file group; the final unit uses V3's read-only group and did not change V3 permissions or content.

One fifth V3 signal arrived during compilation. It was preserved as a post-request late startup handoff:

- ID: `8ycb-v3-8ad6229f799983dfa46db343`
- mint: `CWYm4A4yHXJ2L2hwrsW7iCnB72VzLJTysgo8HKSKpump`
- signature: `4dewiXAekrYEmhbS9JZs31jPRvZ16FPrQD5wQyqxt85oDZ8v2gzNJLs92xYwVoejum4Bt5EYTQkkYWFfVSPkAxhY`
- real Rust build/sign succeeded; adapter receipt -> intent 4.303 ms; intent -> buy ready 0.270 ms
- signal -> adapter was approximately 1,359.247 seconds because the translation service was not yet online
- correctly marked `lateStartupHandoff=true` and excluded from live latency percentiles
- submission count 0

There are currently zero latency-eligible future signals. Future rows will record all requested receipt, intent, build, sign-ready, transaction-ready, and blocked-submission timestamps.

## 5. Buy-path feasibility

Fixture distribution (240 builds): P50 0.452 ms, P75 0.507 ms, P90 0.641 ms, P95 0.949 ms, max 3.209 ms. Pure build duration was P50 0.303 ms, P75 0.346 ms, P90 0.382 ms, P95 0.416 ms, max 1.250 ms.

Hit rates: <=10 ms 100%, <=25 ms 100%, <=50 ms 100%, <=100 ms 100%.

Conclusion: <=25 ms from an already-emitted V3 signal to a locally signed ready transaction is realistic with wide observed headroom. Reliability on genuine real-time V3 arrivals remains to be measured by the running service; the current evidence does not include disk pressure spikes, process descheduling extremes, or landing.

## 6. Landing and fill uncertainty

No-submit work cannot determine leader scheduling, validator/relay latency, competitive ordering, landing slot/time, expiration/failure probability, intervening bonding-curve movement, tokens actually received, or whether a competing transaction lands first. The signed fixture transaction proves only construction readiness.

Later tiny-live telemetry must capture local submit time, every sender attempted, first sender ACK, signature status transitions, landing slot, provider receive time, exact token balance delta, full wallet SOL delta, program error/expiration, actual compute consumed, and all fees/tips. No live calibration is authorized by this report.

## 7. Exit-path result

The actual `ExitWatcherRegistry` and actual sell builder were exercised with TP, SL, and max-hold states. Frozen thresholds were TP +20%, SL -20%, `max_hold_ms=120000`, 100% sell. The 120-second boundary was tested by advancing the watcher clock input exactly to entry+120,000 ms, not by sleeping wall-clock time.

Across 720 sell probes:

- decision -> sell intent: sub-0.01 ms typical (recorded per probe)
- sell intent -> ready: P50 0.124 ms, P75 0.134 ms, P90 0.148 ms, P95 0.173 ms
- decision -> ready: P50 0.128 ms, P75 0.139 ms, P90 0.153 ms, P95 0.180 ms, max 2.187 ms
- reasons exercised: `take-profit`, `hard-stop-loss`, `max-hold`
- duplicate action before ACK: 0
- duplicate action after terminal ACK: 0
- terminal ACK failures: 0

Because no buy landed, inventory was synthetic and marked `EXERCISED_AS_DETERMINISTIC_FIXTURE_NOT_LIVE_PROOF`. Production code audit verifies ATA derivation, Yellowstone balance cache, exact-balance requirement, RPC fallback, activation, action queue, terminal guard, and sell construction. It does not prove 8YCB-specific live inventory visibility or sell landing.

## 8. First-sellable reconciliation

V3 remains frozen at P50 122 ms, P75 178 ms primary, and P90 346 ms, all labeled `EXTERNAL_EXECUTION_TELEMETRY_REFERENCE`. They were not changed or relabeled as 8YCB-specific.

To replace them, a live cohort must pre-subscribe to the exact Token-2022 ATA, record first exact nonzero balance callback with slot/provider/kernel and user-space timestamps, then relate that to the first sell transaction actually accepted and landed. A balance callback proves inventory visibility, not sellability. Without separately authorized immediate sell probes, actual strategy exits later will create right-censored first-sellable observations; that censoring must be reported rather than imputed.

## 9. Cost model

Frozen values were copied unchanged:

- size: 0.1 SOL
- Pump fee: 125 bps each side, already included in curve replay
- buy CU limit 250,000 at 18,000,000 micro-lamports/CU: 0.0045 SOL configured priority-fee estimate
- sell CU limit 200,000: 0.0036 SOL configured priority-fee estimate
- base fee: 0.000005 SOL each side
- round-trip non-Pump configured estimate: 0.00811 SOL
- Jito tip: 0; Helius tip: 0; optional sender fee: 0 in this translation
- ATA creation: 0.00203928 SOL; frozen research treats it as recoverable, but live capital remains locked until the empty account is actually closed

A transaction that never lands costs no chain fee. A landed failure can still be charged base and prioritization fees; the live record must take the actual fee from transaction metadata rather than infer it from success/failure.

`SHADOW_EXPECTED_PNL` is frozen modeled sell proceeds minus 0.1 SOL and frozen base/priority costs, with Pump fees inside curve replay and rent treated as recoverable. `ACTUAL_LIVE_REALIZED_PNL` is observed sell wallet credit minus observed buy wallet debit, all successful/failed transaction fees and tips, optional fees, and unrecovered rent. Future reconciliation must decompose entry degradation, exit degradation, fee/failure/rent differences, and total actual-minus-shadow gap.

## 10. Live calibration telemetry contract

The exact machine-readable contract is in `live_calibration_telemetry_contract.json`. It requires paired shadow and actual fields for the same immutable signal ID, including intended/modeled entry, modeled first-sellable and exit, actual build/submit/landing, exact tokens and wallet deltas, first balance visibility, actual exit decision/build/submit/landing, every fee/tip/failure/rent effect, and realized wallet PnL. Adaptive tuning is prohibited.

## 11. Recurrent-wallet telemetry

The translation service copies only completed V3 `external_flow` telemetry for its processed signal IDs. It never consumes recurrent-wallet participation before buy construction and never changes the frozen wallet set.

For the fifth late-handoff signal:

- <=100 ms: 0 recurrent SOL / 0 total external SOL
- <=250 ms: 1.500 recurrent SOL / 5.000 total (30.0%)
- <=500 ms: 6.300 / 9.800 (64.29%)
- <=1000 ms: 6.434 / 9.934 (64.77%)

## 12. Broadcast safety

Safety evidence:

- optimized standalone binary SHA-256: `cb83b401a016ed8c43a53b4f9340310039d15228fea9e080753dc5da5fa4af3d`
- Rust source SHA-256: `c42224b4b2981211d0dcde68e120803ce8543f9c828bde61be06f0838ca35e59`
- adapter SHA-256: `b3e666be336250b2ad26788d5b8dd61e5eae7b47fa6a870fc502a8f5094660d0`
- release `nm -C`: zero `broadcast_signed_tx`, `race_broadcast`, `send_candidate`, or execute-command symbols
- release strings: zero `sendTransaction`, `sendBundle`, Jito endpoint, or execute-command strings
- compiler marked broadcaster/execute functions dead; link-time elimination removed them
- request surface exposes only `build8ycbProductionNoSubmit`
- real executor returns `senderPath=dry-build-no-submit`, `broadcastMs=0`, no candidate count
- signer is generated inside the process, unfunded, and not loaded from production secrets
- all tip values zero; all sender URL lists empty; every sender boolean false
- runtime network sample found only the read-only blockhash HTTPS request
- 83 Rust executor/exit-watcher tests passed; 12-fixture parity and 960 signed dry builds passed
- translation submissions 0; V3 submissions 0

The probe was **not live-tested**: there was no broadcast, landing, fill, or real inventory. This is intentional and is the principal remaining uncertainty.

## 13. V3 unchanged

At cutoff, `edgesniffer-8ycb-prospective-shadow-v3.service` remained active with PID 621336 and NRestarts 0. Cohort start is `2026-09-03T21:20:47.141763Z`; N=5, resolved=5; `transaction_submission_count=0`. Its service, scripts, frozen spec, recurrent-wallet set, trigger, economics, entry/exit assumptions, and output files were not modified. Collection toward N25 continues.

## Important limitations

- Timing is overwhelmingly fixture/local build evidence: 240 buys and 720 sells, but zero real-time eligible future V3 signals so far.
- Transaction ready-to-send is not transaction submitted, landed, ordered, or filled.
- The ephemeral signer changes public-key-derived accounts from a future live wallet, although instruction shape, signing primitive, message size, and executor code path are the same.
- Modeled token output is construction telemetry only and does not predict the actual fill after intervening curve movement.
- No real 8YCB ATA balance became visible, so exact live inventory activation and true first-sellable timing remain unmeasured.
- The ledger handoff is intentionally isolated and safe but adds a file/poll boundary absent from a future in-process integration.
- The fifth post-request signal was captured late and is excluded from real-time percentiles.
- No conclusion about V3 alpha strength is changed by this engineering report.

## Final classification

`8YCB_PRODUCTION_TRANSLATION_READY_FOR_FUTURE_TINY_LIVE_CALIBRATION`

Meaning: parity, local construction speed, exit state-machine behavior, and broadcast isolation pass the no-submit gate. This does **not** authorize real-money, dust, or other transaction submission. Continue the new no-submit production shadow and unchanged V3 collection; any future tiny-live step requires separate explicit authorization.
