# Omego Copy-Trading Strategy Handoff — 2026-06-21

This document preserves the investigation and strategy discussion so it can be continued in another assistant/session without relying on chat history.

## Wallets and scope

- Tracked/source wallet (Omego): `omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq`
- Our execution wallet: `GaYjpVLZ1ViQy9fK2PFVJu9t9jXLUF8YEJ71cNRTaXNo`
- Main supplied bot log: Codex attachment `8f818801-e594-433f-80b0-1a3f189a5041/pasted-text.txt`
- Candle audit: `data/path-wallet-audit-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq.json`
- Candle window: source entry through up to 14 hours later, ending at the available June 21 data.
- Candle coverage: 189 of 207 source token campaigns had usable OHLCV data.

## Padre fee investigation

The Padre calendar screenshot showed 710 buys, 612 sells, $5.46K buy volume, $5.37K sell volume, $124.22 protocol fees, and $8.27 transaction fees.

An on-chain fee decode matched the displayed protocol-fee total:

- Pump protocol fees: 1.405505155 SOL
- Pump creator fees: 0.309887818 SOL
- PumpSwap LP fees: 0.002037397 SOL
- PumpSwap protocol fees: 0.006698543 SOL
- PumpSwap creator fees: 0.008301177 SOL
- Total DEX/protocol fees: 1.732430090 SOL
- At Padre's implied SOL price of about $71.70, this equals approximately $124.22.

These are real DEX fees, separate from Solana transaction fees/tips. They are charged by the Pump.fun/PumpSwap trading protocols and creator/LP fee mechanisms. Using a 15% copy size does not create a special surcharge; percentage protocol fees scale approximately with traded notional. The damaging variable is total turnover: repeated buys and sells repeatedly pay the percentage fee.

## What today's supplied log says about re-entry

The supplied log contains 88 parseable SOL buy records across only 25 distinct mints:

- Buy events: 88
- Unique mints: 25
- Repeat/re-entry buys: 63
- Total parsed buy turnover: 9.8151 SOL
- Turnover from first buy per mint: 2.4682 SOL
- Turnover from repeat buys: 7.3469 SOL (about 74.9% of parsed buy turnover)

Most repeated names included:

- IMY: 11 buys, 1.2015 SOL
- PEEL: 9 buys, 1.4695 SOL
- NGMI: 9 buys, 0.8699 SOL
- AMONG: 8 buys, 0.8274 SOL
- ACCELERATE: 7 buys, 1.1848 SOL
- SimVerse: 6 buys, 0.8433 SOL
- SCREAMING: 4 attempted/recorded buys; several became phantom buys and were cleaned up

This is the clearest operational problem. Re-entry created roughly three quarters of parsed buy turnover, plus corresponding sell turnover, protocol fees, transaction fees, tips, slippage, and more time exposed to the same token.

### Approximate buy-once replay from the supplied log

A simple reconstruction kept only the first confirmed-looking buy per mint and allocated the first subsequent full-position sell proportionally to that first lot. It found 21 closed first positions:

- First-lot cost: 2.1939 SOL
- Trade-basis result: approximately +0.0672 SOL
- Return on deployed first-lot capital: approximately +3.06%
- Winners/losers: 11 / 10

This is approximate and excludes wallet-delta fees, missing/unclosed positions, and any records that did not parse cleanly. It is nevertheless useful evidence that buy-once would have radically reduced turnover without obviously destroying the day's first-entry edge.

## ATH after entry investigation

The requested definition was: maximum price reached at any time after entry, including after Omego sold.

Across 189 measured source-token paths:

- Reached +30% after entry: 84 (44.4%)
- Reached +50%: 61 (32.3%)
- Reached +80%: 41 (21.7%)
- Reached +100%: 30 (15.9%)
- Reached +200%: 7 (3.7%)
- Median maximum favorable excursion: +21.4%
- Median time to 2x among tokens that reached it: about 30 minutes

Examples where later ATH greatly exceeded Omego's aggregate exit:

- JINS: +585.3% ATH; Omego exit +29.3%
- KWM: +336.4%; exit -3.7%
- DCT: +232.5%; exit -3.4%
- Martha: +174.5%; exit -12.8%
- SimVerse: +171.8%; exit -3.1%
- MINORI: +160.7%; exit +2.2%
- CROFT: +158.8%; exit -3.3%

The observation is valid: some tokens ran substantially after Omego exited.

## Critical correction to the first TP calculation

An earlier quick calculation reported that TP70–TP85 looked strongly profitable. That calculation asked whether a token ever reached the TP during the entire post-entry candle window, but used Omego's exit as the fallback when it did not. This is not an executable policy when the TP occurs after Omego's exit: with `MIRROR_SELLS=true`, our position would already be closed.

Therefore those positive projected SOL totals must not be treated as a valid backtest.

There are three distinct executable policies:

1. **Mirror-first:** take a TP only if it occurs before Omego's sell; otherwise mirror his sell.
2. **Independent exit:** ignore Omego's sell and wait for TP/trailing/stop/end-of-window.
3. **Runner:** sell part when Omego exits and retain a smaller remainder for a later TP/trailing exit.

Policy 1 needs exact source-sell timestamps joined to each candle path. Policy 3 needs a runner feature; the current `HYBRID_EXIT_*` implementation does the opposite (takes a partial TP first, then source-exits the remainder).

## Quick independent-exit simulations

These simulations ignore Omego's exit and use the full post-entry candle path. They are gross candle-price results before our exact fees, slippage, tips, and quote latency. They use one consolidated row per mint, so they do not model individual re-entry cycles.

| Policy | Average return | Median return | Win rate |
|---|---:|---:|---:|
| Mirror Omego aggregate exit (reference) | -2.60% | -2.61% | 39.2% |
| Hold to end of available horizon | -35.51% | -45.15% | 13.2% |
| TP50, otherwise horizon | -13.30% | -21.60% | 36.5% |
| TP70, otherwise horizon | -14.04% | -31.42% | 30.2% |
| TP80, otherwise horizon | -16.23% | -33.54% | 27.0% |
| TP85, otherwise horizon | -15.57% | -33.54% | 27.0% |
| TP100, otherwise horizon | -19.98% | -36.35% | 22.8% |
| TP50 / emergency SL55 | -12.27% | -31.18% | 33.3% |
| TP80 / emergency SL55 | -14.01% | -36.15% | 24.9% |
| Trail armed +35%, 20-point giveback, SL55 | -7.72% | -18.33% | 39.7% |
| Trail armed +50%, 25-point giveback, SL55 | -11.58% | -31.42% | 32.8% |
| Trail armed +70%, 30-point giveback, SL55 | -13.01% | -35.17% | 26.5% |

Conclusion: the large later winners exist, but the majority of tokens decay badly. Simply ignoring Omego and waiting for +80% is worse. The later runners do not compensate for holding every failed token. A runner must be only a fraction of the position, with a defined stop/time exit.

These figures also have one-minute candle ordering ambiguity: when both a stop and a high occur inside the same candle, the actual event order is unknown. Treat close comparisons as inconclusive.

## Current local `.env` observations (no secrets copied)

At the time of this handoff, the local `.env` contains:

- `COPY_BUY_PERCENTAGE=100`
- `MAX_BUY_SOL=0.3`
- `BUY_ONCE_PER_TOKEN=true`
- `MIRROR_SELLS=false`
- `STOP_LOSS_PCT=55`
- `TRAILING_STEPS=0:8,35:15,60:30,100:60,200:130,350:240,500:360,800:620,1200:950`
- `MIN_HOLD_BEFORE_STOP_LOSS_MS=25000`

This does **not** resemble the 15% mirrored sizing shown in the supplied trading log. Confirm the VPS/live environment before restarting. At 100%, buys can be much larger than the tested session, capped at 0.3 SOL.

The first trailing step `0:8` means the effective stop becomes +8% immediately from entry. Once the 25-second minimum hold elapses, any quoted PnL at or below +8% can trigger an exit. This is unusual and may behave like a forced ~25-second exit for positions that have not already exceeded +8%. It should not be used accidentally.

## Recommended next live-test posture

Do not jump directly to an independent TP80 strategy. The evidence currently supports reducing turnover first:

```dotenv
BUY_ONCE_PER_TOKEN=true
MAX_BUYS_PER_TOKEN=1
COPY_BUY_PERCENTAGE=10
MAX_BUY_SOL=0.10
MIRROR_SELLS=true
TAKE_PROFIT_PCT=
TRAILING_STEPS=
```

Notes:

- Ten percent and a 0.10 SOL cap are a conservative live-test posture, not a proven optimum.
- `BUY_ONCE_PER_TOKEN=true` is the most evidence-backed change from the supplied day.
- Lowering size reduces absolute SOL fees and exposure, but does not erase percentage protocol fees. The strategy must still have edge after roughly proportional DEX fees.
- Mirror exits remain the safest baseline until the exact **TP-before-source-exit** replay is completed.
- Do not combine `MIRROR_SELLS=true` with an expectation of capturing post-source-exit ATH on the full position.

### Candidate runner experiment (requires implementation or manual support)

If post-exit upside is to be tested, start small:

- Sell 75–90% when Omego exits.
- Retain only 10–25% as a runner.
- Runner TP: initially +80% from our entry.
- Runner protection: time exit (for example 30–60 minutes) and/or a trailing floor after +35–50%.
- Measure results separately from the mirrored core.

This is intentionally small because only 21.7% of measured tokens reached +80%, while the median token's end-of-horizon result was deeply negative.

## Source-hold scanner and 429 errors

The source-hold scanner is not free/local-only. It reads source token accounts through RPC and the supplied log shows extensive HTTP 429 rate limiting. Making it poll faster on the same provider will increase contention and can delay other work.

Safer options are:

- Prefer event-driven gRPC/ShredStream sell detection for the hot path.
- Use source-hold polling only as a slower reconciliation/failsafe.
- Batch/deduplicate account reads and apply backoff/jitter.
- Use a separate RPC quota/endpoint for reconciliation if faster polling is required.

## Required next analysis for a defensible final strategy

1. Add `firstSellTs`/source-exit timestamps to the path-audit output.
2. Replay fixed TP and trailing triggers only up to the source sell for the mirror-compatible strategy.
3. Replay each actual execution-wallet entry, not only one consolidated source campaign per mint.
4. Deduct exact protocol/creator/LP fees, transaction fees, tips, and wallet-delta slippage.
5. Compare `BUY_ONCE_PER_TOKEN=true`, `MAX_BUYS_PER_TOKEN=2`, and unlimited re-entry.
6. Measure peak concurrent SOL exposure and drawdown, not only summed returns.
7. Evaluate a 90/10, 80/20, and 75/25 mirrored-core/runner split.
8. Use out-of-sample sessions before selecting TP/trailing parameters; today's best parameter can easily be overfit.

## Bottom line

- Yes, today's Omego strategy needs readjustment.
- The strongest diagnosed issue is excessive repeated turnover, not proof that the initial 15% sizing alone was too large.
- Buy-once should be the first controlled change.
- Current local sizing appears to be 100%, so live/VPS configuration must be checked before another run.
- Full-position independent TP80 is not supported by the corrected simulation.
- A small post-source-exit runner is plausible, but it needs proper implementation and a fee-aware replay before risking meaningful size.

