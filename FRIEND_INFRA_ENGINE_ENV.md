# Friend Infra Engine Env Patch

Fast path after pulling the latest build:

```powershell
npm run env:infra-engine
```

That command merges `.env.infra-engine` into your existing `.env`, preserves secrets, and saves a backup in `.restore-backups`.

Manual copy/paste fallback:

```env
# Execution landing / retry engine
SENDER_RPC_FALLBACK_ENABLED=true
EXECUTION_MULTI_SEND_ENABLED=false
EXECUTION_RESCUE_RETRIES=2
EXECUTION_RESCUE_DELAY_MS=25
EXECUTION_ALT_ADDRESSES=
EXECUTION_TIP_MULTIPLIERS=pumpfun-direct-buy:1,pumpswap-direct-buy:1,pumpfun-direct-sell:1,pumpswap-direct-sell:1,jupiter-buy:1,jupiter-sell:1

# Runtime telemetry
EXECUTION_TELEMETRY_ENABLED=true
EXECUTION_TELEMETRY_PATH=data/execution-telemetry.jsonl

# Wallet/account warmup
WARM_WALLET_MODE=true
KEEP_WSOL_ATA_OPEN=true
```

Recommended first run:

```powershell
git pull
npm install
npm run build
```

Keep `EXECUTION_MULTI_SEND_ENABLED=false` for the first live test. Turn it on only if dropped/landing failures are still showing in `data/execution-telemetry.jsonl`.
