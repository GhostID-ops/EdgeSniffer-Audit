# kEFi PumpSwap Multi-Mint Decoder Repair V1

Status: `DECODER_MAPPING_STILL_AMBIGUOUS`

## What was repaired

The observer decoder now uses an explicit, evidence-backed account-layout table
instead of transaction-wide balance heuristics:

| Variant | Pool | User | Base mint | Quote mint | Minimum accounts |
| --- | ---: | ---: | ---: | ---: | ---: |
| `buy` | 0 | 1 | 3 | 4 | 23 |
| `buy_exact_quote_in` | 0 | 1 | 3 | 4 | 23 |
| `sell` | 0 | 1 | 4 | 3 | 21 |

The SELL reversal was established from the retained routed fixture: account 3
is wrapped SOL and account 4 is the traded PumpSwap mint. The implementation
accepts an event only where the event side and pool map to exactly one layout
base mint; duplicate outer/inner views with the same mint collapse safely.

Local and VPS regression tests passed for deterministic state behavior,
multiple invocations, duplicate invocation views, ordinary BUY, ordinary SELL,
and ambiguous mappings failing closed.

## Exact retained fixtures

The repair retained the full protobuf source update and decoded structural
metadata for the observed failures. The latest fixture is:

`/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/validation-choice-state-v1-1791874084712175782-pid765518/failed-layout-fixture.json`

The original observed routed SELL fixture established the reversed mint order:

`/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/validation-choice-state-v1-1791874004078253359-pid764571/failed-layout-fixture.json`

## Remaining proven blocker

The latest exact fixture has a PumpSwap SELL event, but the current instruction
extractor emits **zero recognized canonical PumpSwap BUY/SELL invocations** for
that transaction. Therefore the observer has no structural invocation to bind
to the event.

This is not merely a multi-mint conflict. The missing fact is the routed
transaction's actual PumpSwap instruction discriminator/account layout (or a
reliable CPI stack relationship connecting the event log to that instruction).
Until that variant is decoded into an invocation with pool/base-mint positions,
there is no provable event -> invocation -> mint chain.

No additional balance-delta fallback was added. The bounded live observer
remains failed closed with zero choice snapshots, rather than skipping the
event or writing a biased candidate universe.

## Isolation and live health

- V8 capture-first source: active.
- PRE_H3 qualification timer: active.
- No PRE_H3 artifact, outcome, evaluator, or PnL input was opened.
- No candidate, snapshot, or selection model was run.

## Next permissible work

Decode the exact routed instruction discriminator from the retained protobuf
fixture and establish its canonical account layout from protocol evidence. Add
that layout as a named supported variant with a fixture regression. Do not
restart choice-state capture before that proof exists.

`DECODER_MAPPING_STILL_AMBIGUOUS`
