# Wallet Strategy Presets

These are the working presets we have been using/discussing. Apply them per bot folder, then restart that folder's service.

General rule:

- Fast sniper instances should disable Moralis/DexScreener prebuy checks.
- Use `BUY_ONCE_PER_TOKEN=true` when tracking a wallet cluster, so the first detected wallet buys and the second wallet does not double-buy the same mint.
- Use `EXECUTION_MULTI_SEND_ENABLED=true` only on the fastest/highest-priority instance, or multiple services can compete with each other and hit Jito/IP rate limits.

## Shared Fast Infra

Use this on fast Pump.fun instances unless a strategy says otherwise:

```env
SHYFT_GRPC_URL=https://grpc.va.shyft.to
SHYFT_RABBITSTREAM_URL=https://rabbitstream.va.shyft.to/
JITO_REGION=ny
JITO_REGIONS=ny
DESIRED_REGIONS=ny
JITO_SHREDSTREAM_ENABLED=true
JITO_SHREDSTREAM_GRPC_URL=http://127.0.0.1:9999

USE_JITO_DIRECT=true
USE_JITO_FALLBACK=true
JITO_SEND_MODE=race
BROADCAST_RACE_ENABLED=true
SENDER_RPC_FALLBACK_ENABLED=true

RUST_SIDECAR_ENABLED=true
RUST_SIDECAR_YELLOWSTONE_ENABLED=true
RUST_EXECUTOR_ENABLED=true
RUST_EXECUTOR_TIMEOUT_MS=1500
RUST_HOT_SNIPER_ENABLED=true

JITO_CONNECTION_WARMUP_ENABLED=true
JITO_CONNECTION_WARMUP_MS=15000

DYNAMIC_PRIORITY_FEE_ENABLED=true
DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS=3000000

JITO_TIP_ORACLE_ENABLED=true
JITO_TIP_ORACLE_PERCENTILE=p90
JITO_TIP_ORACLE_MIN_LAMPORTS=300000
JITO_TIP_ORACLE_MAX_LAMPORTS=3000000

FAST_MODE=true
PUMPFUN_EXACT_SOL_IN_BUYS=true
PUMPSWAP_DIRECT_BUY_ENABLED=true
PUMPSWAP_DIRECT_PREFLIGHT=true

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=false
```

## 5qn + EAz Leader Cluster

Purpose: fastest possible entry after whichever wallet leads first. This is the main same-slot/next-slot sniper setup.

Folder/service:

```bash
cd /root/bot/FinalBot-5qn
systemctl restart finalbot-5qn
```

Wallets:

```env
TRACKED_WALLETS=5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF,EAzB32pcd8xP8hQgaAVH9FNKtGW34EuuowfPpprZAGF5
```

Strategy:

```env
SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false
MIRROR_SELLS=false
BUY_ONCE_PER_TOKEN=true

MIN_BUY_SOL=0.15
MAX_BUY_SOL=0.15
MAX_SOL_PER_TOKEN=0.15
MAX_SESSION_DEPLOYED_SOL=0.45
MAX_OPEN_POSITIONS=2

STOP_LOSS_PCT=35
MIN_HOLD_BEFORE_STOP_LOSS_MS=8000
TRAILING_STEPS=35:20,70:35,120:70,200:130,350:240,600:430
TAKE_PROFIT_PCT=

SKIP_SOURCE_PREEXISTING_MINTS=true
SOURCE_PREEXISTING_SCAN_TX_LIMIT=300
MAX_ENTRY_PRICE_IMPACT_PCT=60

EXECUTION_MULTI_SEND_ENABLED=true
```

Notes:

- Best outcome is same slot; very good is `+1 slot`.
- Latest local audit showed your wallet landing mostly `+1 slot` behind 5qn on shared entries.
- Keep local MC filter off for this pure-speed instance unless you intentionally want high-MC-only sniping.

Optional high-MC-only variant:

```env
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=66
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=15000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false
```

## 4BdK

Purpose: bigger-wallet scalp/copy setup. This wallet can justify copying sells, but only if we mirror proportionally so we do not full-exit on a tiny source trim.

Folder/service:

```bash
cd /root/bot/FinalBot-4bdk
systemctl restart finalbot-4bdk
```

Wallet:

```env
TRACKED_WALLETS=4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk
```

Strategy:

```env
SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false
BUY_ONCE_PER_TOKEN=true

MIN_BUY_SOL=0.25
MAX_BUY_SOL=0.30
MAX_SOL_PER_TOKEN=0.30
MAX_SESSION_DEPLOYED_SOL=0.90
MAX_OPEN_POSITIONS=2

MIRROR_SELLS=true
MIRROR_SELL_MODE=proportional
MIRROR_SELL_PROPORTIONAL_MIN_PCT=20
MIRROR_SELL_PROPORTIONAL_MAX_PCT=100
MIRROR_SELL_PROPORTIONAL_FALLBACK=full

STOP_LOSS_PCT=30
MIN_HOLD_BEFORE_STOP_LOSS_MS=8000
TRAILING_STEPS=50:20,100:50,180:110,300:200,500:360
TAKE_PROFIT_PCT=

SKIP_SOURCE_PREEXISTING_MINTS=true
SOURCE_PREEXISTING_SCAN_TX_LIMIT=300
MAX_ENTRY_PRICE_IMPACT_PCT=60

EXECUTION_MULTI_SEND_ENABLED=false
```

Notes:

- `MIRROR_SELL_MODE=proportional` is the important part.
- `MIRROR_SELL_PROPORTIONAL_MIN_PCT=20` avoids wasting exits on tiny source dust trims.
- If 4BdK sells in chunks, this follows the chunk behavior instead of guessing one TP.

## Cya

Purpose: quick scalp on a wallet where higher market-cap entries tend to have enough volume for a fast profit attempt.

Folder/service if using the Cya instance:

```bash
cd /root/bot/FinalBot-cya
systemctl restart finalbot-cya
```

Wallet:

```env
TRACKED_WALLETS=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o
```

Strategy:

```env
SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false
MIRROR_SELLS=false
BUY_ONCE_PER_TOKEN=true

MIN_BUY_SOL=0.06
MAX_BUY_SOL=0.06
MAX_SOL_PER_TOKEN=0.06
MAX_SESSION_DEPLOYED_SOL=0.30
MAX_OPEN_POSITIONS=2

STOP_LOSS_PCT=45
MIN_HOLD_BEFORE_STOP_LOSS_MS=10000
TAKE_PROFIT_PCT=50
TRAILING_STEPS=20:8,35:18,60:35,100:70,180:130

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_USD=66
LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED=true
LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS=300000
LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS=2500
PREBUY_MIN_MARKET_CAP_USD=15000
PREBUY_MAX_MARKET_CAP_USD=
PREBUY_FILTER_FAIL_OPEN=false

EXECUTION_MULTI_SEND_ENABLED=false
```

Notes:

- Use this for the high-MC-only Cya idea.
- If you want pure speed on Cya, turn `LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=false`.
- If you only care about the 50% scalp and do not care about runners, keep `TAKE_PROFIT_PCT=50` and make trailing less important.

### Cya Same-Slot Live Validation

Purpose: remove the market-cap gate and let Rust hot sniper try to buy in the source wallet's slot.
Use this on the main/root bot for the first live same-slot test.

Folder/service:

```bash
cd /root/bot/FinalBot
npm run env:cya-same-slot
npm run build
npm run rust-sidecar:jito-build
systemctl restart finalbot
```

Wallet:

```env
TRACKED_WALLETS=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o
```

Live validation exposure:

```env
SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false
MIRROR_SELLS=false
BUY_ONCE_PER_TOKEN=true

MIN_BUY_SOL=0.04
MAX_BUY_SOL=0.04
MAX_SOL_PER_TOKEN=0.04
MAX_SESSION_DEPLOYED_SOL=0.20
MAX_OPEN_POSITIONS=2

STOP_LOSS_PCT=45
MIN_HOLD_BEFORE_STOP_LOSS_MS=10000
TAKE_PROFIT_PCT=50
TRAILING_STEPS=20:8,35:18,60:35,100:70,180:130

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=false
PREBUY_FILTER_FAIL_OPEN=true

EXECUTION_MULTI_SEND_ENABLED=true
RUST_HOT_SNIPER_ENABLED=true
RUST_HOT_SNIPER_BLOCKHASH_REFRESH_MS=750
RUST_HOT_SNIPER_BLOCKHASH_MAX_AGE_MS=15000
```

Same-slot fee bias:

```env
TIP_LAMPORTS=1500000
RUST_HOT_SNIPER_JITO_TIP_LAMPORTS=1500000
RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS=300000
RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS=5000000
JITO_TIP_ORACLE_PERCENTILE=p95
JITO_TIP_ORACLE_MIN_LAMPORTS=500000
JITO_TIP_ORACLE_MAX_LAMPORTS=5000000
DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS=5000000
```

Scale-up after clean telemetry:

```env
MIN_BUY_SOL=0.06
MAX_BUY_SOL=0.06
MAX_SOL_PER_TOKEN=0.06
MAX_SESSION_DEPLOYED_SOL=0.30
```

Notes:

- Same-slot hot sniper requires live mode; it is disabled in simulation or shadow execution.
- Keep the MC filter off for this run. The high-MC filter is useful for trade selection, but it costs the hot path the thing we are testing: time.
- Check `source slot -> landing slot`, `process -> broadcast`, and `senderPath` with `npm run telemetry:execution`.

## 4vw Full Sell Mirror

Purpose: buy once, then sell the entire bag on the first source sell, even if the source sell is partial. Keep a 50% TP as backup.

Folder/service: usually main bot unless copied into its own folder.

Wallet:

```env
TRACKED_WALLETS=4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9
```

Strategy:

```env
SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false
BUY_ONCE_PER_TOKEN=true

MIRROR_SELLS=true
MIRROR_SELL_MODE=full

TAKE_PROFIT_PCT=50
STOP_LOSS_PCT=35
MIN_HOLD_BEFORE_STOP_LOSS_MS=8000
TRAILING_STEPS=

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=false
```

Notes:

- This is not a proportional sell strategy.
- First sell from source closes our full position.

## Omego

Purpose: not recommended as pure blind multi-buy/multi-sell copy. Better as conservative buy-once with exposure caps, or shadow-test only.

Wallet:

```env
TRACKED_WALLETS=omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq
```

Conservative strategy:

```env
SIMULATION_MODE=false
SHADOW_EXECUTION_MODE=false
BUY_ONCE_PER_TOKEN=true
MIRROR_SELLS=false

MIN_BUY_SOL=0.02
MAX_BUY_SOL=0.02
MAX_SOL_PER_TOKEN=0.02
MAX_SESSION_DEPLOYED_SOL=0.04
MAX_OPEN_POSITIONS=2

STOP_LOSS_PCT=45
MIN_HOLD_BEFORE_STOP_LOSS_MS=10000
TRAILING_STEPS=35:15,70:35,120:75,220:150
TAKE_PROFIT_PCT=

PREBUY_MORALIS_FILTER_ENABLED=false
LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED=false
```

Notes:

- Earlier live tests suggested pure copytrading Omego's multiple buys/sells can overexpose the bot.
- Keep this low-size until there is stronger wallet-specific telemetry.

## Quick Commands

Check latest code in a folder:

```bash
cd /root/bot/FinalBot-5qn
git log -1 --oneline
```

Update a copied folder:

```bash
cd /root/bot/FinalBot-5qn
git fetch origin
git switch feature/latency-roadmap
git pull origin feature/latency-roadmap
npm install
npm run build
npm run rust-sidecar:jito-build
```

Verify after trades:

```bash
cd /root/bot/FinalBot-5qn
npm run telemetry:execution
```

Look for:

```text
source slot -> landing slot
process -> broadcast
senderPath
```
