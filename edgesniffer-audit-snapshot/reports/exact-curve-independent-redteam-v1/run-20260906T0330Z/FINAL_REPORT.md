# EXACT_CURVE Independent Red-Team Audit

Generated: `2026-09-06T04:08:35.683039+00:00`

## Executive Verdict

- **Engine observed-transaction validation:** PASS
- **Paired sign reversal:** REPRODUCED
- **Prospective exact subset:** `MATERIALLY_BIASED`
- **Historical local recovery:** `PARTIALLY_RECOVERABLE` only where the RPC cache already contains authoritative transaction evidence; the canonical local tapes alone are insufficient.
- **Final classification:** `EXACT_CURVE_ENGINE_REDTEAM_PASS_FUTURE_LABEL_COLLECTION_REQUIRED`

## Independent Transaction Audit

Scanned `212,378` cached finalized transaction payloads and decoded `100,650` current-schema Pump TradeEvents. Paired `100,650` events to decoded trade instructions.

Observed economic disagreements: `0`. Failed transactions with logged events were kept at zero mutation: `99` transactions / `99` events.

The global cache view has `13,255` apparent gaps across `99,204` adjacent event pairs because fetched curve windows overlap and are not globally complete. This is treated as missing evidence, not agreement. Complete authoritative windows are tested separately below.

Excluded from standard-curve arithmetic: `3,000` nonstandard Mayhem/non-SOL events and `214` complex/noncanonical token-delta cases.

Instruction variants: `{"buy": 27589, "buy_exact_quote_in_v2": 7596, "buy_exact_sol_in": 19189, "buy_v2": 4154, "sell": 36715, "sell_v2": 5407}`

Token programs: `{"legacy": 949, "token2022": 99701}`

Fee regimes: `{"p0:c0:cb0:bb0": 1519, "p95:c0:cb30:bb0": 20, "p95:c0:cb30:bb5000": 57454, "p95:c30:cb0:bb0": 1461, "p95:c30:cb0:bb5000": 40196}`

Exact-quote fee remainder allocations: `181` events / `185` lamports. The strict evaluator can reject these and `70` waived-creator cases; that is fail-closed coverage loss, not silently admitted bad economics.

### Formal Rounding Finding

The official SDK uses `floor(numerator / denominator) + 1`. The evaluator uses `ceil(numerator / denominator)`. They differ by one lamport only when `numerator % denominator == 0`. Observed affected events: `2`.

This is a real specification-level discrepancy and is retained as a hardening item. The current evaluator would fail closed on the two affected observed events rather than admit a wrong exact label; it cannot explain the large proxy-to-exact reversal.

### Outcome/Timing Semantics

Audited `12,018` exact output rows across all configured sizes/delays. Arithmetic, TP/SL, first-sellable, and true-T+120 disagreements: `0`.

Raw entry-state cross-check: `500/500` matched a decoded finalized TradeEvent exactly; mismatches `0`, unavailable raw rows `0`. Local-timestamp future-state failures: `0`.

Outcomes: TP `176`, SL `2,060`, true max-hold `9,782`. Counterfactual constraint stress by size: `{"10000000": {"external_status_flips": 1159, "external_transactions": 22055, "failed_chain_transactions": 50390, "rows": 3130, "rows_with_status_flips": 798}, "100000000": {"external_status_flips": 1318, "external_transactions": 21755, "failed_chain_transactions": 49778, "rows": 3082, "rows_with_status_flips": 798}, "1000000000": {"external_status_flips": 1848, "external_transactions": 19302, "failed_chain_transactions": 49352, "rows": 2849, "rows_with_status_flips": 1027}, "500000000": {"external_status_flips": 1576, "external_transactions": 20710, "failed_chain_transactions": 49592, "rows": 2957, "rows_with_status_flips": 960}}`.

## Paired 208-Row Recalculation

Recomputed directly from unique per-trade rows in `exact-outcomes.jsonl.gz`; no aggregate report was used.

| Model | N | PF | Expectancy | Median |
|---|---:|---:|---:|---:|
| Old MC proxy | 208 | 1.525309 | +1.589661% | -2.624608% |
| Exact curve | 208 | 0.088998 | -7.197289% | -3.178910% |

`63` rows changed return sign. Row-set digest: `5b3c991253cedff00108f6b9a21c4b9ac350134c272e6787410451d9f2f7925e`.

## Coverage Bias

Exact-evaluable: `236/1044`; excluded: `808`. Classification: `MATERIALLY_BIASED`.

Largest pre-outcome continuous shifts:

| Feature | Exact mean | Excluded mean | SMD | KS |
|---|---:|---:|---:|---:|
| raw.w10000.gross_buy_sol | 0.445133 | 1.13387 | -0.814 | 0.428 |
| raw.w10000.net_buy_sol | 0.445133 | 1.13387 | -0.814 | 0.428 |
| raw.w5000.gross_buy_sol | 0.445133 | 1.13387 | -0.814 | 0.428 |
| raw.w5000.net_buy_sol | 0.445133 | 1.13387 | -0.814 | 0.428 |
| raw.second_to_first_sol_ratio | 423.825 | 1104.33 | -0.813 | 0.420 |
| raw.w2000.gross_buy_sol | 0.444996 | 1.1273 | -0.808 | 0.427 |
| raw.w2000.net_buy_sol | 0.444996 | 1.1273 | -0.808 | 0.427 |
| raw.w1000.gross_buy_sol | 0.443357 | 1.11638 | -0.799 | 0.420 |
| raw.w1000.net_buy_sol | 0.443357 | 1.11638 | -0.799 | 0.420 |
| selected_mc_usd | 4589.86 | 4815.39 | -0.784 | 0.434 |
| raw.mc_usd | 4589.86 | 4815.39 | -0.784 | 0.434 |
| raw.w500.gross_buy_sol | 0.432075 | 1.07014 | -0.768 | 0.425 |

Categorical total-variation distances:

| Feature | TV distance |
|---|---:|
| instruction_variant | 0.433 |
| state | 0.353 |
| utc_hour | 0.275 |
| utc_date | 0.262 |
| token_program | 0.003 |
| source_timing | 0.000 |

## Historical Recovery

In a deterministic `120`-mint overlap sample, local normalized/matched observations contained `658` of `1,374` authoritative successful Pump event signatures (`47.89%`).

Within those complete authoritative windows, chronological reserve checks: `1,255` adjacent event pairs, `330` same-slot pairs, `0` continuity failures. Multi-event transaction atomicity failures: `0`. Failed logged events kept at zero mutation: `1`.

The locally inferred `native-shredstream-replay` reserve identity was checked against finalized TradeEvent post-state on `224` overlapping transactions: exact matches `0`, mismatches `224`. It is not an authoritative reserve archive.

More importantly, those local rows omit finalized status, TradeEvent post-reserves, transaction logs/meta, fee-config snapshots, and raw curve-account bytes. Instruction bytes plus ordering cannot identify which observed failed transactions mutated state, nor anchor exact reserves. Exact reconstruction from those tapes alone is therefore impossible without inventing state.

Current recoverability by split:

| Split | Frozen N | Authoritative exact currently recovered | Local-only additional exact recoverable |
|---|---:|---:|---:|
| Train | 964 | 0 | 0 proven |
| Validation | 428 | 0 | 0 proven |
| Final test | 766 | 8 | 0 proven |
| Prospective | 1044 | 236 | 0 proven beyond cached authoritative windows |

Historical recovery classification: `B — HISTORICAL EXACT RELABEL IS PARTIALLY RECOVERABLE`, restricted to periods/mints already covered by the authoritative RPC cache. The canonical corpus by itself cannot expand exact coverage.

### Evidence Coverage

| Source | First UTC | Last UTC | First slot | Last slot |
|---|---|---|---:|---:|
| normalized | 2026-08-28T13:39:25.262000+00:00 | 2026-09-06T04:08:20.076000+00:00 | 442350780 | 444698024 |
| detected | 2026-08-28T13:39:43.065000+00:00 | 2026-09-06T04:08:20.076000+00:00 | 442350829 | 444698024 |
| capsules | 2026-08-28T13:39:25.262000+00:00 | 2026-09-06T04:08:20.076000+00:00 | 442350780 | 444698024 |
| decisions | 2026-08-28T13:39:25.262000+00:00 | 2026-09-06T04:08:20.076000+00:00 | 442350780 | 444698024 |
| authoritative RPC cache | 2026-09-02T06:47:37+00:00 | 2026-09-06T01:23:05+00:00 | 443631807 | 444666714 |

### Local Evidence Suitability

| Source | Size GiB | Ordered | Finality | Exact reserves | Constraints | Exact-label suitable |
|---|---:|---|---|---|---|---|
| normalized | 23.361 | yes | no | no | yes | no |
| detected | 0.050 | yes | no | no | yes | no |
| capsules | 29.461 | yes | no | no | yes | no |
| decisions | 24.510 | yes | no | no | yes | no |
| decision_receipts | 4.151 | no | no | no | no | no |
| matched | 0.316 | yes | no | no | yes | no |
| wallet_8ycb_enhanced | 0.007 | yes | yes | no | yes | no |
| wallet_8ycb_trade_events | 0.001 | yes | yes | no | no | no |
| rpc_cache | 0.921 | yes | yes | yes | yes | yes |

The derived decision receipts explicitly describe source slots as grouping-only, not ordering or inventory proof. The 8YCB archives are targeted parsed wallet history, not lossless full-universe curve histories. Neither can expand exact Ranker labels.

## Required Future Evidence

For every eligible Pump mint, persist one lossless append-only event ledger containing:

1. Raw create transaction and all curve-touching transactions through completion/migration or the longest research horizon.
2. Signature, slot, block transaction index, instruction index/CPI order, block time, local receive/decode time, version, loaded addresses, and finalized `meta.err`.
3. Full Pump instruction bytes/accounts and decoded min-token/max-cost/min-output constraints.
4. Full logs or lossless TradeEvent bytes, including exact post reserves and fee/cashback/buyback fields.
5. Raw curve account bytes with slot/write-version at creation, each observed write, entry boundaries, completion, migration, and T+120.
6. Raw Global/FeeConfig/sharing-config bytes with account address, slot, write-version, and hash whenever the version changes.
7. Mint owner/program, raw mint bytes/extensions, supply, quote mint, and Token-2022 extension state.
8. Coverage reconciliation counters proving every curve signature has finalized status and every successful trade has a reserve transition.

Store this once in compressed hourly `zstd` partitions with manifests and SHA-256 checksums. Feature/score/outcome rows should reference evidence IDs rather than duplicate the raw transaction payload.

## Safe Research Use

The engine is safe as the target for new alpha research only for rows it marks exactly evaluable. Its current fail-closed transition validation contains the one-lamport boundary and strict fee-allocation mismatches as coverage loss; correcting those boundaries would recover coverage. It must never silently fall back to MC-ratio economics, and the 236-row prospective result must not be generalized as though coverage were random.

`EXACT_CURVE_ENGINE_REDTEAM_PASS_FUTURE_LABEL_COLLECTION_REQUIRED`
