# BWAM Deep Wallet Research

Wallet: `bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`

Generated: `2026-07-01`

## Executive Read

`bwam` is not a normal copy-trade source. The wallet looks like a launch/creator-side ultra-fast scalper rather than a passive buyer we can simply follow with a generic TP/SL policy.

The historical wallet numbers are extremely strong in the fetched window:

- 5,000 fetched transactions from `2026-06-28T18:04:25Z` to `2026-07-01T16:53:43Z`.
- About `4,000` reconstructed swaps.
- About `1,000` campaigns.
- Realized PnL around `+2,156` to `+2,201 SOL`, depending on the accounting pass.
- Net 7-day/newest-window PnL: `+2,186.0339 SOL`.
- Median hold is only about `0.1m`, and average hold is about `0.5m`.
- Source-edge profiling shows median source order `1` and median source delay `0s`.

The catch: this edge does not look copyable with ordinary exits. The candle/path tests show that fixed TP/SL, trailing, and time-exit policies are negative in the measured sample. The only strongly positive strategies are source-exit-dependent, which means a copy bot would need to detect the source entry and source exit nearly immediately, with local execution and route support.

Current local telemetry did not capture this wallet at all in the audited window. Source coverage found `4,020` source swaps and `0` captured/detected rows in local telemetry/trade logs.

## Commands Run

Telemetry:

```powershell
npm run telemetry:execution *> data\bwam-audit\telemetry-execution.txt
npm run telemetry:scan-today *> data\bwam-audit\telemetry-scan-today.md
```

Source coverage:

```powershell
node scripts/auditSourceCoverage.mjs --wallet=bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa --label=bwam --since=2026-06-28T18:00:00Z --until=2026-07-01T17:00:00Z --max-txs=5000 --sleep-ms=1000 --out-json=data/bwam-audit/source-coverage.json --out-md=data/bwam-audit/source-coverage.md
```

Ledger and bucket audits:

```powershell
npx tsx src/walletLedgerAudit.ts bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa 5000 --cache
npx tsx src/walletLedgerBucketAudit.ts bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa 5000 wallet-ledger-buckets-bwam-5000
```

Quant and path audits:

```powershell
npx tsx src/walletQuantAudit.ts bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa 5000 160 > data\wallet-quant-audit-bwam-5000-160.json
$env:PATH_AUDIT_QUIET='true'; $env:PATH_AUDIT_SLEEP_MS='500'; npx tsx src/pathBasedWalletAudit.ts bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa 5000 120 24 40
```

Supporting reads:

```powershell
node scripts/analyzeWalletPnlWindows.mjs --wallet=bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa --cache-only --anchor=newest
npx tsx src/profileSourceEdge.ts bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa 300 20
npx tsx src/analyzeWalletPerformance.ts bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa 500
```

Note: a first `50,000` transaction ledger attempt hit Helius `429 Too Many Requests`, so the completed research uses the repo-standard `5,000` transaction pass.

## Live Telemetry Report

Global execution telemetry file: `data/execution-telemetry.jsonl`

Rows in current telemetry file: `2,439`

Success/failure:

- Success rows: `2,412` (`98.9%`)
- Failure rows: `27` (`1.1%`)

Overall latency:

- p50: `18ms`
- p90: `1061ms`
- p99: `1075ms`

Important stage latencies:

| Stage | n | p50 | p90 | p99 |
|---|---:|---:|---:|---:|
| `source-detected` | 13 | 281ms | 289ms | 291ms |
| `stage:build` | 89 | 5ms | 10ms | 14ms |
| `broadcast` | 43 | 25ms | 65ms | 99ms |
| `stage:confirm` | 40 | 1064ms | 1074ms | 1076ms |
| `stage:quote` | 14 | 185ms | 419ms | 433ms |

Feed latency:

| Segment | n | p50 | p90 | p99 |
|---|---:|---:|---:|---:|
| provider to bot received | 26 | 281ms | 290ms | 290ms |
| bot received to parsed | 30 | 0ms | 1ms | 1ms |
| parsed to process start | 30 | 0ms | 1ms | 1ms |
| provider to process start | 26 | 281ms | 291ms | 291ms |

End-to-end chains:

| Segment | n | p50 |
|---|---:|---:|
| provider to broadcast | 3 | 639ms |
| process to broadcast | 3 | 375ms |
| broadcast to confirm | 3 | 1076ms |
| provider to confirm | 3 | 1715ms |

Route mix in telemetry:

| Route | Count | Share |
|---|---:|---:|
| `pumpfun-bonding-curve` | 1,634 | 67.0% |
| `pumpfun-v2-token2022` | 450 | 18.5% |
| `jupiter-non-pump` | 90 | 3.7% |
| `pumpfun-direct-sell` | 81 | 3.3% |
| `pumpswap-graduated` | 51 | 2.1% |
| `pumpfun-direct-buy` | 38 | 1.6% |
| `pumpfun-direct-sell-v2` | 27 | 1.1% |
| `jupiter-sell` | 26 | 1.1% |
| `pumpfun-source-replay-buy` | 20 | 0.8% |

Failure classes:

| Failure Class | Count | Share |
|---|---:|---:|
| `unknown` | 10 | 37.0% |
| `not-tradable` | 10 | 37.0% |
| `slippage` | 3 | 11.1% |
| `account-layout` | 3 | 11.1% |
| `no-route` | 1 | 3.7% |

Interpretation:

- The local execution stack itself is fast where it owns the work: parsing and build are low single-digit to low double-digit milliseconds.
- Confirmation dominates p90/p99 because Solana confirmation is not software-owned latency.
- The current telemetry sample is not evidence that `bwam` is working as a source. It is evidence that the existing bot telemetry is healthy in general.

## Source Coverage For BWAM

Coverage command window: `2026-06-28T18:00:00Z` to `2026-07-01T17:00:00Z`

Summary:

| Metric | Value |
|---|---:|
| Source swaps found | 4,020 |
| Source buys | 1,022 |
| Source sells | 2,998 |
| Captured trades | 0 |
| Not captured | 4,020 |
| Source buys without matching bot buy | 1,022 |
| Detection-only rows | 0 |
| No telemetry/no trade rows | 4,020 |

Status breakdown:

| Status | Count | Share |
|---|---:|---:|
| `not-detected` | 4,020 | 100.0% |

Interpretation:

- This wallet was not being tracked or detected by the local telemetry/trade logs during the audited window.
- Any strategy recommendation here is historical source research, not a live-tested FinalBot source result.
- Before live-copying this wallet, the first requirement is a shadow-only tracking pass that proves source detection, route classification, and exit detection.

## Ledger Audit

Ledger artifact: `data/wallet-ledger-audit-bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa.md`

Coverage:

| Metric | Value |
|---|---:|
| Transactions fetched | 5,000 |
| Classified buy/sell token-flow txs | 4,017 |
| Unclassified token-flow txs | 125 |
| Positions touched | 1,025 |
| Time range | 2026-06-28 18:04:25Z to 2026-07-01 16:45:00Z |
| SOL/USD used | $77.49 |

PnL:

| Metric | Value |
|---|---:|
| Realized PnL | +2,201.0032 SOL |
| Realized USD estimate | +$170,556 |
| Realized ROI on sold cost basis | +30.9% |
| Realized trades | 2,982 |
| Win rate | 79.9% |
| Profit factor | 13.58 |
| Max realized-sequence drawdown | -30.7479 SOL |
| Average holding duration | 0.5m |
| Median holding duration | 0.1m |
| Largest win | +49.5910 SOL |
| Largest loss | -18.6913 SOL |

Open lot warning:

- The ledger pass marked a very large priced unrealized value from current DexScreener marks.
- Treat the `+32,529 SOL` unrealized figure as a current-mark diagnostic, not a reliable copyable edge or executable exit value.
- The realized ledger is the cleaner number for wallet quality.

## PnL Windows

PnL windows artifact: `data/wallet-pnl-windows-bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa.md`

Coverage:

- Transactions loaded: `5,005`
- Swaps extracted: `4,005`
- Realized costed sells: `2,994`
- Uncosted sells: `8`
- Oldest swap: `2026-06-28T18:04:25Z`
- Newest swap: `2026-07-01T16:53:43Z`
- Anchor: newest observed swap

| Window | Buys | Sells | Buy Mints | Deployed | Gross PnL | Net PnL | Net ROI | Win % | PF | Fees |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1d | 345 | 955 | 344 | 2,224.6503 SOL | +533.5499 SOL | +527.5882 SOL | +24.4% | 78.1% | 12.62 | 5.9619 SOL |
| 7d | 1,003 | 3,002 | 1,001 | 7,687.0118 SOL | +2,203.5712 SOL | +2,186.0339 SOL | +30.4% | 79.6% | 13.16 | 17.5565 SOL |
| 30d | 1,003 | 3,002 | 1,001 | 7,687.0118 SOL | +2,203.5712 SOL | +2,186.0339 SOL | +30.4% | 79.6% | 13.16 | 17.5565 SOL |

The 30d row is not a true 30d history; the cache only spans about 3 days.

## Entry Market-Cap Buckets

Bucket artifact: `data/wallet-ledger-buckets-bwam-5000.md`

Coverage:

- Transactions: `5,000`
- Reconstructed fills: `4,016`
- Campaigns: `1,014`
- Closed measured campaigns: `992`
- Pump rows: `1`
- BAGS rows: `5`
- Other rows: `986`

All-route bucket table:

| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median Hold |
|---|---:|---:|---:|---:|---:|---:|---:|
| $0-5k | 979 | 44.9% | +19.9% | -2.2% | 14.71 | +1,552.90 SOL | 0.13m |
| $5k-10k | 8 | 87.5% | +529.7% | +542.9% | 1204.24 | +643.83 SOL | 0.25m |
| $20k-50k | 4 | 75.0% | +25.1% | +4.8% | 22.39 | +5.71 SOL | 0.22m |
| $100k+ | 1 | 0.0% | -0.6% | -0.6% | 0.00 | -0.39 SOL | 0.15m |

Read:

- Most volume is ultra-low entry market cap, especially `$0-5k`.
- The `$5k-10k` bucket is very profitable but thin, with only `8` measured campaigns in this pass.
- The route mix is almost entirely `other`, not pump.fun/PumpSwap as classified by this bucket tool.

## Candle-Enriched Quant Audit

Quant artifact: `data/wallet-quant-audit-bwam-5000-160.json`

Sample:

| Metric | Value |
|---|---:|
| Transactions fetched | 5,000 |
| Swaps | 4,017 |
| Buys | 1,017 |
| Sells | 3,000 |
| Campaigns | 1,015 |
| Closed campaigns | 993 |
| Measured candle entries | 155 |
| Oldest tx | 2026-06-28T18:04:26Z |
| Newest tx | 2026-07-01T16:51:40Z |

Wallet performance from quant pass:

| Metric | Value |
|---|---:|
| Realized PnL | +2,176.7072 SOL |
| Win rate | 45.3% |
| Average ROI | +23.9% |
| Median ROI | -2.1% |
| Profit factor | 19.43 |
| Average hold | 0.46m |
| Median hold | 0.13m |
| Largest win | +169.0285 SOL |
| Largest loss | -25.8792 SOL |

Entry/path stats:

| Metric | Value |
|---|---:|
| Average entry size | 5.7941 SOL |
| Median entry size | 5.0114 SOL |
| Median pre-5m volume | $884 |
| p75 pre-5m volume | $1,662 |
| Median current liquidity | $3,185 |
| Median current market cap | $2,144 |
| Median MFE | 0.0% |
| Median MAE | -0.29% |

Target probabilities:

| Target | Probability | Avg Time |
|---|---:|---:|
| +10% | 6.45% | 226s |
| +20% | 5.16% | 263s |
| +30% | 4.52% | 296s |
| +50% | 2.58% | 422s |
| +100% | 1.94% | 561s |

Downside probabilities:

| Drawdown | Probability |
|---|---:|
| -10% | 14.84% |
| -20% | 9.03% |
| -30% | 8.39% |

Strategy simulation:

| Strategy | Trades | Win | Avg ROI | Median ROI | PF | Cumulative ROI | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sell when wallet sells | 155 | 46.5% | +20.8% | -0.6% | 17.17 | +3222.9pp | -18.0pp |
| 50% at +30, 50% source exit | 155 | 46.5% | +19.0% | -0.6% | 15.74 | +2937.6pp | -18.0pp |
| 20% trailing stop | 155 | 9.0% | -0.4% | 0.0% | 0.80 | -60.1pp | -176.2pp |
| TP30 SL20 | 155 | 6.5% | -2.2% | 0.0% | 0.22 | -334.8pp | -334.8pp |
| TP50 SL25 | 155 | 5.8% | -2.6% | 0.0% | 0.20 | -407.4pp | -407.4pp |
| Time exit 30m | 155 | 4.5% | -6.0% | 0.0% | 0.01 | -930.4pp | -930.4pp |

Read:

- The edge is in source behavior, not generic exits.
- The measured path has low ordinary target-hit probability.
- Copying without source-exit fidelity likely turns a strong wallet into a weak strategy.

## Path-Based Wallet Audit

Path artifact: `data/path-wallet-audit-bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa.md`

Coverage:

| Metric | Value |
|---|---:|
| Transactions fetched | 5,000 |
| Swaps | 4,019 |
| Campaigns | 1,015 |
| Measured entries | 68 |
| Horizon | 24h |
| SOL/USD used | $77.30 |

Wallet PnL:

| Metric | Value |
|---|---:|
| Realized PnL | +2,156.0466 SOL |
| Closed campaigns | 993 |
| Realized win rate | 45.2% |
| Average source exit | +31.2% |
| Median source exit | +9.7% |

Path stats:

| Metric | Value |
|---|---:|
| Hit +30% | 4/68 |
| Hit +50% | 4/68 |
| Hit +100% | 3/68 |
| Hit +200% | 0/68 |
| Median MFE | 0.0% |
| Median MAE | 0.0% |
| Median entry MC approx | $2,554 |
| Median time to 2x | 10m |

Path strategy backtest:

| Strategy | Trades | Avg ROI | Median ROI | Win Rate | PF | Cumulative ROI | Cumulative DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mirror source exit | 67 | +31.2% | +9.7% | 55.2% | 30.60 | +2089.8pp | -14.1pp |
| TP100 SL35 | 68 | -2.5% | 0.0% | 11.8% | 0.55 | -167.5pp | -167.5pp |
| Trail arm45 give30 SL35 | 68 | -2.8% | 0.0% | 11.8% | 0.49 | -190.6pp | -190.6pp |
| Trail arm65 give35 SL35 | 68 | -3.0% | 0.0% | 11.8% | 0.46 | -200.6pp | -200.6pp |
| TP50 SL30 | 68 | -3.4% | 0.0% | 11.8% | 0.31 | -234.0pp | -234.0pp |
| TP30 SL25 | 68 | -3.5% | 0.0% | 11.8% | 0.21 | -239.0pp | -239.0pp |
| Time exit 30m | 68 | -8.0% | 0.0% | 8.8% | 0.01 | -541.4pp | -541.4pp |

## Lead / Source-Edge Profile

Source-edge command profiled the 20 most recent mints.

Summary:

- Mints profiled: `20`
- Closed in sample: `19`
- Wins: `9/19`
- Average source delay from first mint tx: `0.0s`
- Average source buy order in first mint history: `0.9`
- Repeated creator: `bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa` appeared `17x`
- Repeated top early buyers appeared across the sample, but `bwam` itself is the dominant repeated creator/source.

Interpretation:

- This wallet frequently appears to be first or effectively first.
- Several entries show `bwam` as the creator.
- If the edge is creator-side or launch-side, ordinary copy trading will usually arrive after the source's informational advantage has already happened.

## Copyability Assessment

Copyability: poor unless source-exit detection and local routing are proven.

Reasons:

- Median hold is measured in seconds.
- Median source delay is `0s`; source order is usually first.
- Generic TP/SL and time exits are negative in candle/path tests.
- Target-hit probabilities are low for a follower entering after the source.
- Source coverage says local telemetry did not detect this wallet at all in the audited window.
- Route classification is mostly `other`, so live execution support must be proven before any real copy test.

What would need to be true before live testing:

- Add the wallet to shadow tracking first.
- Prove `source-detected` telemetry rows for entries and exits.
- Prove route classification without Jupiter fallback.
- Prove local buy/sell execution path support for the actual route family.
- Prove same-slot or next-slot landing in telemetry.
- Keep `RUST_ONLY_BUYS=true`, `RUST_ONLY_SELLS=true`, and `JUPITER_FALLBACK_ENABLED=false`.

## Recommended Posture

Do not copy this wallet live yet.

Recommended sequence:

1. Run shadow-only tracking for `bwam`.
2. Confirm source detection and exit detection in `data/execution-telemetry.jsonl`.
3. Run source coverage again after shadow tracking.
4. Classify source tx protocols for missed route rows if any.
5. Only then test tiny sizing, and only if the route is local/Rust-supported.

Suggested tiny-test rule if shadow telemetry proves capture:

- Entry: source buy only.
- Exit: source sell only at first.
- No fixed TP/SL as primary exit from these audits.
- Use a hard emergency stop only as circuit protection.
- Avoid this source entirely if source exits cannot be detected quickly.

## Caveats

- The completed research is a `5,000` transaction pass because the attempted `50,000` transaction ledger pull hit Helius `429`.
- Candle/path sample sizes are smaller than ledger samples because OHLCV/pair coverage is not available for every token.
- Unrealized mark-to-market values are current DexScreener-style marks and can be misleading on illiquid tokens.
- This was not live-tested on the VPS.
- No hot-path code was changed.
- No Jupiter fallback was added or used as a recommendation.

