# 2CQ new-wallet system audit — 2026-10-19

Audit scope: confirmed new-wallet source, route/decoder coverage, selective post-entry capture, strategy lab, service health, storage, and trading posture. The audit used VPS `159.195.207.109` and did not enable trading.

## Core lane result

| Check | Result |
|---|---|
| Finalized wallet source | Active, `NRestarts=0` |
| Selective PumpSwap window collector | Active, `NRestarts=0` |
| Source JSONL | 12,319 valid, 0 malformed |
| Pool-window JSONL after repair | 487 valid, 0 malformed |
| Unique anchor windows | 18 |
| Unique market signatures | 469 |
| Duplicate keys after repair | 0 |
| Collector errors in state | 0 |
| Live trading | Disabled |
| Jupiter fallback | Disabled |
| Rust-only flags | Buys and sells true |

The pool-window file previously contained 897 rows: 29 anchor envelopes and 868 market rows. It had 11 duplicate anchor envelopes and 399 duplicate market rows caused by the state file living under systemd `PrivateTmp`. The original file was archived before repair. The repair removed 410 duplicate rows, merged one later larger anchor signature set, and wrote 487 unique working rows with no conflicting duplicates. The state file now lives under persistent research storage, so a service restart cannot silently recreate the duplicate history.

## Unresolved-program investigation

The 369 unresolved anchors remain split across:

- `93JqDj2DjxDYuKPY3Z1rUG3YzPJykyMsrisPnvWYeuq5`: 124 immutable-snapshot anchors.
- `ABWKmNctDYqfC3nTBJW63oFkU5KYAjfMFigWzhuia8NV`: 241 immutable-snapshot anchors.

The current-source diagnostic expanded those program-frame counts to 1,690 and 7,780 respectively. The raw instruction evidence shows:

- Both programs are invoked top-level.
- The tracked wallet is absent from every target instruction.
- Wallet token delta is zero in every target frame.
- `93J...` has no canonical venue CPI and has multiple short instruction shapes plus a longer 72-byte shape.
- `ABWK...` has mostly 44-byte top-level instructions; only eight frames contain a known Pump CPI, while the wrapper-only majority has no canonical PumpSwap/Pump event invariant.

The correct result is not “decode bytes into a guessed trade.” The decoder gate remains fail-closed: no venue or buy/sell transition is admitted without a known discriminator/layout, account-role invariant, and wallet-side state transition. The current diagnostic artifact is `unknown-program-diagnostic-current-v2.json` on the VPS; the earlier immutable diagnostic remains preserved.

## Strategy-lab refresh

The repaired current tape was run through the batched strategy lab:

- 12,521 source anchors loaded.
- 18 exact PumpSwap anchor windows prepared.
- 8,316 candidate outcomes across 495 matrix combinations.
- 0 controls.
- Maximum actionable count for any candidate: 5/18.
- Exploratory candidates remained negative and every candidate remained promotion-blocked.
- `policy_frozen=false`, `selection_performed=false`, `trading_enabled=false`.

This is useful discovery evidence, but not validation. The zero-control result and 13/18 missed marks on the most actionable candidates are the current statistical and measurement blockers.

## Storage and unrelated failures

The root/research filesystem is at 94% used with approximately 66 GB free. No large full-block collector should be started.

The failed-unit list contains unrelated or legacy services. The important failure causes are recorded here rather than hidden:

- Docker log rotation: referenced `/etc/logrotate.d/docker-container-json` is missing.
- Evidence dependency snapshot: failed with `ENOSPC`.
- AllenHark arrival classifier: missing historical reconciliation manifest.
- Supplemental KEFI observer: missing environment variable.
- Meteora and Raydium choice windows: timed out.
- Pump exact-alpha scan: immutable checkpoint input mismatch.
- VPS doctor/safe-clean units: `203/EXEC`.

None of these failures affected the two active new-wallet collectors in this audit. They should not be restarted as part of this route experiment without a separate repair decision.

## Collection estimate and next gate

The source covered approximately 3.86 hours and contained 18 exact PumpSwap buys, about 4.7/hour in this observed interval. This rate is not stable, so it is an estimate rather than a promise:

- 50 exact windows: about 7 more hours at the observed rate.
- 100 exact windows: about 18 more hours.
- 30 actionable windows at the observed 5/18 rate: roughly 19 additional hours, before controls.

The next required build is a bounded contemporaneous-control collector. The previous full-block tape was too expensive and exhausted storage; controls must be captured selectively and deduplicated. After controls and more complete marks accumulate, rerun the same matrix on a sealed discovery snapshot, freeze one route-specific candidate, and validate it on a fresh prospective cohort.

## Final audit checkpoint after bounded-control deployment

At the follow-up audit, the bounded control service was active with `NRestarts=0`, `ExecMainStatus=0`, and no warning/error journal entries in the last 30 minutes. It had produced:

- 18 `ANCHOR_CONTROL_WINDOW` records;
- 450 unique `CONTROL_SAME_SLOT` records;
- 0 malformed rows;
- 0 duplicate control identities;
- 0 collector errors.

The source continued appending during the audit. The final recheck at `2026-10-19T23:08:51Z` showed 12,562 source records, with the bounded collector's source byte offset exactly aligned to the file size and 12,562 processed source signatures. This explains the earlier short-lived offset difference as a normal append race, not a source rewrite or data loss.

The finalized source had 12,548 valid records, 0 malformed rows, and 0 duplicate signatures. The repaired selective pool tape had 487 valid rows, 18 unique anchor windows, 469 unique market signatures, and 0 duplicate identities. Its persistent state was at the source byte offset and reported 18 anchors, 435 post-repair market writes, and 0 errors; the 469-row working file includes preserved unique historical market rows from before the state repair and is not treated as 469 new observations.

The three new-wallet collection services were active with zero restarts. No service capable of live/dust submission was active, and the environment still read `LIVE_TRADING_ENABLED=false`, `RUST_ONLY_BUYS=true`, `RUST_ONLY_SELLS=true`, and `JUPITER_FALLBACK_ENABLED=false`.

The whole VPS is not 100% healthy: unrelated legacy/auxiliary units remain failed, and the root/research filesystem is at approximately 94% used with 68 GB free. Those failures are outside the active new-wallet lane and were not restarted. The active lane is operationally healthy, but “bug-free” is not claimed; ongoing collection and later sealed-snapshot audits remain required.

The 369 unresolved anchors were not decoded by guesswork. Current raw diagnostics still show no canonical target-venue/event invariant for the two unknown programs, so they remain explicitly unresolved and preserved. They cannot be counted as genuine buys until evidence supports a decoder.

At the observed rate of approximately 4.7 exact PumpSwap wallet buys/hour, the rough collection estimates are about 7 more hours for 50 total exact windows, 18 more hours for 100, and about 19 more hours for 30 actionable windows at the current 5/18 mark-coverage rate. This is an event-rate estimate, not a deadline.

## Storage correction and post-cutover audit (2026-10-19)

- The new local 1.5 TB volume is `/dev/vdd1`, mounted at `/mnt/edgesniffer-canonical-hot`; it now has approximately 1.3 TB free. The separate NFS expansion mount `/mnt/edgesniffer-expansion` was not copied or modified and remains a separate full 1 TB mount.
- The active 2CQ route-census namespace is persistently bind-mounted from `/dev/vdd1`; its original directory was preserved on `/dev/vdb1`. The three active receive-only collectors are running with `NRestarts=0` and `ExecMainStatus=0`.
- An unnecessary archive migration was stopped. Its incomplete ~1.1 TB duplicate on `/dev/vdd1` was removed only after verifying the authoritative source remained intact. No research source or canonical evidence was deleted.
- Post-cleanup counts: 17,461 valid source rows with 0 duplicate signatures; 22 pool windows and 1,769 unique market signatures with 0 duplicate identities; 22 control windows and 550 unique same-slot controls with 0 duplicate identities. All three streams have 0 malformed rows.
- Storage after cleanup: old research volume `/dev/vdb1` is 43% used with approximately 848 GB free; new active-research volume `/dev/vdd1` is 8% used with approximately 1.3 TB free. The earlier duplicate pool-window records remain repaired and archived separately; this storage cleanup did not alter that evidence.

## Storage repair checkpoint

The new 1.5 TB volume is `/dev/vdd1`. The active 2CQ namespace is now bind-mounted there at its original service path. The inactive Yellowstone archive was migrated and verified on that volume; six inactive KEFI evidence sets totaling approximately 265 GB were migrated to `/dev/vdb1` and bind-mounted back to their original root paths.

The first root-offload attempt used a path outside the vdb mount and was stopped after it began consuming root space; its partial copy was removed. The corrected migration used `/mnt/HC_Volume_106775198/edgesniffer-research/root-offload`, passed zero-difference metadata verification, and completed without touching active collectors. Persistent fstab entries were repaired and `mount -a -f` passes.

Final capacity: root `/dev/vda3` 85% used / 157 GB free; research `/dev/vdb1` 43% used / 848 GB free; new hot `/dev/vdd1` 87% used / 188 GB free. The three active 2CQ collectors remain `active/running`, `NRestarts=0`, and have no recent error-level journal entries. Current counts are 17,112 valid source rows, 22 pool windows, 1,769 market rows, and 550 bounded controls.
