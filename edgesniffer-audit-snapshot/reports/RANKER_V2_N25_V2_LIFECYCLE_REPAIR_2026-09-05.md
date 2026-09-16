# Ranker V2 N25-v2 Recovery and Lifecycle Repair

Generated: 2026-09-05T21:42:02Z

Final classification:

`RANKER_V2_POSITION_RECOVERED_LIFECYCLE_REPAIR_COMPLETE_READY_FOR_N25_V3`

This means the recovery is finalized, the engineering repair is staged and tested,
and a fresh N25-v3 is ready to be explicitly authorized. It does not mean the new
binary has already been live-proven. N25-v2 was not resumed and N25-v3 was not started.

## Executive Summary

- The stranded Token-2022 position was flattened with one controlled economic sell.
- The wallet now has zero nonzero token accounts; managed, unmanaged, and pending-sell
  inventories are all zero.
- The buy did not fail confirmation. It finalized successfully. A local lifecycle
  branch misclassified a rejected exact-balance update as `buy_not_confirmed` and
  deleted the canonical watcher after ownership had already been proved.
- Ownership, transaction confirmation, strategy position, exit action, and capacity
  now have separate state authority. Positive authoritative wallet inventory cannot
  be released until an authoritative zero-balance proof exists.
- A missing canonical watcher is rebuilt from a durable recovery command or remains
  explicitly blocked-open with capacity held. It cannot become unmanaged inventory.
- Exact quantity changes invalidate retained marks/curve generations. The old
  +416.94% mark cannot trigger from a pre-update curve/quantity pairing.
- Local and Linux `cargo test --all-targets` pass. The new Linux release binary is
  archived and hashed. V3 units are loaded, disabled, inactive, and have never run.

## 1. Recovery Sell Result

| Field | Value |
|---|---:|
| Mint | `9Zr7Un5XvCtuVqbkqZNNCC2tMyxFKiiH5jHXuQ8Upump` |
| Full amount sold | `321914716761` raw |
| Route | `pumpfun-direct-sell-v2-manual-emergency` |
| Signature | `36CLA5v4Lr6bN11TD9UqWt8huHaw2dSaWQV5vM27fCjkrMiy1n7t1eFVdLdaa22GvB9WTLc5WSUmvhDa3AYAm89f` |
| Finalized slot | `444611185` |
| Chain error | `null` |
| First send / ACK / result | `12ms / 12ms / 24ms` |
| Gross SOL proceeds | `0.008890572 SOL` |
| Transaction fee | `0.000005000 SOL` |
| Net wallet delta | `+0.008885572 SOL` |

Exactly one recovery transaction was signed and submitted. There was no duplicate
economic sell.

## 2. Final Wallet Flatness

Finalized RPC reconciliation reports:

- Held mint balance: `0` raw.
- Nonzero legacy or Token-2022 accounts in the execution wallet: `0`.
- Managed positions: `0`.
- Unmanaged positions: `0`.
- Pending sell: none.
- Wallet token inventory: flat.

## 3. ATA and Rent

ATA `Dq4YTiwV1bGr8pEG2K5jhCVSQQwihefxX7jHWpJjcpZc` remains initialized under
Token-2022 with balance zero and `0.001887234 SOL` rent. It was not closed because
the available close helper is hard-coded for the legacy token program; no already
proven Token-2022 close path existed. Deferring ATA closure did not delay recovery.

## 4. Exact Terminal Race Root Cause

The failure was an authority inversion between the buy-finality task, the Yellowstone
balance-cache task, and the exit-action task:

```text
18:56:27.309 BUY_RESERVED / candidate selected
18:56:27.313 BUY_SENT
18:56:27.352 BUY_ACK
18:56:27.354 BUY_RESULT ok
18:56:27.474 watcher mark: -21.77%, provisional quantity
18:56:27.791 watcher mark: -27.06%, provisional quantity
18:56:27.950 BUY_FINALITY landed_ok, finalized, slot 444593131
18:56:27.950 BALANCE_PROVEN 321914716761, Yellowstone exact
18:56:27.950 POSITION_ACTIVE then EXIT_PENDING
18:56:27.953 post-buy exact update rejected: exit sell pending
18:56:27.954 POSITION_RELEASED with default reason buy_not_confirmed
18:56:27.961 stale mixed-generation mark creates false +416.94% TP
18:56:27.962 authoritative sell refresh corrects value to -2.46914%
18:56:27.962 SELL_PRE_SIGN rejects canonical_position_missing
18:56:27.975 RPC exact proof finds no watcher
18:56:30.047 first repeated unmanaged-inventory alert
```

The relevant tasks/functions were `submit_selected_candidate_buy`, the Yellowstone
balance-cache path ending in `maybe_activate_selected_candidate_stream_exact_balance`,
`ExitWatcherRegistry::update`, `run_exit_action_loop`, and
`ExitWatcherRegistry::guard_exit_action_before_submit`.

## 5. `buy_not_confirmed` Source and Ordering

There was no negative chain confirmation. The buy-finality row was:

`status=landed_ok`, `confirmationStatus=finalized`, `error=null`, slot `444593131`.

`buy_not_confirmed` was a local default label selected after
`ExitWatcherRegistry::update` returned false with
`cannot update while a Rust exit sell is pending`. It was therefore a state-decision
bug, not timeout, ACK lag, finalized-status lag, or an on-chain failure.

## 6. Why Ownership Lost

The old implementation conflated transaction bookkeeping with economic ownership.
`BuyNoPositionReleased` could cancel the watcher and release capacity based on a
local activation/update result. Exact wallet quantity had no independent authority
to veto that transition. A pending exit made the exact-balance update fail, which
fed the incorrect terminal branch.

The stale TP exposed this precise pending-update race. Any TP, SL, max-hold,
hard-stop, or manual exit overlapping the balance update could have exposed it.
Without an exit trigger, the exact pending-update rejection would not occur, though
the old model still lacked the global ownership invariant. The defect was token
program agnostic and could affect legacy Pump tokens as well as Token-2022. The
observed incident is the exact-balance-before/post-finality interleaving; the repair
also covers the inverse ordering and restart reconciliation.

## 7. Lifecycle Code Changes

`ownership_lifecycle.rs` now separates:

- `BuyConfirmationState`
- `WalletOwnershipState`
- `StrategyPositionState`
- `ExitActionState`
- `CapacityState`

Positive balance calls `prove_owned`, forces capacity held, and preserves either a
managed watcher or `OwnedInventoryRecovery`. Late failed/uncertain confirmation
cannot overwrite ownership. Only `prove_flat` can transition owned inventory to
closed/free. Every release path checks `blocks_release` first.

Durable recovery commands are appended after a buy result and loaded at startup.
Authoritative zero appends a matching tombstone. This persistence is deliberately
off the pre-send hot path.

## 8. `canonical_position_missing` Recovery

The pre-submit guard still rejects a missing canonical watcher, but the rejection is
no longer terminal. If lifecycle ownership is nonzero,
`ensure_selected_candidate_owned_exit_watch` rebuilds a generation-safe watcher from
the durable route/template, applies exact wallet quantity and cost basis, activates
normal exit management, and keeps capacity held. If the command is unavailable, it
enters an explicit blocked-open state and emits a critical event; it does not free
capacity or forget ownership.

## 9. Unmanaged-Inventory Invariant

Any nonzero wallet position absent from the watcher registry now immediately:

1. records authoritative ownership,
2. occupies capacity,
3. attempts exact recovery adoption,
4. remains explicit and retryable or explicitly blocked.

A terminal watcher with a persistent positive residual is held through the existing
60-second stale-RPC grace, then rearmed as a new recovery generation. This works even
when ordinary auto-flatten is disabled, as it is in the frozen N25 settings.

The forbidden steady state `wallet > 0 + managed=0 + no recovery` is no longer a
valid lifecycle outcome.

## 10. False +416.94% Mark

The provisional watcher held `240888431543` raw and had fresh curve marks of
`-21.77%` and `-27.06%`. Exact balance then changed inventory to `321914716761` raw.
The old update replaced quantity while retaining the previous mark/curve generation;
activation recomputed the larger quantity against retained state and emitted an
unknown-source, slot-zero `+416.93629%` TP (`0.051693629 SOL` expected).

The send-time Yellowstone refresh, slot `444593131`, age `71ms`, correctly repriced
the same exact quantity to `0.009753086 SOL` or `-2.46914%`.

The fix clears latest mark and peak whenever exact quantity changes, sets
`fresh_mark_required_after_inventory_change`, and refuses to trigger until a new
authoritative curve mark arrives. A pending sell revalidates against deferred exact
quantity before signing.

## 11. Prior N25 Search

| Cohort | Attempt mints | Malformed/interleaved rows | Lifecycle-race mints |
|---|---:|---:|---:|
| N25-v1 | 17 | 6 | 0 |
| N25-v2 | 11 | 4 | 1 |

Only `9Zr7...pump` had `BuyNoPositionReleased`, pending-update rejection,
`canonical_position_missing`, and wallet-owned/managed-zero abandonment. It produced
1,132 unmanaged alerts.

N25-v1 had no instance of this race. Its absurd mark/decision events were confined
to known Mayhem mint `3v8q...pump` (maximum `+1961.76611%`) and belong to the separate
Mayhem route/recovery incident, not this unknown-source mixed-generation defect.

## 12. Exact Incident Replay

The deterministic replay covers:

buy result -> exact ownership -> activation -> exit pending -> stale trigger ->
authoritative quote correction -> late negative/uncertain transaction bookkeeping ->
canonical mutation attempt.

Assertions prove ownership remains open, capacity stays held, rejected TP leaves the
position managed, normal SL/max-hold/recovery stay available, a valid sell can flatten,
and only a zero-balance proof closes.

## 13. Failure-Injection Matrix

All 20 required cases pass:

1. balance before ACK
2. balance before finality
3. balance after finality
4. ACK missing with ownership
5. status timeout with ownership
6. status failure with ownership
7. uncertain buy result with ownership
8. balance update during exit pending
9. stale TP rejected at send time
10. stale SL rejected at send time
11. sell build failure
12. sell submission failure
13. uncertain sell finality
14. missing canonical position
15. restart while owned
16. orphan reconciliation
17. Token-2022
18. legacy token
19. max-hold with late buy confirmation
20. multiple balance updates during exit

Every case finishes `CLOSED` after zero proof or remains
`EXPLICITLY_BLOCKED_OPEN_POSITION_WITH_ACTIVE_RECOVERY`. None can finish unmanaged.

## 14. Terminal-State Proof

There is one lifecycle record per mint. Its invariant requires every economically
open record to be managed/recovery with capacity held. All mutation methods assert
that invariant in debug/test builds. Runtime release paths separately emit a critical
`OwnedInventoryReleaseBlocked` event and retain capacity when wallet ownership is
nonzero.

The only owned-to-closed transition is authoritative `prove_flat`.

## 15. Max-Hold Proof

Max-hold is evaluated before ordinary failed-exit cooldowns, survives TP6003 salvage,
survives deferred exact-balance adoption, and is recreated during canonical recovery.
Sell build/submission/finality failures reopen retry state without removing ownership.
Persistent terminal residuals receive a new recovery generation after stale-RPC grace.

Tests cover max-hold with late confirmation, failed pre-exact TP, armed TP6003,
route failure, terminal residual, and exact-quantity changes.

## 16. Ordinary Execution Regression

No strategy semantics changed. V3 differs from V2 only in its output/state paths and
the new ownership-state journal. Frozen values remain:

- Ranker threshold `0.590596`
- same direct scorer emitter and event-gated states
- `0.01 SOL`, `MAX_OPEN=1`, fresh `0/25`
- FillFirst and `min_tokens_out=1`
- TP `+20%`, SL `-20%`, true max hold `120s`
- Jupiter disabled and optional fees zero
- ordinary Pump and Token-2022 sell builders unchanged
- Mayhem policy unchanged

Canonical scorer, semantic-freeze, Atlas, and emitter hashes match the N25-v2 freeze.

Verification:

- Local `cargo fmt --check`: PASS.
- Local `cargo test --all-targets`: PASS (`97` library and `172` main-sidecar tests;
  all other target suites green).
- Linux `cargo test --all-targets`: PASS (`97`, `172`, and the `86`-test helper suite;
  all other target suites green).
- Both staged systemd units pass `systemd-analyze verify`.

## 17. Latency Regression

The 10 ordinary N25-v2 live attempts measured:

| Metric | Median | P75 | P90 | Worst |
|---|---:|---:|---:|---:|
| selected -> first send | 2.0ms | 2.75ms | 7.0ms | 16ms |
| selected -> buy result | 36.5ms | 42.0ms | 47.8ms | 64ms |
| selected -> first sellable proof | 260.5ms | 371.25ms | 439.3ms | 496ms |

The lifecycle patch adds only in-memory mutex/map operations before submission. Its
durable journal write and `sync_data` happen after the buy result, not before first
send. It adds no synchronous RPC, HTTP, file, or database lookup to the entry hot
path. Static/harness regression is clean; post-patch live latency remains to be
measured in N25-v3 and is not claimed as already live-proven.

## 18. Immutable N25-v2 Economics

N25-v2 remains permanently classified:

`RANKER_V2_N25_V2_INTERRUPTED_POSITION_STATE_DESYNC_BLOCKER`

The emitter is stopped/disabled. Its receiver remains on the original immutable
binary and PID. State remains 11/25. The recovery transaction is separate incident
recovery and the shadow TP is not a live win.

| # | Mint | Entry | Gross exit | ROI | Strategy PnL | Tx fees | ATA/rent | Frozen shadow | Shadow PnL | Live-shadow | Exit | State |
|---:|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|---|
| 1 | 9Zr7Un5XvCtuVqbkqZNNCC2tMyxFKiiH5jHXuQ8Upump | 0.010000000 | 0.008890572 | -11.094% | -0.001109428 | 0.000010 | 0.001887234 | TP | +0.002000000 | -0.003109428 | incident recovery | INCIDENT |
| 2 | 4gnZN6jvRNnr6tqURLGv7pQNNDEUarUvHxJckhdqpump | 0.010000000 | 0.009141110 | -8.589% | -0.000858890 | 0.000010 | 0.001887234 | MAX HOLD | -0.000243416 | -0.000615474 | max hold | clean |
| 3 | 2BPgWpJMGiJRZ8H5AwBhNiu6vKBnpHLMPirRApAZpump | 0.010000000 | 0.009478944 | -5.211% | -0.000521056 | 0.000010 | 0.001887234 | MAX HOLD | -0.000539849 | +0.000018793 | max hold | clean |
| 4 | 92sPgFWFQwa1iJGurG7qVBEpaSpY5fRnTEw8owbBpump | 0.010000000 | 0.008846517 | -11.535% | -0.001153483 | 0.000010 | 0.001887234 | MAX HOLD | -0.001631173 | +0.000477690 | max hold | clean |
| 5 | DK6WhnWu6RCRPL6fBWrvKsMJCGtN5hq1LtteBxgzpump | 0.010000000 | 0.009746451 | -2.535% | -0.000253549 | 0.000010 | 0.001887234 | MAX HOLD | -0.000583141 | +0.000329592 | max hold | clean |
| 6 | 2Yscwn7gbbu4XbJpZCdZAg1qfVQvftJLSFzaDsybpump | 0.010000000 | 0.009669737 | -3.303% | -0.000330263 | 0.000010 | 0.001887234 | MAX HOLD | -0.000265786 | -0.000064477 | max hold | clean |
| 7 | 44YNLZVraBdmeL251o9k6nnRjj3cep33KvK1Xfzjpump | 0.010000000 | 0.008698486 | -13.015% | -0.001301514 | 0.000010 | 0.001887234 | MAX HOLD | -0.000110609 | -0.001190905 | max hold | clean |
| 8 | GKeLzTCxPniARfmaw5nF2xczwWnanWnvrhjVUm3tpump | 0.010000000 | 0.012323871 | +23.239% | +0.002323871 | 0.000010 | 0.001887234 | MAX HOLD | +0.000309152 | +0.002014719 | take profit | clean |
| 9 | AKxXdkkng2TgeLYKWqJrUmto5vL3sZuNjDh8mHyNpump | 0.010000000 | 0.009416294 | -5.837% | -0.000583706 | 0.000010 | 0.001887234 | MAX HOLD | -0.000287330 | -0.000296376 | max hold | clean |
| 10 | Ejse6UzJZn32W4AXJtf8t9Q2XMCXRGHK22uUwB36pump | 0.010000000 | 0.008699188 | -13.008% | -0.001300812 | 0.000010 | 0.001887234 | MAX HOLD | -0.000297574 | -0.001003238 | max hold | clean |
| 11 | 7Xzdw3wmjPpCAv4EKkWLiLyvgMY72LBL64opo878pump | 0.010000000 | 0.008076084 | -19.239% | -0.001923916 | 0.000010 | 0.001887234 | MAX HOLD | -0.000243416 | -0.001680500 | max hold | clean |

Closeout totals:

- First 10 resolved organic: `1W/9L`, WR `10%`, PF approximately `0.28`,
  strategy PnL `-0.005903318 SOL`.
- Same-mint frozen shadow for those 10: `-0.003893142 SOL`.
- Ordinary live minus shadow: `-0.002010176 SOL`.
- Incident recovery strategy PnL: `-0.001109428 SOL`.
- All 11 strategy-economic PnL: `-0.007012746 SOL`.
- All 11 wallet cashflow: `-0.027882320 SOL`.
- Total network fees: `0.000110000 SOL`.
- Locked ATA/rent and other entry account effects: `0.020759574 SOL`.

This N=11 is too small for a Ranker alpha verdict, but the live/shadow degradation
and lifecycle failure are materially concerning and justify a fresh cohort.

## 19. Live-vs-Shadow Degradation

For the 10 ordinary positions, entry was not the dominant leak:

- Entry token-output degradation median: `-2.776%` (negative means actual output
  was better than pre-sign expected output).
- Implied live entry MC versus frozen shadow median: approximately `-2.701%`.
- First sellable proof median/P90: `260.5ms / 439.3ms`.
- Exit mix: nine max-hold and one live TP.
- Sell first-send-to-finality median/P90: `351ms / 482.8ms`.

The `-0.002010176 SOL` ordinary gap is concentrated in differences between modeled
120-second marks and actual executable sell values, partly offset by the one live TP.
The largest negative same-mint differences were `7Xzdw...` (`-0.001680500`),
`44YNL...` (`-0.001190905`), and `Ejse6...` (`-0.001003238`). The incident adds a
separate `-0.003109428 SOL` versus its frozen shadow TP and must not be counted as a
strategy win.

## 20. N25-v3 Readiness

Staged release:

`/mnt/HC_Volume_106775198/edgesniffer-bin/pumpfun-sidecar-ranker-v2-lifecycle-repair-v3-7992d04956cdc56d`

SHA-256:

`7992d04956cdc56d5edadc92836d2da5724e475f1a6865c88ad7eda01b9b8724`

Staged units:

- `edgesniffer-ranker-v2-n25-tiny-live-v3.service`: inactive, disabled, PID 0,
  NRestarts 0.
- `edgesniffer-ranker-v2-n25-tiny-live-v3-emitter.service`: inactive, disabled,
  PID 0, NRestarts 0.
- No v3 state or audit file exists; the cohort is truly fresh `0/25` and never run.

N25-v2 is unchanged at 11/25. Its emitter is inactive/disabled and no new buy occurred.
The source, normalizer, observation emitter, canonical scorer, and translation mirror
are current. The source feed's cumulative `NRestarts=16679` came from the earlier
wrong-feature binary loop; the corrected process has remained on one PID since
`2026-09-05T17:17:17Z`, while downstream components show zero restarts.

Storage is the only operational warning: root has about `49GB` free, while the data
volume has about `3.1GB` free (`97%` used). The canonical corpus is currently growing
at roughly `5.2GB/day` across its three primary JSONL files. V3 writes are staged on
root, so this does not invalidate lifecycle readiness, but volume expansion or safe
rotation is required before a long unattended run.

No N25-v3 start has been performed. Starting it requires a separate explicit
authorization and a final flat-wallet/pre-start storage check.
