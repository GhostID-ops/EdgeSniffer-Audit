# Frozen 2CQ Paper Lane Runbook

## Purpose

This is a separate paper-only EdgeSniffer lane derived from the validated 2CQ
flow projection. It consumes the existing local stream but does not share
filters, pending confirmations, exposure, positions, exits, or PnL with the
existing Edge launch strategy.

The lane is disabled by default. Its profile is compiled as
`2cq-flow-projection-v1` so launch `.env` changes cannot silently retune it.

## Frozen Profile

Entry trigger:

- Pump route.
- Local stream slot required.
- At least 1 buy and 1 unique wallet in the 60-second context.
- At least 5 SOL of 60-second buy volume.
- Minimum historical transaction-index lead is zero, so no index-lead gate is
  applied to forward stream events.
- Wait 1,000ms, then require a new local stream quote within 1,500ms.
- Maximum 3 shared open entries and 20 entries per hour.
- Fixed 0.5 SOL paper accounting and 250bps round-trip cost.

One shared entry feeds two isolated exits:

- Runner: TP50 / SL20 / 120 seconds.
- Scalper: TP20 / SL10 / 30 seconds.

The two variants do not consume exposure twice.
TP/SL thresholds are gross market-cap moves. The ledger deducts the frozen
250bps round-trip cost after applying the bounded gross exit, matching the
forward replay.

`sameSlotAsFirstSeen` is only a stream-observation proxy. The lane does not
claim that it observed token creation, and the historical creation gate is not
applied.

## Install On VPS

Upload the bundle from local PowerShell:

```powershell
scp "C:\Users\Cheet\OneDrive\Documents\EdgeSniffer\edgesniffer-2cq-frozen-lane.tgz" root@87.99.145.246:/root/bot/EdgeSniffer/
```

Then install it on the VPS:

```bash
cd /root/bot/EdgeSniffer
tar -xzf edgesniffer-2cq-frozen-lane.tgz
npm run build
npm run test:2cq-frozen
```

Enable only the new lane:

```bash
cd /root/bot/EdgeSniffer
if grep -q '^TWO_CQ_FROZEN_ENABLED=' .env; then
  sed -i 's/^TWO_CQ_FROZEN_ENABLED=.*/TWO_CQ_FROZEN_ENABLED=true/' .env
else
  printf '\nTWO_CQ_FROZEN_ENABLED=true\n' >> .env
fi
```

This command does not change any `LAUNCH_*` setting.

## Start A Fresh Session

Stop the existing foreground scanner first, then:

```bash
cd /root/bot/EdgeSniffer
npm run session:reset
npm run paper:launch
```

Startup must include:

```text
2CQ FROZEN lane enabled profile=2cq-flow-projection-v1
```

The normal `EdgeSniffer new launch paper scanner started` settings remain the
existing Edge lane. They are not the 2CQ profile.

## Monitor

In another SSH window:

```bash
cd /root/bot/EdgeSniffer
tail -f data/2cq-frozen-events.jsonl
```

Quick state:

```bash
cd /root/bot/EdgeSniffer
node -e "const r=require('./data/2cq-frozen-summary.json'); console.log(JSON.stringify({session:r.sessionId,opened:r.openedEntries,blocked:r.blockedEntries,pending:r.pendingEntries,open:r.openEntries,closed:r.closedByVariant,pnl:r.realizedPnlSolByVariant},null,2))"
```

## Report

The report can run while scanning:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-frozen
```

It exports JSON and Markdown under `data/reports/2cq-frozen-*`.

Do not tune after a few trades. The first decision checkpoint is at least 40
paired closed opportunities. Keep collecting until the sample also spans
multiple UTC hours and more than one session.

## Decision Rule

Keep the lane paper-only. Evidence is provisionally constructive only when:

- At least 40 paired entries have closed.
- Results are positive across multiple time blocks, not one burst.
- Base-cost PnL is positive and profit factor is at least 1.5.
- Effective entry delay remains close to the replayed 1,000-2,500ms range.
- Results are not dominated by one or two outliers.
- A later untouched session remains positive.

If it fails, preserve the logs and reject the frozen template. Do not rescue it
by changing thresholds inside the same evaluation window.

## Current V1 Evidence Log

Checkpoint reports pasted from the VPS:

```text
2026-07-05T04:00:17Z report
session=2cq-2026-07-05T03-00-41-818Z-590060 opened=20 blocked=447 open=0
runner closed=20 pnl=+0.4523 SOL win=40.0% pf=1.39 tp=7 sl=10 delay=1309ms
scalper closed=20 pnl=+0.2075 SOL win=50.0% pf=1.37 tp=9 sl=9 delay=1309ms
read=constructive early block, below 40-close checkpoint

2026-07-05T04:35:22Z report
session=2cq-2026-07-05T04-23-23-970Z-590567 opened=20 blocked=111 open=0
runner closed=20 pnl=-0.1154 SOL win=30.0% pf=0.91 tp=5 sl=11 delay=1365ms
scalper closed=20 pnl=+0.2494 SOL win=45.0% pf=1.48 tp=9 sl=7 delay=1365ms
read=scalper constructive, runner weak, delay still in band

2026-07-05T06:09:27Z report
session=2cq-2026-07-05T04-37-51-416Z-590689 opened=40 blocked=492 open=0
runner closed=40 pnl=-0.4263 SOL win=27.5% pf=0.85 tp=10 sl=23 delay=1420ms
scalper closed=41 pnl=-0.4884 SOL win=31.7% pf=0.69 tp=13 sl=24 delay=1416ms
read=untouched 40-close reproduction failed despite delay remaining in band
2026-07-05T12:40:09Z report
session=2cq-2026-07-05T06-24-58-751Z-591767 opened=72 blocked=874 open=2
runner closed=70 pnl=+3.2387 SOL win=45.7% pf=1.87 tp=30 sl=31 delay=1374ms
scalper closed=71 pnl=-0.2554 SOL win=39.4% pf=0.90 tp=26 sl=37 delay=1383ms
read=runner reproduced strongly over 70 closes; scalper still failed; require time-block review before any promotion
```

Working interpretation:

- The live stream timing assumption is still holding.
- `2cq-flow-projection-v1` has not reproduced the replay PnL/PF threshold.
- Do not promote v1 live and do not loosen v1 in place.
- Next useful work is failure classification: entry quality, exit template,
  regime, cost/latency, or concentration.


### 2026-07-05 06:10 Tail-Ledger Diagnostic

The pasted `tail -n 20` ledgers after the failed report were from a newer
session, `2cq-2026-07-05T06-10-09-905Z-591505`, not the failed
`04-37` session. Treat them as diagnostic evidence only, not as a replacement
for the failed 40-close report.

Parsed from the attached tail sample:

```text
rows=40 (20 runner + 20 scalper over the same 20 mints)
runner pnl=+0.5515 SOL, win=40.0%, tp=8, sl=11, no_update=1
scalper pnl=-0.4556 SOL, win=25.0%, tp=5, sl=14, time=1
median delay=1437ms
```

Initial pre-entry separators in this tail sample:

- Entries with `entryContext.netBuySol60 <= 0` or sell volume greater than buy
  volume were all losses in the sample.
- Entries with `entryMarketCapUsd >= 100k` were negative overall and had weak
  median best gross ROI.
- A rough pre-entry filter of MC `<100k`, positive entry net flow, buy volume
  at least sell volume, `3-20` unique wallets, and sell count no greater than
  buy count improved the runner tail sample, but the scalper tail sample
  remained negative.

Working read: v1 remains rejected as a live candidate. The 06:10 tail suggests
that v2 should first investigate entry-quality filters and likely favor runner
analysis over the current scalper exit, but the sample is too small and must be
validated from full ledgers, not tails.

## V2 Runner-Only Candidate

`2cq-flow-projection-v2` is a separate runner-only paper candidate created
after v1 runner evidence reproduced across multiple UTC time blocks while the
scalper exit continued to fail. V2 keeps the same local stream, delay,
round-trip cost, runner TP50/SL20/120s, exposure, and hourly throttles as v1,
but drops the scalper ledger from the active candidate.

V2 must start from a fresh sample and write to separate files:

```text
data/2cq-v2-frozen-signals.jsonl
data/2cq-v2-frozen-events.jsonl
data/2cq-v2-frozen-runner-closed.jsonl
data/2cq-v2-frozen-summary.json
```

Deploy from local PowerShell:

```powershell
cd "C:\Users\Cheet\OneDrive\Documents\EdgeSniffer"
scp edgesniffer-2cq-v2-runner.tgz root@87.99.145.246:/root/bot/EdgeSniffer/
```

Install on the VPS:

```bash
cd /root/bot/EdgeSniffer
tar -xzf edgesniffer-2cq-v2-runner.tgz
npm run build
npm run test:2cq-frozen
```

Start v2 paper from zero:

```bash
cd /root/bot/EdgeSniffer
npm run session:reset
TWO_CQ_FROZEN_ENABLED=true TWO_CQ_FROZEN_PROFILE=v2 npm run paper:launch
```

Startup must include:

```text
2CQ FROZEN lane enabled profile=2cq-flow-projection-v2 variants=runner
```

Report v2:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v2
```

Promotion gates remain conservative: runner positive across multiple UTC time
blocks, PF at least 1.5, delay near 1,000-2,500ms, and no outlier dominance.

## V3 Filtered Runner Candidate

`2cq-flow-projection-v3` replaces the rejected loose v2 runner with the first
validated entry-quality filter from the good-v1 versus bad-v2 comparison:

```text
entryMarketCapUsd < 50000
entryContext.netBuySol60 >= 1
runner-only
```

The filter improved both samples at once:

```text
GOOD_V1 PF 1.87 -> 2.36
BAD_V2  PF 0.69 -> 1.20
COMBINED PF 1.12 -> 1.65
```

Deploy from local PowerShell:

```powershell
cd "C:\Users\Cheet\OneDrive\Documents\EdgeSniffer"
scp edgesniffer-2cq-v3-runner.tgz root@87.99.145.246:/root/bot/EdgeSniffer/
```

Install on the VPS:

```bash
cd /root/bot/EdgeSniffer
tar -xzf edgesniffer-2cq-v3-runner.tgz
npm run build
npm run test:2cq-frozen
```

Start v3 paper from zero:

```bash
cd /root/bot/EdgeSniffer
npm run session:reset
TWO_CQ_FROZEN_ENABLED=true TWO_CQ_FROZEN_PROFILE=v3 npm run paper:launch
```

Startup must include:

```text
2CQ FROZEN lane enabled profile=2cq-flow-projection-v3 variants=runner
```

Report v3:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v3
```

V3 is paper-only. First checkpoint is 40 closes, first meaningful read is
100+ closes, and promotion-quality evidence still requires 200+ closes across
multiple positive UTC blocks plus a later untouched positive session.
V2 remains paper-only until those gates reproduce from a fresh v2 session.
## V1 Failure Diagnosis

Use this section only after an untouched 40-close session fails the decision
rule. A failed session rejects `2cq-flow-projection-v1` as a live candidate; it
does not reject 2CQ as a research source.

First preserve the current evidence:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-frozen
LATEST="$(ls -td data/reports/2cq-frozen-* | head -1)"
cp data/2cq-frozen-summary.json "$LATEST/"
cp data/2cq-frozen-events.jsonl "$LATEST/"
cp data/2cq-frozen-runner-closed.jsonl "$LATEST/"
cp data/2cq-frozen-scalper-closed.jsonl "$LATEST/"
```

Then inspect the report and closed ledgers before restarting or resetting:

```bash
cat "$LATEST"/2cq-frozen-report.md
tail -n 5 data/2cq-frozen-runner-closed.jsonl
tail -n 5 data/2cq-frozen-scalper-closed.jsonl
```

Classify the failure before proposing a v2:

- Entry-quality failure: both exits lose, wins are rare, and losses appear
  across multiple time blocks.
- Exit-template failure: entries often move favorably, but the configured
  TP/SL/hold rules convert them into losses.
- Regime failure: one time block or market condition dominates the losses.
- Cost/latency failure: gross moves look constructive, but effective delay or
  250bps round-trip cost removes the edge.
- Concentration failure: the only positive result depends on one or two
  outliers.

Do not change `TWO_CQ_FROZEN_ENABLED`, TP/SL, delay, buy-volume, or throttle
settings to rescue v1. If the diagnosis supports a new candidate, create a
separate frozen profile such as `2cq-flow-projection-v2` and start its paper
sample from zero.

Record each checkpoint with:

```text
session=
opened=
runner closed/pnl/win/pf/tp/sl/delay=
scalper closed/pnl/win/pf/tp/sl/delay=
read=
decision=
```

## Files

```text
data/2cq-frozen-signals.jsonl
data/2cq-frozen-events.jsonl
data/2cq-frozen-runner-closed.jsonl
data/2cq-frozen-scalper-closed.jsonl
data/2cq-frozen-summary.json
```

`npm run session:reset` archives these together with the normal launch session.
Generate the report before aggressive cleanup.

## Disable

```bash
cd /root/bot/EdgeSniffer
sed -i 's/^TWO_CQ_FROZEN_ENABLED=.*/TWO_CQ_FROZEN_ENABLED=false/' .env
```

Restart `paper:launch`. The existing Edge strategy continues independently.
