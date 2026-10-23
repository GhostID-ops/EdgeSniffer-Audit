# 2CQ new-wallet route census V1

Subject wallet: `9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw`

This is an isolated, read-only research lane for the confirmed current 2CQ wallet. It does not replace the legacy 2CQ wallet evidence and does not alter KEFI collectors, frozen policies, or trading services.

Current materialization checkpoint:

- Raw finalized frames: 6,261 at the materialization run.
- Resolved route inventory at that run: Pump bonding-curve CPI 1,018; Pump bonding-curve top-level 249; PumpSwap CPI 92; PumpSwap top-level 10; Raydium LaunchLab 24; Meteora DLMM 6; unresolved 4,825.
- Adapter output: 1,350 event rows. Pump bonding-curve top-level has 230 buys; Pump bonding-curve CPI has 1 buy and 1,008 sells; PumpSwap direct has 7 buys; PumpSwap CPI has 92 sells; Raydium CLMM top-level has 10 buys; Meteora DLMM top-level has 2 buys.
- All 1,350 rows are explicitly marked `TRAILING_32_BYTES_EXTENSION`. The adapter strips and records the observed 32-byte trailing extension only after the canonical decoder rejects the full payload; this is an adapter hypothesis requiring fixture-level validation before economics or policy search.
- Each emitted event currently maps to a unique source signature. The asymmetric buy/sell distribution is retained as an audit item, not interpreted as a strategy result.
- The corrected raw instruction/state validation gate checked 1,488 current event rows: 1,488 passed and 0 failed. Of these, 491 are exact non-SOL quote events excluded from SOL economics and 884 are SOL-economics eligible.
- The canonical artifacts were promoted only after that gate: `canonical-route-census.json` and `canonical-route-events.jsonl`.
- The fixed-exit Pump curve surface ran at 0.1/0.5/1.0 SOL across 5/10/15/30/60/120/300 seconds for 195 SOL-quoted buy events. Historical block-time actionability was extremely low because this wallet-only tape does not contain a dense post-delay same-mint mark path; the surface is descriptive discovery only and is not validation.
- Route inventory now distinguishes direct routes, router/composite routes, and unresolved programs; composite routes are no longer silently merged into `UNRESOLVED_ROUTE`.
- No policy was selected or frozen. Prospective validation remains pending a new-wallet live-parity cohort with contemporaneous controls and sufficient post-entry marks.
- Prospective collector: `edgesniffer-2cq-new-wallet-route-source-v1.service`, receive-only, active, `NRestarts=0` at deployment verification.

## Prospective causal tape implementation (2026-10-19)

- Built `scripts/captureWalletProspectiveCausalTapeV1.py` and its focused regression tests. It tails the immutable wallet source, treats each finalized wallet transaction as an anchor, and records successful same-slot transactions as contemporaneous controls with raw transaction hashes, slot position, route-resolution output, and decoder errors.
- Built unit `edgesniffer-2cq-new-wallet-prospective-causal-tape-v1.service`. It is receive-only, uses the existing source and cohort boundary, and does not select policies, evaluate PnL, restart existing collectors, or submit transactions.
- Local verification: Python compilation and 5 focused tests passed.
- Deployed to the current VPS `159.195.207.109` after correcting the stale `87.99.145.246` address. The service is `active`, `enabled`, and has `NRestarts=0`; the existing wallet source remains untouched.
- Initial remote startup catch-up reached 13,293 append-only causal records. This is collection evidence only, not a policy or PnL result.
- The v1 runtime audit found an envelope-classification defect: same-slot controls were captured, but wallet anchors were not recognized from `getBlock(full)` responses. The v1 output is preserved as evidence and is not used for economics.
- Corrected versioned service `edgesniffer-2cq-new-wallet-prospective-causal-tape-v2.service` is now active with `NRestarts=0`; v1 was stopped, not deleted. Initial v2 checkpoint: 533 records, including 2 anchors and 531 same-slot controls. The anchor/control distinction now passes the focused tests and runtime check.
- Immutable discovery audit `causal-audit.json` completed for snapshot `immutable-discovery-20261019T202608Z`: 452 unique anchors, 161,828 successful same-slot controls, 0 malformed records. The gate remains blocked because 370 anchors are unresolved by the current route resolver and the tape has 0 post-entry mark rows. No economics or PnL was calculated from this snapshot.
- Required next gate: repair/expand route resolution for the populated anchor families and add a deduplicated post-entry market-window collector with native receive/block lineage. Only then can the causal 0.1/0.5/1.0 SOL economics surface run.

## Causal economics build checkpoint (2026-10-19)

- Route repair artifact: `route-repair-report.json` classified the 370 unresolved anchors as 82 exact single-venue rows, 1 composite route, and 369 unresolved-program rows. Unknown programs were not falsely assigned to a venue.
- The full-block window prototypes were stopped after proving their storage cost was unsuitable; their failed intermediate outputs were removed to restore the research-volume quota. Raw source, sealed discovery snapshot, causal tape, route repair, and selective pool-window evidence were preserved.
- Production capture is now the selective pool-window service `edgesniffer-2cq-new-wallet-pool-windows-v9.service`, active with `NRestarts=0`. It queries exact PumpSwap pool signatures, fetches only pool transactions, applies the verified trailing-32-byte extension adapter, and records the one-second/300-second causal window.
- Current selective tape checkpoint: 11 anchor-window records and 433 market transactions. Immutable `/tmp` checkpoint hash: `a763db3c2417790327610d376758a44a46964fa6ef0ad038cf4c4ae0092d1cfa`.
- Causal evaluator `scripts/evaluateWalletCausalEconomicsV1.py` is built and tested. It models one-second entry delay, exact PumpSwap fees, 0/25/50 bps stress, constant-product impact, 5/10/15/30/60/120/300-second exits, missed exits, and fixed network fee.
- Current surface run: 7,842 sealed raw anchors loaded; 11 windows; 10 exact PumpSwap buy anchors; notionals 0.1/0.5/1.0 SOL; all seven exit horizons. At the current partial direct PumpSwap sample, only 3/10 anchors had usable marks for the 10-second horizon and those were negative across the tested notionals/stresses. This is a coverage checkpoint, not a strategy conclusion.
- Economics artifact hash: `13C15C0B202B9AC0739DB3BDC6BEBFFA54C987CFD6F935E6921439C815D1410C`. `policy_frozen=false`; `trading_enabled=false`.
- Focused verification: 7 tests passed and `git diff --check` passed. The collector remains receive-only while additional windows accumulate; promotion remains blocked until route coverage and post-entry mark coverage are sufficient.
- This tape is the coverage prerequisite for realistic 0.1/0.5/1.0 SOL economics. Its output must still be evaluated with a separate causal evaluator using the fixed one-second delay, fees, impact, failed entries/exits, and fresh validation cohort.

Next gate: validate the 32-byte extension layout against known instruction/event fixtures, repair unresolved route coverage, and rerun the census outcome-blind before any policy search or freeze.

## Batched strategy discovery lab (2026-10-19)

- Built `scripts/runWalletStrategyMatrixV1.py` and the immutable contract `strategy-matrix-v1.json`.
- The lab evaluates 9 pre-registered exact PumpSwap entry filters, 11 exit policies, 0.1/0.5/1.0 SOL notionals, and 0/25/50 bps stress in one offline batch. It models the one-second entry delay, venue fees, constant-product impact, missed/failed trades, fixed exits, take-profit/stop-loss, trailing protection, and partial take-profit.
- Local discovery run: 11,548 source anchors loaded, 11 exact PumpSwap anchor windows prepared, 5,148 candidate outcomes across 495 combinations, and 0 controls. Therefore all rankings remain exploratory; `policy_frozen=false`, `selection_performed=false`, `promotion_blocked=true`, and `trading_enabled=false`.
- Focused verification: 7 tests passed, Python compilation passed, and `git diff --check` passed.
- Full details: `strategy-lab-report-v1.md`. The next gate is a larger sealed selective-window cohort with contemporaneous controls, followed by one frozen route-specific candidate and fresh prospective validation.

## Full system audit and collector repair (2026-10-19)

- Read-only VPS audit used the current address `159.195.207.109`. The new-wallet source and selective pool-window collectors were active with `NRestarts=0`; the stopped full causal-tape services remained inactive. Trading flags were verified as `LIVE_TRADING_ENABLED=false`, `RUST_ONLY_BUYS=true`, `RUST_ONLY_SELLS=true`, and `JUPITER_FALLBACK_ENABLED=false`.
- Source integrity checkpoint: 12,313 valid JSONL records, 0 malformed. Pool-window file: 897 valid records, 0 malformed, containing 18 unique exact PumpSwap anchor windows and 469 unique market signatures. The file had 11 duplicate anchor envelopes and 399 duplicate market rows from prior ephemeral-state resets; those duplicates were not used as independent samples.
- The duplicate repair preserves the original file as an evidence archive, writes a deduplicated working file, and moves the v9 state file from `PrivateTmp` to persistent research storage so a future service restart cannot silently recreate duplicates. This is an integrity repair, not a new cohort or strategy change.
- Route re-investigation found 18 exact wallet PumpSwap buys in the current source and 0 additional exact PumpSwap buys after the latest existing anchor window. The unresolved 369 anchors remain unsupported by canonical venue invariants; no unsafe decoder was admitted.
- Storage warning: the VPS root/research filesystem was at 94% used with approximately 66 GB free. No large full-block collector will be started.
- The clean-cohort clock is event-driven. The current source covered roughly 3.86 hours and contained 18 exact PumpSwap buys, or about 4.7/hour in that interval. The rate is not stable, so a reliable ETA is expressed as a sample target, not a calendar promise.
- Full audit artifact: `system-audit-20261019.md`. The repaired current tape now contains 18 unique anchor windows and 469 unique market signatures; the refreshed lab has 18 prepared windows, 8,316 outcomes, 495 combinations, 0 controls, and remains promotion-blocked. The next build is a bounded contemporaneous-control collector, not another full-block tape.

## Bounded contemporaneous controls and final operational audit (2026-10-19)

- Built and deployed `edgesniffer-2cq-new-wallet-bounded-controls-v1.service` on VPS `159.195.207.109`. It is receive-only, enabled, active, and has `NRestarts=0`; the existing source and selective pool-window collectors were not restarted or changed.
- The bounded collector uses only finalized exact PumpSwap wallet-buy anchors, fetches the finalized block for those anchors, retains at most 25 exact PumpSwap control transactions from the same slot, and records raw transaction lineage plus route/decoder evidence. It does not select policies, calculate promotion results, or submit transactions.
- Current bounded-control checkpoint: 18 anchor-control windows, 450 unique same-slot PumpSwap controls, 0 malformed rows, 0 duplicate control identities, and 0 collector errors. Output SHA-256: `b0efffad52a25f65c25709a245f2f9bfdf49c50006dfe8357347a4774c880412`.
- Current source checkpoint: 12,562 valid finalized records, 0 malformed, and 0 duplicate source signatures. Current selective pool tape: 487 valid records, 18 unique anchor windows, 469 unique market signatures, and 0 duplicate identities after repair.
- Route coverage remains fail-closed: the 369 unresolved anchors were investigated against current raw evidence but no canonical venue/event invariant was found, so no speculative decoder was admitted. They remain preserved as unresolved research candidates, not negative trades.
- Full operational audit: the three active new-wallet collectors are healthy with zero restarts and zero recent warning/error journal entries; live trading remains disabled (`LIVE_TRADING_ENABLED=false`, Rust-only flags true, Jupiter fallback false). The VPS as a whole is not clean: unrelated legacy/auxiliary units remain failed and storage is approximately 94% used / 68 GB free. Those failures did not affect this lane and were not restarted.
- Collection estimate remains event-driven. The observed rate is approximately 4.7 exact PumpSwap wallet buys/hour over the current capture interval: about 7 more hours for 50 total exact windows, about 18 more hours for 100, and roughly 19 more hours for 30 actionable windows at the current 5/18 mark-coverage rate. These are estimates, not guarantees; controls are now collected in parallel.
- Next gate: keep collecting without resetting the cohort; seal a larger discovery snapshot, rerun the same pre-registered 0.1/0.5/1.0 SOL causal strategy matrix with controls, freeze nothing from that snapshot, and validate any candidate only on a separate fresh prospective cohort.

## Storage correction (2026-10-19)

- The active route-census namespace is now persistently bind-mounted onto the new local `/dev/vdd1` volume, which has approximately 1.3 TB free. The separate NFS expansion mount was not involved.
- An interrupted legacy-archive copy was stopped and its incomplete duplicate removed. The authoritative old archive and all active evidence remain preserved.
- Post-cutover audit: 17,657 source rows, 22 pool windows, 1,769 unique market rows, 22 control windows, and 550 unique controls; all active streams have zero malformed rows and zero duplicate identities. All three receive-only collectors remain active with zero restarts.

## Refreshed strategy-lab result (2026-10-20)

- Reran the unchanged pre-registered matrix against the current post-cutover data: 17,657 anchors loaded, 22 exact PumpSwap windows, 1,769 market rows, 572 control records loaded (550 controls plus 22 envelopes), 10,296 outcomes, and 495 surface combinations.
- The maximum actionable count for any combination is 9/22. Among actionable combinations, the best result remains negative: the best 9-actionable group produced `-0.5824 SOL`, zero wins, and PF `0.0`; the best 4-actionable group produced `-0.2499 SOL`, zero wins, and PF `0.0` at 0.1 SOL/0 bps.
- This is a negative exploratory signal, not a final rejection: 4-9 actionable windows are insufficient for strategy selection, and the current matrix is not allowed to select or freeze a policy. Controls are now captured for later causal comparison but are not used to optimize this run.
- No policy was frozen, no live/dust execution was enabled, and no trading decision changed. The next efficient step is continued collection toward at least 30 actionable windows and a larger sealed discovery snapshot, then the same matrix plus control comparison before any route-specific freeze.

## Storage expansion and path migration (2026-10-20)

- The new 1.5 TB volume is `/dev/vdd1`, mounted at `/mnt/edgesniffer-canonical-hot`. The active 2CQ namespace is bind-mounted there while retaining its original service path, with a persistent `/etc/fstab` entry.
- The complete inactive Yellowstone archive was migrated to `/dev/vdd1` and verified before the duplicate on the old full volume was removed. The preserved archive is at `/mnt/edgesniffer-canonical-hot/migrated-research/2cq-yellowstone-full-meta-prospective-v1`.
- Six inactive KEFI evidence sets totaling approximately 265 GB were moved to `/dev/vdb1` under `root-offload`, verified, and bind-mounted back to their original root paths. Root usage fell from 100% during the failed first attempt to approximately 85%.
- The first root-offload attempt used a path outside the vdb mount and was stopped; its partial copy was removed. No source evidence was deleted by that failed attempt.
- Final capacity checkpoint: root `/dev/vda3` approximately 85% used / 157 GB free; research `/dev/vdb1` approximately 43% used / 848 GB free; canonical hot `/dev/vdd1` approximately 87% used / 188 GB free. The active collectors remain healthy and no trading service was enabled.
- Final current collector counts: 17,112 valid source rows; 22 pool windows and 1,769 market rows; 22 bounded control windows and 550 same-slot controls; 0 malformed rows and 0 recent collector errors.

## Route-resolution and collector-integrity audit (2026-10-19)

- Diagnostic coverage: `93JqDj2DjxDYuKPY3Z1rUG3YzPJykyMsrisPnvWYeuq5` occurred in 1,027 raw frames and `ABWKmNctDYqfC3nTBJW63oFkU5KYAjfMFigWzhuia8NV` in 4,878. The unresolved anchor split remains 124/241. No venue adapter was admitted: `93J...` has no recognized venue CPI; `ABWK...` has only 8 known Pump CPI frames and no canonical venue evidence for the wrapper-only frames. Artifact: `immutable-discovery-20261019T202608Z/unknown-program-diagnostic-v1.json`.
- The 369 unresolved anchor rows remain fail-closed because they do not contain a proven mint/side transition. Route repair remains 82 exact single-venue, 1 composite, and 369 unresolved-program anchors; no strategy or venue was inferred.
- Source/pool JSONL integrity was repaired with full pre-repair files and hashes preserved. Two malformed source records (lines 10,185 and 10,192) and one malformed pool record (line 445) were removed only after backup; final audits show zero malformed records.
- The redundant full causal-tape v2 service was stopped/disabled after its 7.3 GB output exhausted the research volume. Its output was moved intact to `/root/edgesniffer-evidence-archive/2cq-new-wallet-route-census-v1/live-causal-tape-v2/`; the finalized source and selective pool collector remain active and receive-only.
- Current checkpoint: 11,558 valid source frames; 21 anchor windows; 790 market transactions; both active collectors report zero current restarts; trading remains disabled.
- Read-only economics refresh: 18 exact PumpSwap anchors, 4 actionable marks, 14 missed/failed attempts. At 30 seconds/0 bps: `-0.2456 SOL` at 0.1 SOL, `-1.2343 SOL` at 0.5 SOL, `-2.4842 SOL` at 1.0 SOL. This is insufficient-coverage evidence, not a strategy conclusion. Artifact hash: `4288184F7ED73C431622832139D2D18040C6789040F09940FB7158CD732814`.
- Verification: focused tests, malformed-record handling, Python compilation, and `git diff --check` passed. No live or dust execution was enabled.

## Native receive/inclusion telemetry gate (2026-10-20)

- Built `scripts/auditNativeTimingTelemetryGateV1.py` and deployed the receive-only unit `edgesniffer-2cq-new-wallet-native-timing-telemetry-v1.service`.
- The gate reads the existing source and deduplicated prospective windows without changing either tape, selecting a policy, restarting existing collectors, or submitting transactions. It records source observation time, transaction ordering, the one-second decision deadline, exit target, and first post-exit market observation.
- Current VPS artifact: `/mnt/HC_Volume_106775198/edgesniffer-research/2cq-new-wallet-route-census-v9/native-timing-telemetry-gate-v1.json`.
- Current checkpoint: 46,034 source rows, 522 anchors, and 74,841 market rows; 0 malformed source/window rows; 515 anchors have a post-exit market observation.
- The gate is intentionally blocked: native receive-clock coverage is 0 because `captured_at_utc` is an RPC-finalized observation time, not a ShredStream/gRPC receive clock. Submitted-exit and exit-inclusion coverage are also 0 because execution remains disabled.
- The initial service start had three namespace restarts because its output directory had not yet been created. The directory was created; the service now runs with `ExecMainStatus=0`. All eight existing receive-only collectors remain active with `ExecMainStatus=0` and `NRestarts=0`.
- Deployed script SHA-256: `3e702b1eca317eab6cafe4581750d22dbd7cc561e095fae11f325b841b0937d3`. Deployed unit SHA-256: `61732ef741f907712376647ea5071f7cd7d3b0abd62133f3431c8ff8d333a019`.
- Next roadmap gate: add native monotonic receive/source/slot fields at ingestion and, only if execution is separately authorized, submitted-exit and inclusion telemetry. Until those fields exist, future economics must remain explicitly marked RPC-observed/quote-parity rather than live execution parity.

## Formula-controlled parity comparison (2026-10-20)

- Built `scripts/comparePumpCurveParityFormulaV1.py` to hold the original quote, fee, notional, network-fee, and selected-mark `post_state` exit formula constant while varying only ordering and delayed entry state.
- Replayed the sealed 381-window dataset at 0.5 SOL, 50 bps stress, one-second entry delay, five-second exit, and 0.00001 SOL network fee. The original formula reproduces `+18.991028950 SOL`, PF `2.7102`, and 373 actionable rows.
- Keeping that exact formula but advancing the entry reserve state through the one-second boundary produces `-6.913341080 SOL`, PF `0.6929`, and 373 actionable rows. The strict no-entry-boundary subset is `-1.849940219 SOL`, PF `0.8734`, and 221 actionable rows.
- This establishes that the large deterioration is primarily caused by delayed entry state, not by silently changing fees or the exit valuation formula. The earlier `+12.111 SOL` delayed proxy was not a full one-second replay; it advanced only through safe same-second events and was therefore too favorable.
- The earlier `-5.192 SOL` native-boundary figure remains a boundary diagnostic, not the canonical updated PnL, because that report used a pre-boundary exit state for one candidate class. The formula-controlled comparison supersedes it for like-for-like interpretation.
- Next gate remains a fresh prospective cohort with native receive-clock fields and the corrected formula-controlled evaluator. No strategy is frozen or promoted from this replay.

## Corrected delayed-entry strategy lab (2026-10-20)

- Built `scripts/runPumpCurveDelayedStrategyLabV1.py`. It uses the delayed reserve state after the one-second boundary, the original exact curve/fee/selected-mark formula, and tests five entry filters, fixed 5/10/15/30/60-second exits, TP/SL, trailing protection, partial take-profit, and 0.1/0.5/1.0 SOL at 50 bps stress.
- Sealed discovery run: 488 source anchors loaded, 381 Pump-curve windows prepared, and 180 pre-registered strategy surface combinations. The result is discovery-only with `policy_frozen=false`, `selection_performed=false`, and `promotion_blocked=true`.
- The strongest cross-size exploratory family was positive-flow plus 5% activation/3% trailing over 60 seconds: 0.1 SOL `+1.4850 SOL` / PF `1.77`; 0.5 SOL `+5.9520 SOL` / PF `1.63`; 1.0 SOL `+7.9450 SOL` / PF `1.39`. The stricter flow-plus-price variant was also positive but had fewer trades.
- These rows still contain block-time ambiguity. For the strongest 1.0 SOL row, 94 of 221 actionable outcomes were entry-boundary ambiguous; for the 0.5 SOL row, the ambiguity count is reported in the artifact. These are hypotheses, not live-parity evidence.
- Registered validation candidates in `pump-curve-delayed-strategy-preregistration-v1.json`: positive-flow trailing and flow-plus-price trailing, each tested separately at 0.1/0.5/1.0 SOL on a fresh native-telemetry cohort. No candidate is frozen yet.
- Dynamic and partial-exit rules were tested in the discovery matrix but were not silently promoted based on this tape. Their future validation status is governed by the preregistration artifact.

## Native receive-clock adapter

- `rust-sidecar/src/bin/native_shredstream_wallet_receipts_v1.rs` is a separate receive-only native ingress adapter. It records the timestamp immediately after each local Jito ShredStream gRPC message is yielded and before entry decoding, with `nativeReceiveUnixNs`, `nativeReceiveMonotonicNs`, `nativeReceiveSlot`, `nativeReceiveSource`, transaction ordinal, signature, and immutable payload hashes.
- VPS unit: `edgesniffer-2cq-new-wallet-native-shredstream-receipts-v1.service`; output namespace: `/mnt/HC_Volume_106775198/edgesniffer-research/2cq-new-wallet-route-census-v9/native-shredstream-wallet-receipts-v1/`.
- The service is healthy and connected, but native packet coverage is currently zero. `doublezerod` has its reconciler disabled, `doublezero1` is absent, and `shred-proxy` is idle with no active multicast group. Enabling or repairing that network path requires explicit approval.
- Until native packets arrive and submitted-exit/inclusion telemetry exists, the timing gate remains blocked and all current economics remain RPC-observed research evidence, not live-parity validation.
- The existing DoubleZero reconciler was enabled with approval, but subscribing the VPS identity (`GT6Hf2FDTkqPVc8g6MutsFVPA8P8c6gNn4fKtLjRY9hk`) to `edge-solana-shreds` was rejected because the account has no available credits. No tunnel or `doublezero1` interface was created; the reconciler remains enabled for convergence after funding.

## AllenHark upstream authorization blocker (2026-10-20)

- The renewed AllenHark/Jito ShredStream container is running on the correct VPS and listening locally on UDP `20000` and gRPC `9999`, but the upstream block-engine heartbeat is rejected with `PermissionDenied: pubkey not whitelisted`.
- The authentication keypair inside `/auth.json` contains 64 bytes; its public key was derived without printing or exporting the private key: `DLfKyoNTRsqRBjmucr7frAUoTBi72zkqwVYPRwQrRgDy`.
- The proxy reports `packets_count=0`; the native receipt adapter is connected but has `observedStreamMessages=0` and `observedWalletTransactions=0`. The local listener is healthy, but the AllenHark upstream feed is not authorized.
- Required external action: whitelist `DLfKyoNTRsqRBjmucr7frAUoTBi72zkqwVYPRwQrRgDy` in the AllenHark package/account, then verify heartbeat success and native receipt arrival. No collector or trading policy is changed.

## Native-wallet coverage investigation and v2 observer (2026-10-20)

- Recent finalized source rows confirm that 9qz7 is the fee payer and static account index 0, so the original fee-payer filter is not the explanation for the zero native count.
- The original v1 observer remains unchanged in its evidence namespace. A separate v2 observer now records wallet appearances anywhere in static account keys and adds decoded-transaction counters; it is active and receive-only.
- Verification: v2 decoded `48,946` transactions and found `0` 9qz7 matches; v1 decoded `108,407` stream messages and found `0` fee-payer matches. The existing compact AllenHark observer decoded `6,430,937` transactions and also missed five known finalized 9qz7 signatures.
- VPS checks show UDP `20000` listening, UFW inactive, zero UDP receive-buffer errors, and zero proxy UDP-drop delta. Repeated incomplete-shred/`Invalid Merkle root` diagnostics remain. The current blocker is upstream AllenHark coverage/integrity for the target wallet, not local wallet matching.
- Keep both native observers and all existing collectors running. Do not create a native-timing cohort until a genuine 9qz7 native receipt is observed.

## Final experiment gate audit (2026-10-21)

### Receive-only exit-intent bridge (2026-10-21)

- Added `scripts/captureReceiveOnlyExitIntentTelemetryV1.py` and a dedicated systemd unit, `edgesniffer-2cq-new-wallet-receive-only-exit-intent-v1.service`.
- The collector is idempotent and records the frozen policy's hypothetical exit deadline for each anchor. It never creates, signs, submits, or labels a transaction as included.
- The current ledger contains `1,973` normalized rows with `0` malformed rows. Every row is marked `execution_mode=RECEIVE_ONLY_SHADOW`, `evidence_boundary=PAPER_INTENT_ONLY`, and `exit_result=NOT_SUBMITTED_RECEIVE_ONLY`.
- The native timing auditor now reports this ledger separately as `shadow_exit_intent_telemetry`. It explicitly records `does_not_satisfy_submitted_exit_gate=true`.
- VPS verification: both the new collector and the native timing auditor are `active`; the auditor reports native receive coverage ratio `0.1880385200202737`, actual submitted exits `0`, actual inclusions `0`, and overall `gate_status=BLOCKED_NO_SUBMITTED_EXIT_TELEMETRY`.
- This completes the receive-only engineering portion of the blocker. The remaining requirement is genuine executor telemetry: `exit_submit_monotonic_ns`, `exit_submit_signature`, `exit_inclusion_slot`, `exit_inclusion_transaction_index`, and `exit_result` from an explicitly authorized execution-calibration run. No such run was enabled.

### Isolated execution-calibration attempt (2026-10-21)

- The explicitly authorized calibration lane was built separately from the teacher-conditioned cohorts and frozen policies. It is capped at one `0.001 SOL` Rust-only attempt, with Jupiter fallback disabled and no strategy-promotion authority.
- Calibration v1 generated a real buy signature, but the executor recorded `status_timeout`, `slot=null`, and `confirmationStatus=null`; independent RPC checks also returned no signature status, wallet reconciliation remained zero, and no sell was submitted. This is preserved as a failed/unproven submission attempt, not inclusion evidence.
- Calibration v2 is a separate namespace with a 10-second freshness gate. It is currently active and waiting for a fresh legacy-SPL Pump candidate. Token-2022 candidates remain fail-closed because the canonical sell-template adapter is not certified.
- Detailed audit: `research/independent-execution-calibration-v1/report.md`. VPS v1/v2 audit namespaces are preserved under `/mnt/HC_Volume_106775198/edgesniffer-derived/independent-execution-calibration-v1/` and `...-v2/`.

- The canonical `9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw` route source is capturing finalized wallet transactions. The raw tape has 85,340 records at the current checkpoint, and the native receipt observers have confirmed fee-payer and static-account appearances.
- Discovery economics are complete across 0.1, 0.5, and 1.0 SOL, with a one-second entry delay, 5/10/15/30/60/120/300-second exits, and 0/25/50 bps stress cases. The discovery result remains hypothesis-only; controls are not a promotion decision.
- The preregistered Pump top-level policy is `PUMP_TOP_LEVEL_0_5_SOL_5S_STRESS50_V1`. Its fresh prospective cohort has sufficient wallet and control samples and an equal-sample comparison is ready for review. Promotion remains false by design.
- The repaired economics and prospective evaluators now emit strict JSON when a profit-factor denominator is zero; both patched files compile on the VPS.
- Final audit artifact: `/mnt/HC_Volume_106775198/edgesniffer-research/2cq-new-wallet-route-census-v9/final-experiment-gate-audit-20261021.json`; SHA-256 `ca4cf9711384ecb5c6dadccd8d42fb5888f53d1a7d21a9f24e192a01a9267c56`.
- Remaining promotion blocker: submitted exit-inclusion telemetry is absent (`0` submitted exits, `0` observed inclusions). Native receive-clock coverage is partial, not complete. No live or dust execution is enabled, so the experiment is research-complete through prospective paper review but not execution-parity-complete.
