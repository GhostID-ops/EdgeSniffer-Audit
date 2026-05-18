# Dynamic Sizing Scenarios

Dynamic sizing is meant for source wallets that buy in waves. The goal is to follow the wallet without letting one token or one session drain too much SOL.

Important: these knobs are a strategy spec unless implemented in code:

```env
MIRROR_BUY_SCALE=1.0
MAX_BUY_BALANCE_PCT=1.0
MAX_TOKEN_EXPOSURE_SOL=0.16
MAX_SESSION_DEPLOYED_SOL=0.75
MIN_BUY_BEHAVIOR=skip
```

## Sizing Formula

```text
targetBuy = sourceBuySol * MIRROR_BUY_SCALE

buySize = min(
  targetBuy,
  MAX_BUY_SOL,
  availableSol * MAX_BUY_BALANCE_PCT / 100,
  MAX_TOKEN_EXPOSURE_SOL - currentTokenExposure,
  MAX_SESSION_DEPLOYED_SOL - currentSessionDeployed
)
```

Then:

```text
if buySize < MIN_BUY_SOL:
  if MIN_BUY_BEHAVIOR=skip:
    skip
  else:
    raise to MIN_BUY_SOL
```

## Scenario 1: One Clean Entry

```env
BUY_ONCE_PER_TOKEN=true
MIRROR_BUY_SCALE=1.0
MIN_BUY_SOL=0.03
MAX_BUY_SOL=0.08
MAX_BUY_BALANCE_PCT=10
MAX_TOKEN_EXPOSURE_SOL=0.16
MAX_SESSION_DEPLOYED_SOL=0.75
MIN_BUY_BEHAVIOR=skip
```

Wallet balance: `1.00 SOL`

Source buy:

```text
0.07 SOL
```

Calculation:

```text
targetBuy = 0.07 * 1.0 = 0.07
balance cap = 1.00 * 10% = 0.10
token cap remaining = 0.16
session cap remaining = 0.75

buySize = min(0.07, 0.08, 0.10, 0.16, 0.75)
        = 0.07 SOL
```

Result:

```text
Bot buys 0.07 SOL.
Further buys for the same token are skipped because BUY_ONCE_PER_TOKEN=true.
```

## Scenario 2: Controlled Ladder

```env
BUY_ONCE_PER_TOKEN=false
MIRROR_BUY_SCALE=1.0
MIN_BUY_SOL=0.03
MAX_BUY_SOL=0.08
MAX_BUY_BALANCE_PCT=10
MAX_TOKEN_EXPOSURE_SOL=0.16
MAX_SESSION_DEPLOYED_SOL=0.75
MIN_BUY_BEHAVIOR=skip
```

Wallet balance: `1.00 SOL`

Source buys:

```text
Buy 1: 0.07 SOL
Buy 2: 0.06 SOL
Buy 3: 0.20 SOL
```

Calculation:

```text
Buy 1:
min(0.07, 0.08, 0.10, 0.16, 0.75) = 0.07
token exposure now 0.07

Buy 2:
token cap remaining = 0.16 - 0.07 = 0.09
min(0.06, 0.08, 0.10, 0.09, 0.68) = 0.06
token exposure now 0.13

Buy 3:
token cap remaining = 0.16 - 0.13 = 0.03
min(0.20, 0.08, 0.10, 0.03, 0.62) = 0.03
```

Result:

```text
Bot buys 0.07, then 0.06, then 0.03.
Total exposure is capped at 0.16 SOL.
```

## Scenario 3: Skip Dust

```env
BUY_ONCE_PER_TOKEN=false
MIRROR_BUY_SCALE=0.5
MIN_BUY_SOL=0.03
MAX_BUY_SOL=0.08
MAX_TOKEN_EXPOSURE_SOL=0.16
MIN_BUY_BEHAVIOR=skip
```

Source buy:

```text
0.04 SOL
```

Calculation:

```text
targetBuy = 0.04 * 0.5 = 0.02
```

Result:

```text
Bot skips because 0.02 < MIN_BUY_SOL.
```

This prevents tiny, fee-inefficient buys.

## Scenario 4: Balance Protection

```env
MIRROR_BUY_SCALE=1.0
MIN_BUY_SOL=0.03
MAX_BUY_SOL=0.35
MAX_BUY_BALANCE_PCT=5
MAX_TOKEN_EXPOSURE_SOL=0.25
MIN_BUY_BEHAVIOR=skip
```

Available balance: `0.80 SOL`

Source buy:

```text
1.20 SOL
```

Calculation:

```text
targetBuy = 1.20
max buy cap = 0.35
balance cap = 0.80 * 5% = 0.04
token cap remaining = 0.25

buySize = min(1.20, 0.35, 0.04, 0.25)
        = 0.04 SOL
```

Result:

```text
Bot buys 0.04 SOL instead of chasing the full source size.
```

## Scenario 5: Session Brake

```env
BUY_ONCE_PER_TOKEN=false
MIRROR_BUY_SCALE=1.0
MIN_BUY_SOL=0.03
MAX_BUY_SOL=0.08
MAX_SESSION_DEPLOYED_SOL=0.30
MIN_BUY_BEHAVIOR=skip
```

Current session deployed:

```text
0.27 SOL
```

Source buy:

```text
0.08 SOL
```

Calculation:

```text
session cap remaining = 0.30 - 0.27 = 0.03
buySize = min(0.08, 0.08, 0.03) = 0.03
```

Result:

```text
Bot buys 0.03 SOL.
Session deployed reaches 0.30 SOL.
Further buys are skipped until restart/reset.
```

## Scenario 6: Aggressive First-Buy Sniper

Use when you mostly care about catching the first buy and do not want laddering.

```env
BUY_ONCE_PER_TOKEN=true
MIRROR_BUY_SCALE=1.0
MIN_BUY_SOL=0.05
MAX_BUY_SOL=0.15
MAX_BUY_BALANCE_PCT=15
MAX_TOKEN_EXPOSURE_SOL=0.15
MAX_SESSION_DEPLOYED_SOL=0.75
MIN_BUY_BEHAVIOR=skip
```

Behavior:

```text
One entry per token.
Large source buys are capped.
Tiny source buys below useful size are skipped.
```

## Scenario 7: Safer Multi-Buy Ladder

Use when the source wallet buys the same token 2-5 times before the move.

```env
BUY_ONCE_PER_TOKEN=false
MIRROR_BUY_SCALE=0.5
MIN_BUY_SOL=0.025
MAX_BUY_SOL=0.06
MAX_BUY_BALANCE_PCT=7.5
MAX_TOKEN_EXPOSURE_SOL=0.15
MAX_SESSION_DEPLOYED_SOL=0.60
MIN_BUY_BEHAVIOR=skip
```

Behavior:

```text
Copies half the source size.
Allows multiple entries.
Caps each token at 0.15 SOL.
Caps the session at 0.60 SOL.
```

## Practical Notes

- `BUY_ONCE_PER_TOKEN=true` makes `MAX_TOKEN_EXPOSURE_SOL` less important because only one buy can happen.
- `BUY_ONCE_PER_TOKEN=false` should almost always be paired with `MAX_TOKEN_EXPOSURE_SOL`.
- `MIN_BUY_BEHAVIOR=skip` is safer than raising tiny buys to your minimum.
- `MAX_SESSION_DEPLOYED_SOL` is a useful emergency brake for unattended sessions.
- For fast pump.fun wallets, smaller buys often land more reliably than oversized buys.
