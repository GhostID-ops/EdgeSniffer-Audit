# Friend Speed/Performance `.env` Settings

Fastest update path:

```powershell
git pull --ff-only origin restore-version
npm run env:performance
npm run build
```

For friends on free Helius plans, use this instead:

```powershell
git pull --ff-only origin restore-version
npm run env:free-helius
npm run build
```

That command updates only the shared speed/performance keys from `.env.performance` and preserves private keys/API keys in each friend's existing `.env`. It also writes a backup under `.restore-backups/`.

Copy these into your own `.env` after you have your own wallet/private-key and API-key settings.
Do not share private keys, wallet secrets, Helius keys, Shyft tokens, Moralis keys, or Jito auth UUIDs.

```env
# Fast sender / broadcast path
SENDER_ENDPOINT=http://ewr-sender.helius-rpc.com/fast
FAST_MODE=true
BROADCAST_RACE_ENABLED=true

# Shyft low-latency detectors
SHYFT_GRPC_ENABLED=true
SHYFT_GRPC_URL=https://grpc.ny.shyft.to
SHYFT_RABBITSTREAM_ENABLED=true
SHYFT_RABBITSTREAM_URL=https://rabbitstream.ny.shyft.to/
SHYFT_GRPC_COMMITMENT=processed
SHYFT_GRPC_RECONNECT_MS=1000

# Helius transactionSubscribe fallback
HELIUS_TX_SUBSCRIBE_ENABLED=false
HELIUS_TX_SUBSCRIBE_COMMITMENT=processed
HELIUS_TX_SUBSCRIBE_DETAILS=signatures
HELIUS_TX_SUBSCRIBE_URL=

# Tips, priority fees, and compute
TIP_LAMPORTS=2000000
PRIORITY_FEE_MICRO_LAMPORTS=250000
DYNAMIC_PRIORITY_FEE_ENABLED=true
DYNAMIC_PRIORITY_FEE_REFRESH_MS=5000
DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS=1000000
COMPUTE_UNIT_LIMIT=1000000

# Jito
USE_JITO_DIRECT=true
USE_JITO_FALLBACK=true
JITO_REGION=ny
JITO_REGIONS=ny
JITO_AUTH_UUID=

# Direct route behavior
PUMPFUN_EXACT_SOL_IN_BUYS=true
PUMPSWAP_DIRECT_BUY_ENABLED=true
PUMPSWAP_DIRECT_MIN_OUT_BPS=0
PUMPSWAP_DIRECT_PREFLIGHT=true
MIRROR_GRADUATED_BUYS=true

# Entry routing / Jupiter fallback controls
MAX_ENTRY_PRICE_IMPACT_PCT=60
MIRROR_SLIPPAGE_BPS=6000
MIRROR_RETRY_SLIPPAGE_BPS=10000
JUPITER_QUOTE_MIN_GAP_MS=650
JUPITER_QUOTE_429_COOLDOWN_MS=8000
JUPITER_QUOTE_RETRIES=1
JUPITER_OVERSIZE_MAX_ACCOUNTS=56,52,48,44,40,36,32,28,24,20,16

# Current sizing / session behavior from my live setup
MIN_BUY_SOL=0.5
MAX_BUY_SOL=0.5
MIRROR_QUOTE_ASSET=MATCH
MIN_BUY_USDC=10
MAX_BUY_USDC=15
SOL_RESERVE=0.01
MAX_OPEN_POSITIONS=4
BUY_ONCE_PER_TOKEN=true
SKIP_SOURCE_PREEXISTING_MINTS=true

# Current exit/risk-loop timing
MIRROR_SELLS=false
TAKE_PROFIT_PCT=150
TRAILING_STEPS=
STOP_LOSS_PCT=45
RISK_FAST_INTERVAL_MS=400
RISK_FAST_WINDOW_MS=20000
RISK_NEAR_TRIGGER_INTERVAL_MS=400
RISK_NORMAL_INTERVAL_MS=1000
RISK_NEAR_TRIGGER_PCT=8
RISK_DRIFT_PCT_PER_SEC=5

# Shadow/simulation calibration
SHADOW_EXECUTION_MODE=false
SHADOW_LATENCY_MS=250
SHADOW_ENTRY_SLIPPAGE_PCT=3
SHADOW_EXIT_SLIPPAGE_PCT=5
SHADOW_TX_FEE_SOL=0
SHADOW_FAILURE_RATE_PCT=0
SHADOW_RPC_SIMULATE_TX=true
SHADOW_RPC_SIM_FAIL_OPEN=true
```

Each friend still needs their own values for:

```env
PRIVATE_KEY=
HELIUS_API_KEY=
SHYFT_GRPC_X_TOKEN=
MORALIS_API_KEY=
JITO_AUTH_UUID=
TRACKED_WALLETS=
```
