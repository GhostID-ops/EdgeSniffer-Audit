# Jito ShredStream Integration

This bot can consume Jito ShredStream entries through the Rust sidecar, but it does not receive
raw shreds directly. Run `jito-shredstream-proxy` as the shred receiver/deshredder, expose its
local gRPC entries service, then let the Rust sidecar subscribe to that local service.

## What You Need

- Jito ShredStream approval for the Solana public key used as the proxy `AUTH_KEYPAIR`.
- A VPS with public IPv4 and inbound UDP allowed.
- Inbound `20000/udp` open to Jito's New York shred receiver IPs.
- Linux is strongly recommended for the proxy. Jito recommends host networking for Docker.

Jito docs: https://docs.jito.wtf/lowlatencytxnfeed/

## Build The Bot Sidecar With ShredStream Support

Default Windows builds do not include the Jito entry decoder because Solana entry decoding pulls
native dependencies that are easier on Linux. On the VPS:

```bash
sudo apt update
sudo apt install -y build-essential perl pkg-config
npm run rust-sidecar:jito-build
```

Point `.env` at that binary and enable the stream:

```env
RUST_SIDECAR_ENABLED=true
RUST_SIDECAR_YELLOWSTONE_ENABLED=true
JITO_SHREDSTREAM_ENABLED=true
JITO_SHREDSTREAM_GRPC_URL=http://127.0.0.1:9999
```

Keep `RUST_SIDECAR_YELLOWSTONE_ENABLED=true`. ShredStream sees pre-execution top-level
transactions, but Yellowstone/Shyft remains important for processed balance deltas and router CPI
buys whose inner Pump.fun instruction does not exist yet at shred time.

## Run Jito ShredStream Proxy

Native:

```bash
RUST_LOG=info cargo run --release --bin jito-shredstream-proxy -- shredstream \
  --block-engine-url https://mainnet.block-engine.jito.wtf \
  --auth-keypair /path/to/jito-auth-keypair.json \
  --desired-regions ny \
  --src-bind-port 20000 \
  --grpc-service-port 9999 \
  --dest-ip-ports 127.0.0.1:8001
```

Docker host networking:

```bash
docker run -d \
  --name jito-shredstream-proxy \
  --rm \
  --network host \
  -e RUST_LOG=info \
  -e BLOCK_ENGINE_URL=https://mainnet.block-engine.jito.wtf \
  -e DESIRED_REGIONS=ny \
  -e SRC_BIND_PORT=20000 \
  -e GRPC_SERVICE_PORT=9999 \
  -e DEST_IP_PORTS=127.0.0.1:8001 \
  -v /path/to/jito-auth-keypair.json:/auth.json \
  -e AUTH_KEYPAIR=/auth.json \
  jitolabs/jito-shredstream-proxy shredstream
```

## Verify Shreds Are Arriving

```bash
sudo tcpdump 'udp and dst port 20000'
```

Jito says packets are roughly 1200 bytes when the stream is live. If nothing arrives, check Jito
approval, firewall rules, public IPv4, and whether your VPS provider is doing NAT.

## What The Bot Detects From Shreds

The sidecar scans decoded entries for top-level Pump.fun and PumpSwap buy instructions touching
your `TRACKED_WALLETS`. It emits the same JSON swap shape as the existing Rust sidecar, so the
Node bot's signature dedupe/routing path stays the same.

Current limitation: pre-execution shreds do not include runtime inner instructions or token balance
deltas. Router CPI-only buys are still caught by Shyft/Yellowstone after processing.
