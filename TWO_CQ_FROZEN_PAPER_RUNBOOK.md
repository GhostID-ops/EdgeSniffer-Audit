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
