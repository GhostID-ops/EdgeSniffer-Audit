# Non-Pump Route Roadmap

## Why We Need Classification First

`jupiter-non-pump` is not one route. It can mean Raydium CPMM, Raydium CLMM,
legacy Raydium AMM v4, Meteora DLMM/DBC, Orca Whirlpool, or another router.

The hot bot now records source program IDs for new misses. For older misses, use
the offline classifier below. It fetches historical source transactions from RPC,
so it is intentionally **not** part of the trading hot path.

## Classify Historical Misses

```bash
cd /root/bot/FinalBot-cya
npm run audit:missed-route-protocols -- --telemetry=data/execution-telemetry-cya-live.jsonl --max=500 --delay-ms=80 --out=data/missed-route-protocols.jsonl
```

If your instance uses the default telemetry path:

```bash
npm run audit:missed-route-protocols -- --telemetry=data/execution-telemetry.jsonl --max=500 --delay-ms=80 --out=data/missed-route-protocols.jsonl
```

## Build Order

1. Raydium CPMM if it is the largest bucket.
2. Raydium legacy AMM v4 if old pools dominate.
3. Raydium CLMM if concentrated liquidity dominates.
4. Meteora DLMM/DBC.
5. Orca Whirlpool.

Rust should be the final execution path for these routes: TypeScript can classify
and hand source accounts to Rust, while Rust builds/signs/broadcasts locally.
