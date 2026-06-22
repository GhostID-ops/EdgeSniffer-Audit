# Omego A/B — Max Buys Per Cycle (follower simulation)

Generated: 2026-06-22T16:21:41.126Z
Source tape: data/wallet-ledger-cache/wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-10000.json
Extracted swaps: 9933 · Campaigns (cycles): 4314

Follower model: proportional mirror-sell, copy=20%, drag = fee 1.0%/leg + slippage 0.0%/leg + tip 0.0000 SOL/leg. Realized-only (cycles Omego left open in the cache are skipped).

| Strategy (max buys/cycle) | Cycles | Buys | Sells | Deployed SOL | Trade-basis PnL | Net PnL (after drag) | Fee | Slip | Tip | Total drag | Win % | Med ROI | Net/cycle |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Cap 1 | 4229 | 4229 | 4279 | 629.70 | +12.2962 | -0.4208 | 12.717 | 0.000 | 0.000 | 12.717 | 37.6% | -3.3% | -0.0001 |
| Cap 2 | 4229 | 4258 | 4279 | 634.42 | +12.2384 | -0.5723 | 12.811 | 0.000 | 0.000 | 12.811 | 37.6% | -3.3% | -0.0001 |
| Cap 3 | 4229 | 4264 | 4279 | 635.48 | +12.1385 | -0.6924 | 12.831 | 0.000 | 0.000 | 12.831 | 37.6% | -3.3% | -0.0002 |
| Cap 4 | 4229 | 4268 | 4279 | 636.18 | +12.1466 | -0.6985 | 12.845 | 0.000 | 0.000 | 12.845 | 37.6% | -3.3% | -0.0002 |
| Mirror-all (∞) | 4229 | 4278 | 4279 | 637.84 | +11.9457 | -0.9306 | 12.876 | 0.000 | 0.000 | 12.876 | 37.6% | -3.3% | -0.0002 |

## How to read
- **Trade-basis PnL** = what the bot UI would show (his price replay). **Net PnL** = after your real fee/slip/tip drag — the wallet number.
- A higher cap captures more of his scalps (higher trade-basis) but stacks more drag. The winner is the cap where net, not trade-basis, peaks.
- "Mirror-all (∞)" is today's behavior. "Cap 1" is buy-once.
- Drag assumptions are tunable: re-run with --slip / --fee / --tip from your real fills to harden the result.
