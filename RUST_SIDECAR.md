# Rust Pump.fun Sidecar

Optional hot-path detector for direct pump.fun copy trades.

The sidecar connects to the same Shyft RabbitStream/Yellowstone endpoint as the TypeScript bot, filters tracked-wallet pump.fun transactions, and emits compact JSON swap events back to the bot over stdout. The TypeScript process still owns risk gates, buy-once logic, PnL, Telegram, sells, and all normal fallbacks.

## Build

```powershell
npm run rust-sidecar:build
```

The binary is written to:

```text
rust-sidecar\target\release\pumpfun-sidecar.exe
```

## Enable

```env
RUST_SIDECAR_ENABLED=true
RUST_SIDECAR_BIN=rust-sidecar\target\release\pumpfun-sidecar.exe
```

By default it uses `SHYFT_RABBITSTREAM_URL`, then falls back to `SHYFT_GRPC_URL`. To force a specific endpoint:

```env
RUST_SIDECAR_GRPC_URL=https://rabbitstream.ny.shyft.to/
```

## Current Scope

- Detects top-level direct pump.fun buys/sells for `TRACKED_WALLETS`.
- Preserves `sourceIxAccounts` so the existing direct pump.fun buy path can reuse source account slots.
- Uses processed commitment and RabbitStream-compatible transaction filters.
- Does not yet sign or broadcast in Rust; TypeScript still executes trades after the sidecar handoff.

The next step, if the handoff proves faster/stable in logs, is moving direct pump.fun transaction build/sign/broadcast into this Rust process.
