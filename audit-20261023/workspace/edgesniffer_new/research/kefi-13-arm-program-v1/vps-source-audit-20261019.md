# VPS receive-only source audit — 2026-10-19

## Evidence

- SSH connectivity: `VPS_OK`.
- Existing Yellowstone full-meta kEFi source: active and fresh under `/mnt/edgesniffer-kefi-hot/kefi-capture-first-live-v5`.
- Full-meta freshness watchdog: `collectorActive=true`, `action=NONE`, frame age approximately 1–2 seconds during audit.
- Existing source is outcome-blind/receive-only; no submission path was invoked.
- The 2CQ AllenHark capture-first observer was started at `2026-10-19 01:37:10 UTC` for a no-submit source test.
- Observer state after the verification window: connected, `streamMessages=0`, `entriesSeen=0`, `transactionsSeen=0`, `candidatesWritten=0`.
- Jito ShredStream proxy metrics: `received=0`, `entries_emitted=0`, `dispatch_transactions=0`.
- Upstream error repeated in proxy logs: `PermissionDenied: pubkey not whitelisted` from the AllenHark block engine.
- The observer was stopped after diagnosis. No trading service was started or restarted and no transaction was submitted.
- Follow-up read-only health check at `2026-10-19 01:46 UTC`: the Yellowstone source remained active, with `110,016,917` frames received, `40,129,897` direct-Pump frames, `70,641,044` direct-PumpSwap frames, and latest frame timestamp `2026-10-19T01:46:17Z`.
- Those counters confirm source freshness only; they do not establish canonical arm assignment, exact state parity, or prospective economics.

## Repair and post-freeze verification

- The original promotion destination `/mnt/edgesniffer-expansion` was confirmed at 100% NFS usage with 0 bytes available. No historical files were deleted.
- The receive-only full-meta service was restarted with promotion output moved to `/mnt/edgesniffer-kefi-hot/kefi-capture-first-promotions-v5`, which had approximately 159 GiB available.
- After repair, the source reported `rollingWindowPromotionFailures=0`, `sharedBurstGroupsFailed=0`, and completed sealed raw windows, including `kefi-20261019T020521021Z` and later windows.
- The cohort boundary was reset to `2026-10-19T02:03:37.986067Z` after the repair and before accepting new rows. A pre-boundary event was rejected by both the materializer and strict ingest.
- One sealed post-freeze window was decoded with the VPS unified decoder. Finalized transaction program-log resolution proved two direct PumpSwap events; both were appended to ARM07. The fresh-cohort audit reports 2 rows, 0 errors, and no rows in the other 12 partitions.
- ARM07 exact fixed-30-second economics currently reports 3 actionable observations, 33.3% win rate, `-0.044537879 SOL` paper PnL, PF `0.3156`, and no same-venue controls. Ranking therefore defers the arm at insufficient sample; it does not kill or promote it.
- The current cumulative source health has `framesMetaIncomplete=1`; source compatibility remains fail-closed until that transport condition is resolved or the exact cohort window is independently certified clean.

## Interpretation

The VPS has an active separate Yellowstone full-meta source. The AllenHark path remains unauthorized and is not used for this cohort. Candidate JSONL metadata is not ingested directly; only sealed raw windows are materialized and venue-resolved. The current cohort is a valid two-row ARM07 pilot, not a 13-arm validation sample.

## Gate state

- Fresh arm-partitioned cohort: achieved for ARM07 only, 2 clean rows; 0 rows in the other 12 partitions.
- Exact fixture parity: 0/13.
- Promotion-ready economics: 0/13.
- Winner: none.
