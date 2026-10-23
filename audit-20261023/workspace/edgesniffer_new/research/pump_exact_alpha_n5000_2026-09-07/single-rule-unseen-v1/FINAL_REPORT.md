# Frozen single-rule unseen check

Verdict: `FAILS_UNSEEN`

Rule evaluated—exclusively:

`FIXED_25MS AND firstBuyerShareBps <= 360.40437631955825`

The readiness endpoint was frozen before unseen outcomes were read. `END=6848`, from readiness metadata generated at `2026-09-07T15:09:02.419074Z`, SHA-256 `7a55ebd60fcb5ab88b45e48fd5be2e7bcd3763648c6e4abb7a80e9584f18b684`. The evaluation range is exactly ordinals 5001–6848. It contains 1,848 launches; 1,815 have the FIXED_25MS row, and 82 unique mints match the rule. No other rule was searched, ranked, evaluated, or retuned.

The frozen verdict rule required at least 30 matches overall and 10 in each half. `PERSISTS_UNSEEN` additionally required PF>1, expectancy>0, and PnL>0 at both sizes in aggregate and in both halves. Sample sufficiency passed; persistence failed.

## Exact-label result

| Scope | Match N | Pass N | 0.5 SOL: PF / expectancy / PnL | 1 SOL: PF / expectancy / PnL |
|---|---:|---:|---|---|
| Ordinals 5001–6848 | 82 | 75 / 75 | 1.0196 / +0.1149% / +0.043103 SOL | 0.5632 / −3.2784% / −2.458826 SOL |
| First half, 5001–5924 | 38 | 33 / 33 | 0.9846 / −0.0893% / −0.014743 SOL | 0.4340 / −4.4053% / −1.453759 SOL |
| Second half, 5925–6848 | 44 | 42 / 42 | 1.0467 / +0.2755% / +0.057846 SOL | 0.6716 / −2.3930% / −1.005067 SOL |

The 0.5 SOL result is effectively break-even and fails the first half. The 1 SOL result fails strongly in aggregate and in both halves.

## Exact break-even delivery cost

The label already deducts 0.000010 SOL total fixed network fees per completed trade: 0.000005 SOL on entry and 0.000005 SOL on exit.

| Size | Additional cost beyond label that reaches zero | Total delivery cost including label base fees that reaches zero |
|---:|---:|---:|
| 0.5 SOL | 0.0005747127067 SOL/trade | **0.0005847127067 SOL/trade** |
| 1 SOL | No non-negative break-even cost | Mathematical value −0.0327743479733 SOL/trade; this would require a 0.0327743479733 SOL subsidy, not a cost |

For 1 SOL, expectancy is already negative before any missing delivery charge, so zero delivery cost cannot rescue it.

## What the exact labels include

This is proven from the frozen production code, not inferred:

- `/root/bot/EdgeSniffer/scripts/buildPumpExactAlphaResearchV1.py`, SHA-256 `3a8a6d3ba4c743c644a5120b4e1d9da016513e43447102ce2dd74716a9c572ad`, defines `NETWORK_FEE = 5_000`; entry cost is `gross + NETWORK_FEE`; exit proceeds are `immediate_quote_out - NETWORK_FEE`; `pnlSol` and `returnPct` use only those values.
- `/root/bot/EdgeSniffer/scripts/exactCurveEconomicsV1.py`, SHA-256 `badf8d417a155931498422300ffadb5ba1b3e3574bbcad9fd62d52161144ff10`, includes Pump protocol, creator, and LP fees in the exact curve quotes.
- Although transaction evidence parses RPC `meta.fee`, that actual network fee is not supplied to the label PnL calculation.

Therefore:

| Cost | Included? |
|---|---|
| Pump curve protocol/creator/LP fees | Yes |
| Network base fees | Yes, modeled as fixed 5,000 lamports per side |
| Priority fees | No |
| Jito tips | No |
| Helius tips | No |
| New-mint ATA cost/rent | No |

## Installed production delivery-cost policy

There is currently no running submit-capable trading service. The cost policy below is the current installed and enabled Pump submitting-unit policy; its reference unit was observed inactive and was not started or changed.

Code/config evidence:

- Production main SHA-256: `1a60903c240489e848390707a2becb4e80600d49d5c84de646cce71e3e83782e`.
- Production executor SHA-256: `7850ef1c9ab9c013f0bd68f0219475ea91e36f4d5fcc10313a93bf3f5c1cec23`.
- Effective buy policy: 400,000 CU at 18,000,000 micro-lamports/CU = 0.0072 SOL priority fee; 0.0100 SOL Jito tip; 0.0015 SOL Helius tip.
- Effective sell optional delivery fees: zero.
- Both Jito-direct and fallback are enabled; executor code places both configured transfers in its first compiled tip set.
- Fixed base fee model: 0.000010 SOL round trip.
- Irreversible round-trip delivery cost: **0.018710 SOL/trade**.
- The Pump.fun buy builder always adds idempotent creation of the user's mint ATA. A finalized mainnet `getMinimumBalanceForRentExemption(165)` query returned 1,855,569 lamports. Including that first-trade capital lock gives **0.020565569 SOL** cash outlay. ATA rent can be recoverable if the account is later closed and Token-2022 extension sizing can differ, so it is shown separately.

The installed 10%-of-buy optional-fee cap does not bind at either 0.5 or 1 SOL.

## Cost-adjusted results

All costs below are **total execution/delivery cost per completed trade**. Because the exact label already contains 0.000010 SOL, only the amount beyond that was subtracted again.

| Total cost policy | 0.5 SOL: PF / expectancy / PnL | 1 SOL: PF / expectancy / PnL |
|---|---|---|
| Installed irreversible policy, 0.018710 SOL | 0.5569 / −3.6250% / −1.359397 SOL | 0.4113 / −5.1484% / −3.861326 SOL |
| Installed first-trade cash cost incl. 165-byte ATA, 0.020565569 SOL | 0.5256 / −3.9961% / −1.498564 SOL | 0.3989 / −5.3340% / −4.000494 SOL |
| 0.005 SOL stress | 0.8625 / −0.8830% / −0.331147 SOL | 0.5170 / −3.7774% / −2.833076 SOL |
| 0.010 SOL stress | 0.7329 / −1.8830% / −0.706147 SOL | 0.4746 / −4.2774% / −3.208076 SOL |
| 0.020 SOL stress | 0.5350 / −3.8830% / −1.456147 SOL | 0.4027 / −5.2774% / −3.958076 SOL |

## Stop state

Final verdict: `FAILS_UNSEEN`.

No feature work, 2CQ analysis, scanner, validation/holdout inspection, service change, deployment, shadow trade, or live trade occurred. Stop for review.
