# kEFi Capacity Recovery And Holdout Launch V1

Generated: 2026-10-14T03:06:30Z

## Capacity Recovery

- Hot volume identity: `/dev/vdc1` mounted at `/mnt/edgesniffer-kefi-hot`.
- Free before recovery: 69.992 GiB.
- Released retired V8 promotion-window cache: 368,235,663,098 bytes.
- Free after recovery: 413.029 GiB at resume gate; 409 GiB after source/observer restart.
- Dependency audit: `DEPENDENCY_NONE_PROVEN`.
  - Audit: `/mnt/edgesniffer-expansion/evidence-lake-v1/dependency-audits/kefi-v8-shared-windows-20261014T0250Z.json`
  - SHA-256: `7180ba8abf53c0209e6c70041ac24f2d77371327abda4ee895d1dbacd97cf3d3`
- Reclaim receipt: `/mnt/edgesniffer-expansion/evidence-lake-v1/release-receipts/kefi-v8-shared-windows-20261014T0252Z.json`
  - SHA-256: `0f1f928d7c02bb701d720eba1b37b815dd10c88ebfc7a0ca81e9b75a5e9dff65`
- Resume gate receipt: `/mnt/edgesniffer-expansion/evidence-lake-v1/recovery-receipts/kefi-capacity-recovery-20261014T0256Z.json`
  - SHA-256: `7147a30dec65e61b510e16d8a9513e121d0f1a6907c1e02ad0957c0630c479aa`

## Previous PRE_H3 Cohort

The capacity interruption met its frozen hard-wall rule. The old PRE_H3 cohort was terminated and remains unbridged/unscored.

- Termination receipt: `/mnt/edgesniffer-kefi-hot/kefi-pre-h3-actionability-qualification-v1/cohort-20261012T223947Z/hard-wall-termination-v1.json`
- SHA-256: `355ab9bcfab003b89693bbe27fd1d6381b2becee919d64d93553572d20a1b932`
- PRE_H3 timer: disabled/inactive.
- Outcomes/PnL: not accessed.

## Frozen Development Verification

- Development-40 validity receipt SHA-256: `f19ee8bdff145009126973c0d1588d615aa39a5f80b29a05cde02be364cefd7a`
- Expanded model SHA-256: `f379813a947c90a79a2ff64a5d2dfb4b54dd263e589ac7a4e3104e01b73a91f2`
- Development freeze SHA-256: `0cd032e4ab7128afd12175e24906e01bb2b8a23875ac9d4fd246b0d709d8f4e2`
- Observer binary SHA-256: `49d50d8bbb6ac442f050263bbc6ab16120decc8f2f7423ba44387d5bcda564bc`

## Replacement Holdout

An initial zero-snapshot launch was permanently voided after its replay slot had aged beyond the provider's available replay range before subscription. It produced zero rows and zero snapshots and is not scientific evidence.

- Void receipt: `/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/replacement-holdout-v1/void-receipt-v1.json`
- SHA-256: `402858fb27015cbe9e0b629ed92544d7338f26259c3a7697c44719a99c07b4c1`

The fresh active cohort is the only live replacement cohort:

- Root: `/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/replacement-holdout-v4`
- Boundary UTC: `2026-10-14T03:04:59.818043Z`
- Boundary SHA-256: `61ed93f84b0de801abe507ebc0889f1727f36f781d1d74adebd1486224fa4d5c`
- Tail-sync receipt SHA-256: `482e0dc9ce206b6528c4e9956fe6752e1002305fbbf822f332ec0db486c1bcd3`
- Tail overlap: 32 finalized slots.
- Ledger manifest SHA-256: `4d5f7c6cd898d8a08929005fc582a9b070fb423db74bb5381f3fb3f6fa90deff`
- Source instance: `2026-10-14T02:42:19.851529226Z-pid1060664`.
- Source epoch: `1`.
- Source reconnects/gaps/receive errors since source restart: `0 / 0 / 0`.
- Replacement observer: active, zero restarts.
- First durable scientific tick: not yet present at report generation. No pre-boundary data was substituted.

## Status

`CAPACITY_RECOVERED_REPLACEMENT_HOLDOUT_LIVE_PENDING_FIRST_SELECTION`
