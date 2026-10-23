# Minimal research ledger

This directory is tracked at the Git repository root. It is offline research
code with no order submission path. The canonical records are
[tasks](../../RESEARCH_TASKS.md) and [decisions](../../RESEARCH_DECISIONS.md).

## Implemented slice

`ledger_v1.py` retains the first observed buy per mint as a coverage seed, before
evidence filtering. Each seed gets the same three notional scenarios. It exposes
missing clocks, state, fees, execution evidence and identity conflicts. It never
replaces the first seed with a later better-observed buy. Input is read only to
the byte size observed at startup. Incomplete last lines are explicit rejects;
each run directory must be new. File hashes identify the exact consumed prefix.

This is not yet a burst policy or an economics simulator. All seeds are blocked
until a validated processed-state adapter is wired. Unknowns are retained without
assigning zero PnL. Adding fake flags cannot enable quoting. The prospective
strategy will need a separate frozen seed rule, source scope and warmup contract.
The census maintains identities in memory and is intended for bounded snapshots,
not an unbounded production collector.

From the repository root:

```
python -m unittest discover -s research/edgesniffer -p test_ledger_v1.py -v
python research/edgesniffer/ledger_v1.py --input INPUT.jsonl --output NEW_RUN_DIR --population-id UNIQUE_POPULATION_ID
python research/edgesniffer/inventory_sources_v1.py --root SOURCE_DIRECTORY
```

## Actual evidence

- [A1 census](evidence/ledger-a1-20261022-v1.json): 9571 rows, 1065 seeds, 3195
  attempt scenarios. All blocked; 254 seeds nonmonotonic; 2 missing complete
  reserve fields. This is a native-replay sample, not the full eligible market.
- [A2 retained-source inventory](evidence/source-inventory-a2-20261022-v1.json):
  bounded tail samples show account events already carry real reserves, completion,
  writeVersion, accountTxnSignature and receive/decode clocks. Transaction receipt
  rows have signature/route identity and clocks but omit success and executed
  amounts. One fee snapshot has a capture clock and raw config; it does not prove
  fee validity throughout a later sample. Keys present do not establish nonnull
  values, correct decoding, full subscription coverage or successful state joins.

The local receipt normalizer in the prior workspace explicitly selects only
receipt fields (`materializeNativeYellowstoneTransactionBridgeV1.py`), so absence
in that derived file does not establish absence in raw provider metadata.
Investigate the original emitter and effective subscription before collecting
again. Do not infer executed amounts or success from receipt timestamps.

## Processed-evidence join diagnostic (A3a)

`processed_evidence_join_v1.py` snapshots bounded inputs, retains a result for
every receipt and requires signature/slot/curve identity, original receipt
provenance, complete reserves/phase and account receive/decode clocks no later
than receipt +1 second. Distinct write versions, conflicting payloads, invalid
clocks and nonmonotonic account observations remain unresolved. A match is only
an identity/clock diagnostic: it never proves success, signed flow, current entry
state, fees, common clock epoch, fork validity or economic admission.

The ledger integration preserves all identifiable first-buy seeds and three
sizes before state filtering. Missing mint/side cannot be invented. Receipt
results are retained even when no buy seed is identifiable. Field presence alone
is not proof of values: every normalized receipt in the real sample has curve,
mint and side keys but null values.

```
python -m unittest discover -s research/edgesniffer -p "test_*.py" -v
python research/edgesniffer/processed_evidence_join_v1.py --source-dir SOURCE_DIRECTORY --output NEW_RUN_DIR --population-id UNIQUE_ID
python research/edgesniffer/processed_evidence_join_v1.py --source-dir PRIOR_RUN_DIR --sealed-inputs --output NEW_REPLAY_DIR --population-id SAME_ID
```

20 tests pass locally and on VPS, including actual CLI/ledger integration on
synthetic complete and incomplete states. The real diagnostic was replayed from
sealed inputs: identical joins and ledger hashes in all three runs. Real data
had no qualifying exact-curve receipts, so successful real economic/state
admission is NOT tested. No collector deployment or live execution occurred.

- [Initial capture manifest](evidence/processed-join-a3a-20261022-initial.json)
  preserves original source byte ranges and hashes.
- [Final verified report](evidence/processed-join-a3a-20261022-v2.json) also pins
  implementation hashes: 10364 receipts, all target-wallet kind; no nonnull
  mint/side/curve/instruction index; no identifiable buy seeds. Sample has 80680
  raw account states, 94 target receipts, 27 stats rows. Zero admitted trades is
  a data-contract result, not wallet inactivity or strategy performance.
- The ledger's 9988 `non_buy_rows` include unknown sides, not known sells; 376
  identity conflicts are repeated-identity/different-content receipt records,
  not counts of additional trades. All 10364 inputs remain in `joins.jsonl`.
- [Runtime manifest](evidence/observer-provenance-a3a-20261022-v1.json): actual
  observer is under `/root/bot/FinalBot-cya`, not the historical EdgeSniffer
  directory. Running/on-disk binary hashes match; effective submit=false.
  Adjacent source emits sparse target receipts. Reproducible source-to-binary
  mapping remains unproven; no claim of complete source/binary attribution.
- [Additional bounded inventory](evidence/additional-inventory-a3a-20261022-v1.json)
  finds raw instructions in teacher tape but no execution metadata; sampled
  market-wide normalized rows have no execution object/status evidence.

Durable VPS artifact directory:
`/mnt/HC_Volume_106775198/edgesniffer-research/offline-ledger-a3a-20261022-v1`.
`run1` preserves initial snapshots; `run2` adds field-value counts; `final-code`
and `run3` preserve the final verified implementation and replay. Earlier
versions remain intact. Small reports and code are tracked here, not solely in
the ignored workspace. Raw snapshots are not committed to Git.

## A3b: completed bounded native transaction capture

Historical enrichment was checked before expanding capture. The 10364 receipts
contain 9988 signatures. A 32-signature outcome-blind RPC pilot recovered all 32;
one archived hour matched 213 raw signatures, with metadata in 5. These are
capability samples, not full recovery. `recover_receipts_v1.py` preserves original
receipt clocks and records later RPC availability separately; null/errors remain
unknown. `check_retained_metadata_v1.py` reuses the existing protobuf normalizer.

`pump_capture/` is a standalone receive-only Rust executable using the existing
Yellowstone/prost transport pattern. It captures the Pump-program universe only,
including CPI and failed/unknown executions, with no teacher or mint shortlist.
Raw protobuf, hashes, transaction identities, ALT lookups/loaded addresses,
outer/inner instruction coordinates, status/errors, balances, logs, fees and
return data are preserved. Session, sequence, connection and slot notifications
make interruptions measurable. It has no signing/submission code and does not
load wallet keys. `Cargo.lock` pins the tested dependencies.

See [capture contract](pump-capture-contract-v1.json) for scope and clocks.
Receive time means application dequeue, not NIC arrival. Provider creation,
chain timestamps, native normalization, later finality and historical recovery
are not interchangeable. The offline Python view preserves whole-transaction
balance changes without assuming they are swap fills; known TradeEvent quote
layouts expose actual event amounts while unknown extensions remain opaque.
Logs and event-CPI payloads are reconciled one-to-one without double counting.

The first launch failed on TLS provider selection; preserved as `canary1`.
Explicit ring provider selection and a regression fixed startup; `canary2`
completed 120 seconds. [Verified quality report](evidence/pump-capture-quality-20261023-v2.json):
45825 native transactions, 5056 SOL-flow tx; all 5056 joined to account writes,
5033 by the one-second boundary. In sixteen sampled finalized blocks, all 923
expected transactions were captured with matching status/accounts/balances/fee/
event data. This is sampled evidence, not global completeness or profitable PnL.

Tests: 33 Python tests passed locally and on VPS, 5 Rust tests on VPS, locked
release build, real canary and frozen-input replay. `quality2` and the final
`quality3-offline-replay` have identical transaction-view and account-join hashes:

- transactions: `9b725daf06ba54c371bef928614701c0da494ddb694099b7a7ecdd23d55f66b5`
- account joins: `565343ad17862338ad2170d1d1d63287b3cb7bcf131424a3962844731dfc4a08`

Artifacts, raw data and the verified executable are preserved on VPS at:
`/mnt/HC_Volume_106775198/edgesniffer-research/transaction-evidence-v1-qvxRqmv3`.
The canary is finished; no permanent new service was installed. Existing collectors
and trading settings were untouched. A new run requires a new output directory.

Commands (from Git root; capture and actual Rust build verified on Linux VPS):

```
python -m unittest discover -s research/edgesniffer -p "test_*.py"
cargo test --locked --manifest-path research/edgesniffer/pump_capture/Cargo.toml
cargo build --locked --release --manifest-path research/edgesniffer/pump_capture/Cargo.toml
python research/edgesniffer/recover_receipts_v1.py --receipts SEALED_RECEIPTS --output NEW_RECOVERY_DIR --rpc-env PRIVATE_ENV --limit 32
python research/edgesniffer/run_capture_v1.py --binary CAPTURE_BINARY --env-file PRIVATE_ENV --output-dir NEW_CAPTURE_DIR --seconds 120
python research/edgesniffer/capture_quality_v1.py --input CAPTURE_JSONL --output NEW_AUDIT_DIR --accounts ACCOUNT_JSONL --rpc-env PRIVATE_ENV --block-limit 16
```

Do not put endpoint credentials on the command line or commit environment files.
`PRIVATE_ENV` is an existing private file with `SHYFT_GRPC_URL`,
`SHYFT_GRPC_X_TOKEN`, and `SECURE_RPC_URL`. Only needed fields are read.
Keep bounded captures until retention/experiment duration is explicitly chosen;
this canary produced ~564MB in 120s, and audit snapshots are additional copies.
The default audit takes at most 1GiB of capture input and 128MiB of account input;
inspect byte ranges before using a longer run. Truncated-history samples do not
prove full-session coverage. Do not silently scale this into unbounded capture.

### Earliest next experiment

A3c: materialize first independent burst seeds, then compare the existing burst
and two-second persistence hypotheses against same-clock Pump SOL non-signals.
Use native availability, keep rejected/unresolved seeds and three-size intents.
First measure +1s/+2s price/flow continuation; this narrower falsification does
not need claims about executable net PnL. Do not retune thresholds to this canary.
Net economics still requires current-state continuity, full fee schedule,
own-state impact and all-attempt accounting. Prospective strategy confirmation
requires a later fresh cohort after policy selection/freezing.

Protocol references used for the source contract:
[Solana getTransaction](https://solana.com/docs/rpc/http/gettransaction),
[Solana JSON transaction structures](https://solana.com/docs/rpc/json-structures),
[Yellowstone upstream filter example](https://github.com/rpcpool/yellowstone-grpc/blob/master/examples/rust/src/bin/client.rs).

## Fixed independent burst/persistence diagnostic

`burst_persistence_v1.py` implements the T02/T03 rule cards using only successful,
known non-Mayhem Pump SOL events at **metadata availability**, not block time or
later RPC confirmation. Both arms retain the same original first-per-mint seed.
The persistence arm confirms/rejects/retains unknown at +2s. Entry is +1s after
each arm's decision; exit is entry+15s hold+1s delivery. No exits were optimized.

Controls are other currently active non-burst mints in the same virtual-quote
reserve bucket, selected deterministically at the seed clock. Later signals do
not disqualify controls. Reuse, no-match, missing marks and rejected seeds are
reported. This is an observational comparison, not random treatment assignment.

Prices are the latest observed event reserve ratio, with a fixed maximum age
of1s. They are **not executable quotes** or proof that reserves remained current.
Missing/stale/right-censored prices stay missing. Three notional sizes are kept
as intentions only; no linear price-return-to-PnL scaling. Rejects' zero values
are explicitly abstention proxies, not filled trades or evidence of an effect.
The bootstrap is descriptive; few time periods and reused controls limit inference.

```
python research/edgesniffer/burst_persistence_v1.py --source VERIFIED_NATIVE_VIEWS --output NEW_RESULT --population IMMUTABLE_POPULATION_ID
python research/edgesniffer/failure_diagnostic_v1.py --source VERIFIED_NATIVE_VIEWS --reference SEALED_FINALIZED_REFERENCE --output NEW_FAILURE_REPORT
python research/edgesniffer/run_burst_observation_v1.py --capture-code VERIFIED_CAPTURE_CODE --binary VERIFIED_CAPTURE_BINARY --env-file PRIVATE_ENV --output NEW_BATCH --sessions 8
```

The batch runs8 fixed120s receive-only captures, auditing each complete session
before analysis. It does not alter existing services. Truncated snapshots, writer
gaps, bad clocks/hashes, decoder gaps or sampled metadata mismatches stop admission.
It preserves rejected files and stops for operational safety, never based on PnL.
Sessions are separate (including their gaps); repeated mints cannot inflate the
aggregate first-mint sample. Reserve16GiB free before each session. No data deletion.

Initial canary result:18 seeds/18 mints,108 research intentions,7 observed burst
returns and just3 complete control pairs. Persistence confirms3, rejects12,
leaves3 unknown;2 observed admitted returns and no admitted complete control pair.
See `evidence/burst-persistence-canary-20261023-v1.json`. No strategy conclusion.

Failure diagnostic:38,775 failed transactions are not duplicate-signature inflation.
Top10 payers account for64.7%;65.3% belong to repeated payer/instruction-payload
groups. Failed attempts are never buying/selling flow. Native error bytes and
intent are separate from later reference labels; mint account mentions are not
asserted to be the attempted trade's mint. See `evidence/failure-diagnostic-20261023-v1.json`.

Next evidence requirement:100 unique seed mints and30 time blocks is an engineering
coverage floor, not a powered validation claim. Missing-mark bias and execution
accounting must be resolved before inferring repeatable after-cost profitability.

### Additional observation completed

Eight fixed120s sessions produced157657 transactions and78 first-mint signals;
50 repeated-mint signals remain preserved but do not inflate the independent
asset count.38 seed-time blocks; the initial100-mint floor was not reached.
There are explicit intersession gaps. No ongoing new collector was installed.
`verify_burst_batch_v1.py` replays every session and requires identical opportunity
and attempt hashes; all8 passed.32 sampled finalized blocks matched1178 expected
transactions and their metadata. Tests:59 Python tests locally and on VPS.

`event_continuation_v1.py` is a separately declared retrospective endpoint:
two seconds of executed Pump SOL flow after each arm's unchanged delayed entry.
It improved paired coverage from7 burst price pairs to35 flow pairs without
inventing prices. Burst excess signed flow+0.9365 SOL is **market flow, not profit**.
Persistence's11 admitted pairs show mean excess flow-0.4809 SOL. No strategy selected.

```
python research/edgesniffer/verify_burst_batch_v1.py --batch FINISHED_BATCH --output NEW_VERIFY_DIR --prior-opportunities CANARY_OPPORTUNITIES
python research/edgesniffer/event_continuation_v1.py --batch FINISHED_BATCH --output NEW_ACTIVITY_DIR
```

Full raw inputs and immutable results are preserved on VPS under
`/mnt/HC_Volume_106775198/edgesniffer-research/burst-persistence-v1-WmlstZu7`.
The original batch used the frozen pre-source-guard script; final guarded-source
replay produced identical decisions/attempts. Both implementations are retained.
Tracked reports: `evidence/burst-persistence-fresh-20261023-v1.json` and
`evidence/event-continuation-fresh-20261023-v1.json`. Next: causally join already
retained native account state at unchanged boundaries; retain missing/ambiguous
state rather than blindly forward-filling a profitable-looking outcome.

### Canonical native-account audit

`account_boundary_audit_v1.py --web3-module EXISTING_WEB3_PACKAGE` joins retained
native accounts only to the mint's deterministic curve address, using
`derive_pump_curves_v1.cjs` offline and the [official Pump IDL](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json)
PDA seeds. No SDK installation, RPC, signing or transaction building is performed.
Loose signature/key membership admitted unrelated accounts; canonical identity
excluded28424 such rows.15503 unique selected event transactions then matched
all four account reserves. This does not prove universal capture or quote parity.

```
python research/edgesniffer/account_boundary_audit_v1.py --batch FINISHED_BATCH --source NATIVE_ACCOUNT_FILE --output NEW_ACCOUNT_AUDIT --web3-module EXISTING_WEB3_PACKAGE
```

The bounded256MiB snapshot is immutable and covers the cohort. Canonical observed
state exists for all78 targets and36 original controls, but45 target exit states
are older than1s. No primary result was replaced; no carry-forward fill was created.
Full fee/mint-extension context and account-stream continuity remain unverified.
See `evidence/account-boundary-canonical-20261023-v2.json`. Tests now65 local/VPS;
set `TEST_WEB3_MODULE` to the existing SDK package path when running the PDA test
outside the repository's normal node_modules layout. No tests skipped on either run.
