# Strategy Cheat Sheet

This bot's strongest current edge is early detection plus fast execution. Treat these configs as playbooks, not promises. Memecoin liquidity can disappear faster than any bot can react.

## Sources Used

- Scalping references emphasize quick entries/exits, tight loss control, and small per-trade risk: https://www.tmgm.com/eng/academy/trading-academy/what-is-scalping
- Trailing-stop references frame trailing stops as a way to lock profit while letting winners run: https://journalplus.co/learn/glossary/trailing-stop/
- Position-sizing references emphasize capping risk per trade and avoiding oversized exposure: https://backtestme.com/guides/position-sizing-risk-management
- Kelly-style sizing is useful as a concept, but full Kelly is usually too aggressive for noisy live trading: https://www.tradicted.com/learn/kelly-criterion/

## Current Bot Reality

These are important before choosing a setup:

- `BUY_ONCE_PER_TOKEN=true` means one entry per mint.
- `BUY_ONCE_PER_TOKEN=false` allows ladder/add-on buys, but must be paired with hard caps.
- `MIRROR_SELLS=true` currently treats a source sell as an exit signal. Use it as a defensive full-exit mode unless proportional source sells are implemented later.
- `FAST_MODE=true` records optimistic positions after broadcast. If the tx never lands, the bot cleans the phantom position later.
- `MAX_BUY_SOL` is the most important live safety knob today.
- `SOL_RESERVE` protects your wallet from being fully deployed.

## Strategy 1: Fast Scalp, Single Entry

Best for: your current bot, especially when tracking one high-signal wallet.

Idea:
- Enter once.
- Let risk exits and source sells get you out.
- Avoid repeated adds.

```env
BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true

MIN_BUY_SOL=0.01
MAX_BUY_SOL=0.08
MAX_OPEN_POSITIONS=2
SOL_RESERVE=3.5

TAKE_PROFIT_PCT=60
STOP_LOSS_PCT=25
MIN_HOLD_BEFORE_STOP_LOSS_MS=2500
TRAILING_STEPS=40:10,60:25,90:50
```

Use when:
- You want cleaner behavior.
- You do not want one token to absorb too much SOL.
- You are fine missing extra add-on buys.

Risk:
- If `MAX_BUY_SOL` is too low, winners may feel too small.

## Strategy 2: 100% Minimum Runner

Best for: you only want big wins and can tolerate more round trips.

Idea:
- Do not fixed-sell at +100%.
- Instead, activate trailing only after +100%.
- If it runs to +150%, +200%, +300%, the locked profit rises.

```env
BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true

MIN_BUY_SOL=0.01
MAX_BUY_SOL=0.08
MAX_OPEN_POSITIONS=2
SOL_RESERVE=3.5

TAKE_PROFIT_PCT=
STOP_LOSS_PCT=30
MIN_HOLD_BEFORE_STOP_LOSS_MS=2500
TRAILING_STEPS=100:60,150:100,200:140,300:220
```

Use when:
- You want to avoid selling at +40% to +70%.
- You are intentionally hunting runners.

Risk:
- Many trades that touch +40% to +80% may round trip or stop out.

## Strategy 3: Conservative Overnight / Unattended

Best for: leaving the bot running while you are not watching closely.

```env
BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true

MIN_BUY_SOL=0.005
MAX_BUY_SOL=0.04
MAX_OPEN_POSITIONS=2
SOL_RESERVE=3.7

TAKE_PROFIT_PCT=50
STOP_LOSS_PCT=25
MIN_HOLD_BEFORE_STOP_LOSS_MS=2500
TRAILING_STEPS=35:5,50:20,75:40
```

Use when:
- You want the bot alive but not swinging hard.
- You are testing new code changes.

Risk:
- Smaller winners.

## Strategy 4: Controlled Ladder-In

Best for: following wallets that buy in waves.

Current warning:
- This should ideally wait for dynamic sizing knobs.
- Without per-token exposure caps, laddering can drain SOL.

If using current bot only:

```env
BUY_ONCE_PER_TOKEN=false
MIRROR_SELLS=true

MIN_BUY_SOL=0.005
MAX_BUY_SOL=0.03
MAX_OPEN_POSITIONS=3
SOL_RESERVE=3.5

TAKE_PROFIT_PCT=60
STOP_LOSS_PCT=25
MIN_HOLD_BEFORE_STOP_LOSS_MS=2500
TRAILING_STEPS=40:10,60:25,90:50
```

Use when:
- Source wallet often buys the same mint multiple times before a move.
- You accept that repeated entries increase execution risk.

Risk:
- No true `MAX_TOKEN_EXPOSURE_SOL` yet, unless implemented later.

## Strategy 5: Dynamic Laddering, Recommended Future Upgrade

These knobs are recommended but may need implementation if not already present:

```env
BUY_ONCE_PER_TOKEN=false
MIRROR_SELLS=true

MIRROR_BUY_SCALE=0.35
MIN_BUY_SOL=0.005
MIN_BUY_BEHAVIOR=skip
MAX_BUY_SOL=0.05
MAX_BUY_BALANCE_PCT=1.5
MAX_TOKEN_EXPOSURE_SOL=0.15
MAX_SESSION_DEPLOYED_SOL=0.75
MAX_OPEN_POSITIONS=3
SOL_RESERVE=3.5

TAKE_PROFIT_PCT=60
STOP_LOSS_PCT=25
MIN_HOLD_BEFORE_STOP_LOSS_MS=2500
TRAILING_STEPS=40:10,60:25,90:50
```

Sizing formula:

```text
buySize = min(
  sourceBuySol * MIRROR_BUY_SCALE,
  MAX_BUY_SOL,
  availableSol * MAX_BUY_BALANCE_PCT / 100,
  MAX_TOKEN_EXPOSURE_SOL - currentTokenCost
)
```

Dust behavior:

```text
if buySize < MIN_BUY_SOL and MIN_BUY_BEHAVIOR=skip:
  skip
```

This is the clean version of laddering.

## Strategy 6: Source-Sell Full Exit

Best for: avoiding being left holding after the wallet starts exiting.

```env
BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true

MIN_BUY_SOL=0.01
MAX_BUY_SOL=0.08
MAX_OPEN_POSITIONS=2
SOL_RESERVE=3.5

TAKE_PROFIT_PCT=
STOP_LOSS_PCT=25
TRAILING_STEPS=80:40,120:80,180:120
```

Interpretation:
- One entry.
- If source sells, you exit.
- If source does not sell and it runs, trailing exits handle it.

This is probably the most natural fit for your current thesis.

## Wallet-Specific Starting Point

For the wallet that looked best in recent live logs:

```env
TRACKED_WALLETS=4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk
BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true
MAX_BUY_SOL=0.08
MAX_OPEN_POSITIONS=2
SOL_RESERVE=3.5
TAKE_PROFIT_PCT=60
STOP_LOSS_PCT=25
TRAILING_STEPS=40:10,60:25,90:50
```

More aggressive:

```env
MAX_BUY_SOL=0.12
MAX_OPEN_POSITIONS=2
TAKE_PROFIT_PCT=
TRAILING_STEPS=100:60,150:100,200:140,300:220
```

## Decision Guide

Choose `Fast Scalp, Single Entry` if:
- You want smoother live operation.
- You are still validating fixes.
- You want fewer phantom or oversized exposure surprises.

Choose `100% Minimum Runner` if:
- You only care about bigger wins.
- You accept giving back smaller pumps.

Choose `Controlled Ladder-In` if:
- You actively watch the bot.
- You keep `MAX_BUY_SOL` low.

Choose `Dynamic Laddering` once implemented if:
- You want to mirror source laddering without draining SOL.

## My Current Preference

For your next live session:

```env
BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=true
MAX_BUY_SOL=0.08
MAX_OPEN_POSITIONS=2
SOL_RESERVE=3.5
TAKE_PROFIT_PCT=60
STOP_LOSS_PCT=25
TRAILING_STEPS=40:10,60:25,90:50
```

If the next session is stable, test the runner version:

```env
TAKE_PROFIT_PCT=
TRAILING_STEPS=100:60,150:100,200:140,300:220
```

