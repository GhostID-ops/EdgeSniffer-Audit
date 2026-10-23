# Pump bonding-curve sealed discovery report

Status: exploratory research only. This report does not promote a strategy, alter the prospective policy, or enable trading.

## What the fresh cohort is testing

The fresh cohort asks one narrow question:

> When a new, exact Pump bonding-curve top-level buy by wallet `2cq` is observed, does a pre-registered fixed `0.5 SOL` entry, with the real one-second delay and a fixed five-second exit, retain positive net economics under an additional 50 bps stress assumption?

The cohort is separate from the sealed discovery snapshot. It starts at boundary `2026-10-20T03:45:47Z` and cannot use earlier source rows. It does not search exits, sizes, filters, or routes. That separation prevents selecting a rule from the same observations used to judge it.

The policy under test is `PUMP_TOP_LEVEL_0_5_SOL_5S_STRESS50_V1`:

- exact Pump `TradeEvent`, tracked wallet is the buyer;
- top-level Pump invocation only;
- one-second entry delay;
- fixed `0.5 SOL` notional;
- fixed five-second exit;
- extra 50 bps stress in the venue fee model on buy and sell;
- `0.00001 SOL` round-trip network-fee assumption;
- same-period, same-mint/quote, non-wallet Pump BUY controls;
- no dynamic selling, partial take-profit, trading, or live execution.

## Why two wallet outcomes are too small

The first checkpoint has two actionable wallet observations. That means the displayed PnL is only the result of two trades—one win and one loss at the checkpoint—not evidence about the underlying rule. A win rate, PF, or total PnL can move dramatically with the next few observations. The preregistered gate therefore requires at least 30 actionable wallet outcomes and 30 actionable controls before review. The two observations are retained; they are not discarded or used to tune the policy.

## Sealed discovery surface

The sealed snapshot is cohort `2cq-new-wallet-pump-curve-windows-20261020T000000Z`, sealed at `2026-10-20T02:52:56Z`. It contains 657 exact buys/windows: 656 top-level and 1 CPI, plus 53,850 market transactions and 1,269 separately materialized controls. The surface itself has controls marked `NOT_ATTACHED`, so these are descriptive discovery results, not validation.

At 50 bps stress, Pump top-level results were:

| Notional | Exit | Actionable / attempted | PnL SOL | PF | Win rate | Max DD SOL |
|---:|---:|---:|---:|---:|---:|---:|
| 0.1 | 5s | 355 / 656 | 2.8811 | 1.93 | 56.9% | 0.3319 |
| 0.5 | 5s | 350 / 656 | 10.5361 | 1.65 | 52.6% | 1.8214 |
| 1.0 | 5s | 330 / 656 | 12.6898 | 1.37 | 48.8% | 4.4729 |
| 0.5 | 10s | 345 / 656 | 7.9646 | 1.45 | 47.5% | 1.7153 |
| 0.5 | 15s | 327 / 656 | 7.1117 | 1.41 | 44.6% | 1.4885 |
| 0.5 | 30s | 300 / 656 | 3.8890 | 1.21 | 38.7% | 1.8404 |
| 0.5 | 60s | 245 / 656 | 1.0284 | 1.06 | 38.8% | 2.3257 |
| 0.5 | 120s | 190 / 656 | -1.3589 | 0.92 | 37.4% | 4.3126 |

At 0.5 SOL, the five-second exit had the highest PnL, PF, and win rate among the listed fixed exits. At five seconds, 1.0 SOL produced more gross PnL than 0.5 SOL but lower PF and materially larger drawdown. The 0.1 SOL case had the best PF and smallest drawdown, but much lower absolute PnL.

The 300-second rows have zero actionable exits because the captured window is 300 seconds while the evaluator includes the one-second entry delay; the target can fall just beyond the captured window. Those rows are coverage-limited and are not evidence that a 300-second strategy loses.

## What 50 bps stress means

50 basis points equals 0.50%. In this evaluator it is an explicit additional stress fee (`lp_bps=50`) applied to both the simulated buy and sell quote. It is not a measured external slippage distribution and it is not a claim that the chain charged exactly 50 bps. It is a robustness test for fee/slippage-like execution friction. The sealed discovery surface used `0.000005 SOL` network fee per round trip; the new prospective policy preregisters the more conservative `0.00001 SOL` assumption.

## Fingerprints and leads in the sealed trades

The sealed trades show these repeatable descriptive features:

1. **Route/mechanic fingerprint.** All 656 top-level entries use the exact `buy_exact_quote_in` Pump curve event. The one CPI event is a separate route and produced zero actionable rows across the surface. Every top-level event had 95 bps protocol fee, 50% buyback allocation, and `mayhem_mode=false`. Creator fee rates varied and must remain part of the exact decoder/evaluator.

2. **Native-SOL subset.** Of the 656 top-level events, 501 used the native SOL quote mint and 155 used other quote mints. The prospective policy is explicitly SOL-only; non-SOL rows must not be silently treated as SOL trades.

3. **Entry-stage fingerprint.** For the 501 native-SOL top-level entries, the implied post-entry market-cap proxy had median approximately `49.4 SOL`, p10 `33.2 SOL`, and p90 `118.2 SOL`. This uses the Pump reserve ratio and the standard 1 billion-token supply convention; it is not an external market-cap lookup.

4. **Lower-MC lead.** At `0.5 SOL / 5s / 50 bps`, the native-SOL top-level rows grouped descriptively as follows:

| Implied MC band (SOL proxy) | Attempted | Actionable | PnL SOL | PF | Win rate |
|---:|---:|---:|---:|---:|---:|
| 25–40 | 144 | 128 | 5.7816 | 4.22 | 54.7% |
| 40–50 | 109 | 91 | 5.3112 | 2.32 | 56.0% |
| 50–75 | 117 | 76 | 1.8662 | 1.43 | 51.3% |
| 75–100 | 53 | 30 | -1.7177 | 0.57 | 40.0% |
| 100–150 | 49 | 22 | -0.6227 | 0.69 | 50.0% |

This is the strongest current filtering lead: earlier/lower implied MC appears more favorable, while later/higher MC appears weaker. It was discovered inside the sealed cohort, so it is not allowed to modify the current prospective rule. A lower-MC filter must be preregistered as a separate hypothesis and tested prospectively.

5. **Timing fingerprint.** The five-second exit decays into weaker economics at 10/15/30/60 seconds and becomes negative at 120 seconds under 50 bps for 0.5 SOL. This points toward a short impulse/scalp hypothesis rather than a long fixed hold, but does not establish the correct dynamic sell rule.

## Interpretation

The sealed evidence gives us a real lead with three parts: exact top-level Pump route, native-SOL subset, and an apparent lower-implied-MC/short-horizon concentration. The current prospective test intentionally tests only the first two plus the fixed 0.5 SOL/5-second rule. It does not yet test the lower-MC filter, dynamic selling, or size adaptation.

The next research decision is made only after the fresh cohort reaches its sample gate. If the frozen rule survives against controls, the next separate hypothesis should be a preregistered lower-MC filter and/or dynamic exit, tested on a new cohort—not retrofitted into this one.

## Evidence references

- Sealed manifest SHA-256: `4a5b5508475adf818868796f7fef9f5706cd987727ca99f750b1b552f6753bcb`
- Sealed analysis SHA-256: `a1cdc0dc1f0d9affd41d12f7c1981064ef8dfa4cddee114ce43e56e47e747cc3`
- Prospective policy artifact SHA-256: `4aa3e370eb4f42dd6b87d2536bd79e627dec6964d0a30fa98863d4f35fb88689`
- Live execution: disabled
