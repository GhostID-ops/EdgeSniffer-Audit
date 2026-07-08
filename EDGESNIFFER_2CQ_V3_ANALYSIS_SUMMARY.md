# EdgeSniffer 2CQ V3 Analysis Summary

Snapshot from the 2CQ v3 paper dataset after the 1,000-close checkpoint.

## Current Read

`2cq-flow-projection-v3` is a real paper candidate, but not live-ready unfiltered.

The main discovery is that v3 has a positive edge overall, but it appears regime-sensitive by UTC hour. The next forward paper test is v4: v3 plus a dead-hour skip filter.

## V3 Baseline

```text
total closes: 1015
paper pnl: +22.3594 SOL
win rate: 37.7%
profit factor: 1.36
take profit: 365
stop loss: 530
time exit: 4
no update exit: 116
```

Decision read:

```text
paperCandidate: true
liveReadyUnfiltered: false
regimeFilterNeeded: true
```

## Filter Simulation

Baseline v3:

```text
kept: 1015/1015
pnl: +22.3594 SOL
pf: 1.36
```

Skip repeated weak UTC hours `02` and `14`:

```text
kept: 892/1015
keep: 87.9%
pnl: +25.8462 SOL
pf: 1.49
```

Skip UTC hours `02`, `06`, `09`, and `14`:

```text
kept: 852/1015
keep: 83.9%
pnl: +27.4658 SOL
pf: 1.55
```

Trade only stronger hour buckets:

```text
kept: 496/1015
keep: 48.9%
pnl: +18.1868 SOL
pf: 1.65
```

Manual core hours `00`, `01`, `03`, `13`, `15`, `16`, `19`, `21`, `22`:

```text
kept: 495/1015
keep: 48.8%
pnl: +18.9030 SOL
pf: 1.67
```

## Chronological Validation

Train split:

```text
closes: 710
pnl: +19.6150 SOL
pf: 1.46
```

Test split:

```text
closes: 305
pnl: +2.7444 SOL
pf: 1.13
```

Train-learned weak-hour filter applied to test:

```text
closes: 265
pnl: +5.1714 SOL
pf: 1.31
```

Interpretation: the hour filter did not only look good in-sample. It also improved the later chronological test set, though the out-of-sample PF was still below the live-ready bar.

## Confidence Scores

```text
2CQ source lead: 9.0/10
v3 paper candidate: 8.8/10
v3 unfiltered live-ready: 5.4/10
v4 skip-hour paper candidate: 8.2/10
filtered live-readiness: 6.2/10
```

## Roadmap

1. Keep v4 running as the forward paper test.
2. Let v4 reach at least 100 closes for a first read.
3. Treat 300-500 v4 closes as the meaningful checkpoint.
4. Compare v4 actual forward results against:
   - v3 historical baseline
   - v3 simulated skip-hour result
   - v3 chronological test behavior
5. Do not discuss live trading until v4 holds up on fresh paper data.

## Useful Commands

Run the v4 report:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v4
LATEST="$(ls -td data/reports/2cq-v4-frozen-* | head -1)"
cat "$LATEST/2cq-frozen-report.md"
```

Run the v3 regime report:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v3-regime
```

Run the v3 filter simulation:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v3-filter-sim
```
