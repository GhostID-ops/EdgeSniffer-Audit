# FinalBot Beginner Guide

This guide is for using the bot after it is already installed.

## 1. Open Windsurf

1. Open Windsurf.
2. Open the bot folder:

```text
/Users/patricia/Desktop/IM RICH/TradingBot
```

3. Open a new terminal in Windsurf.

## 2. Start The Bot Dashboard

Copy and paste this into the terminal:

```bash
cd "/Users/patricia/Desktop/IM RICH/TradingBot"
npm run web
```

Then open this in the browser:

```text
http://localhost:3000
```

Keep the terminal open. If you close the terminal, the bot stops.

## 3. Stop The Bot

Click the terminal and press:

```text
Control + C
```

That stops the bot.

## 4. The `.env` File

The `.env` file is the bot's settings.

Think of it like the bot's control panel.

Do not share the `.env` file with anyone. It can contain the wallet private key.

## 5. Simulation Or Real Money

```env
SIMULATION_MODE=true
```

This means pretend trading. Good for testing.

```env
SIMULATION_MODE=false
```

This means real trading. Real SOL can move.

Beginner rule:

```env
SIMULATION_MODE=true
```

until you are sure everything works.

## 6. Wallet Key

```env
PRIVATE_KEY_BASE58=
```

This is the wallet the bot uses.

Like explaining it simply:

- The wallet is the bot's money pocket.
- The private key is the key to that pocket.
- Anyone with the private key can use that wallet.

Use a separate trading wallet. Do not use your main wallet.

## 7. Wallets The Bot Copies

```env
TRACKED_WALLETS=
```

This is who the bot watches.

One wallet:

```env
TRACKED_WALLETS=WalletAddressHere
```

Multiple wallets:

```env
TRACKED_WALLETS=WalletOne,WalletTwo,WalletThree
```

Simple meaning:

The bot watches these wallets. When they buy, the bot can copy.

## 8. Min And Max Buy

```env
MIN_BUY_SOL=0.02
MAX_BUY_SOL=0.05
```

These control how much SOL the bot spends per buy.

Simple meaning:

- `MIN_BUY_SOL` is the smallest buy.
- `MAX_BUY_SOL` is the biggest buy.

Example:

```env
MIN_BUY_SOL=0.02
MAX_BUY_SOL=0.05
```

The bot will try to buy between `0.02 SOL` and `0.05 SOL`.

Start small.

## 9. SOL Reserve

```env
SOL_RESERVE=0.1
```

This is SOL the bot tries not to spend.

Simple meaning:

The bot keeps this much SOL aside for fees.

Example:

If the wallet has `1 SOL` and:

```env
SOL_RESERVE=0.1
```

the bot tries to leave `0.1 SOL` alone.

## 10. Max Open Positions

```env
MAX_OPEN_POSITIONS=2
```

This is how many tokens the bot can hold at once.

Simple meaning:

If this is `2`, the bot can hold 2 open trades. After that, it waits.

Beginner setting:

```env
MAX_OPEN_POSITIONS=2
```

## 11. Buy Once Per Token

```env
BUY_ONCE_PER_TOKEN=true
```

Simple meaning:

If the bot already bought a token, it will not buy the same token again during that session.

Beginner setting:

```env
BUY_ONCE_PER_TOKEN=true
```

## 12. Mirror Sells

```env
MIRROR_SELLS=false
```

Simple meaning:

- `true` means the bot tries to sell when the copied wallet sells.
- `false` means the bot uses your stop loss, take profit, and trailing stop settings instead.

Beginner setting:

```env
MIRROR_SELLS=false
```

## 13. Stop Loss

```env
STOP_LOSS_PCT=25
```

Simple meaning:

If the trade is down about `25%`, the bot tries to sell.

Smaller number = exits faster.

Bigger number = gives the trade more room, but can lose more.

## 14. Take Profit

```env
TAKE_PROFIT_PCT=100
```

Simple meaning:

If the trade is up about `100%`, the bot tries to sell.

`100%` means roughly double.

## 15. Trailing Stop

```env
TRAILING_STEPS=25:-5,35:10,50:30,70:45,90:65
```

Simple meaning:

The bot protects more profit as the trade goes up.

Read it like this:

```text
50:30
```

means:

If the trade reaches `+50%`, the bot tries to protect about `+30%`.

Good beginner runner setup:

```env
STOP_LOSS_PCT=25
TAKE_PROFIT_PCT=100
TRAILING_STEPS=25:-5,35:10,50:30,70:45,90:65
MIN_HOLD_BEFORE_STOP_LOSS_MS=3000
```

## 16. Slippage

```env
MIRROR_SLIPPAGE_BPS=1200
MIRROR_RETRY_SLIPPAGE_BPS=2000
```

Simple meaning:

Slippage is how much worse the price is allowed to get while the bot is trying to trade.

Numbers:

- `100` = 1%
- `500` = 5%
- `1200` = 12%
- `2000` = 20%

Beginner setting:

```env
MIRROR_SLIPPAGE_BPS=1200
MIRROR_RETRY_SLIPPAGE_BPS=2000
```

## 17. Fast Mode

```env
FAST_MODE=true
```

Simple meaning:

The bot moves faster.

Sometimes a trade can be sent, then fail after. If that happens, the bot may show a cleanup message.

If you want slower but clearer behavior:

```env
FAST_MODE=false
```

## 18. License Checks

If license checks are disabled, make sure this line exists:

```env
LICENSE_DISABLE_CHECKS=true
```

If this line is missing, the bot may try to call the license server.

## 19. Simple Beginner Config

Good simple starting settings:

```env
SIMULATION_MODE=true
MIN_BUY_SOL=0.02
MAX_BUY_SOL=0.05
SOL_RESERVE=0.1
MAX_OPEN_POSITIONS=2
BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=false
MIRROR_SLIPPAGE_BPS=1200
MIRROR_RETRY_SLIPPAGE_BPS=2000
STOP_LOSS_PCT=25
TAKE_PROFIT_PCT=100
TRAILING_STEPS=25:-5,35:10,50:30,70:45,90:65
MIN_HOLD_BEFORE_STOP_LOSS_MS=3000
LICENSE_DISABLE_CHECKS=true
```

When ready for real trading, change:

```env
SIMULATION_MODE=false
```

Then stop and restart the bot.

