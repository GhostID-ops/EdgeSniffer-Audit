# AGENTS.md

## Current audit entry point

Use [audit-20261023/README.md](audit-20261023/README.md) for the current research
snapshot and its task/decision records. This repository is for audit and research;
do not enable trading or treat historical deployment instructions as authorization.

## Trading Bot Rules

- Never add or rely on Jupiter fallback unless the user explicitly asks for it.
- Prefer Rust executor paths over TypeScript transaction builders.
- Keep buy and sell execution local: Rust sidecar, ShredStream, gRPC, local inferred market-cap filters.
- Preserve latency. Do not add HTTP/API calls to the hot path without explicit approval.
- For PumpSwap, use canonical pool-state account layouts when available.
- Do not trust detector/source account metas for PumpSwap sells.
- RUST_ONLY_BUYS=true and RUST_ONLY_SELLS=true are the expected live-testing posture.
- JUPITER_FALLBACK_ENABLED=false is the expected live-testing posture.
- Before saying a bug is fixed, verify with build/tests and state whether it has been live-tested on VPS.
