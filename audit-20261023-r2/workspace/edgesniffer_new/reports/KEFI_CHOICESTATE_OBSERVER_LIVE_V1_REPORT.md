# KEFI Choice-State Observer Live V1 Report

Generated: 2026-10-13T14:14Z

## Scope

This isolated lane reconstructs public PumpSwap pool state independently of
kEFi, and writes a compact pre-action choice snapshot only when a new
first-observed kEfi selection occurs. It does not read or modify V8,
PRE_H3, outcomes, PnL, or promotion inputs.

## Authoritative Pool Identity

- The routed SELL fixture was resolved from its canonical PumpSwap Pool
  account, not transaction-wide mint guessing.
- Pool account owner: `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`.
- The fixture's resolved non-SOL mint matched independent token-balance
  evidence.
- Pools with unsupported quote configuration or absent Pool accounts are
  explicitly excluded. They are never assigned an inferred mint.

## Regression And Bounded Validation

- Release tests: 7 passed.
- Bounded validation runs passed twice after the authenticated Frankfurt RPC
  switch; the last ran for 120 seconds, processed 4 choice-state snapshots,
  and had zero decoder rejections and zero conflicting identities.
- An expired/absent Pool account observed during live startup is now an
  individual explicit exclusion, not a reason to invent an identity or to
  silently continue that pool.

## Live Deployment

- Live unit: `edgesniffer-kefi-streaming-choice-state-observer-v1-live.service`
- Live source instance: `choice-state-v1-1791900859383019675-pid882360`
- Continuity epoch: `1`
- Startup health: 6,309 source frames; durable output sequence 6,309;
  287 maintained public pools; 28 new authoritative Pool-account resolutions;
  3,018 cache hits.
- Decoder rejections: 0
- Identity conflicts: 0
- First durable scientific snapshot under the final atomic-snapshot build:
  NONE YET (no new first-observed kEFi selection since that build was
  deployed; no snapshot is fabricated).
- Runtime footprint at startup: approximately 20 MiB RSS and low-priority
  CPU/I/O scheduling.
- Storage: `/dev/vdc1` has approximately 169 GiB free. The bounded validation
  estimated compact persisted output at approximately 136 MB/day. The live
  lane therefore has a fail-closed 90 MiB output cap, as well as its 60
  snapshot / 24-hour seal limit, so it cannot silently exceed the <100 MB/day
  operational budget.

## Integrity

- V8 capture service remained active during build, validation, and launch.
- PRE_H3 qualification timer remained active during build, validation, and
  launch.
- No V8/PRE_H3 data, configuration, timer, threshold, or scientific contract
  was opened or modified.

## Deployment Hashes

- Source file SHA-256:
  `3f40de61c35bad8bac660a4c1021311fa905803d491433b915da06ee943841c5`
- Deployed binary SHA-256:
  `43f75a87fb92ca11cf157cac2661b7cfa41e671e8afdeedcc8f69205c069d5cf`
- Live systemd unit SHA-256:
  `3c502fdfbaa892990acf36767980bc4fb29066c72423d486d014b0430556e9da`
- Seeded compact Pool identity cache SHA-256 at launch:
  `13b6a7d681c705c1bf43902ce9c458a3266ebdc9cf567dac4e16a77ba9fa2cf0`

CHOICESTATE_OBSERVER_LIVE_MORE_SELECTIONS_ACCUMULATING
