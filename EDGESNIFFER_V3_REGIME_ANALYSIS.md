# EdgeSniffer 2CQ V3 Regime Analysis

Use this after the 2CQ v3 paper lane reaches the 1,000-close checkpoint.

This creates a VPS-local report script that aggregates across all systemd/tmux-restarted 2CQ v3 sessions. It is designed to answer:

- What is the all-time v3 paper result?
- How many closes are collected vs the target?
- Which sessions are strong or weak?
- Which UTC hours are repeatedly strong or weak?
- Which day-of-week windows are strong or weak?
- Which exact date-hour blocks were best/worst?

## Install The Report Script

Run this on the VPS:

```bash
cd /root/bot/EdgeSniffer
cat > scripts/twoCqV3RegimeReport.mjs <<'NODE'
import fs from 'node:fs';
import path from 'node:path';

const root = '/root/bot/EdgeSniffer';
const file = path.join(root, 'data/2cq-v3-frozen-runner-closed.jsonl');
const target = Number(process.env.TARGET_CLOSES || 1000);
const minBucket = Number(process.env.MIN_BUCKET_CLOSES || 20);
const topN = Number(process.env.TOP_N || 12);
const sinceArg = process.argv.find((arg) => arg.startsWith('--since='));
const since = sinceArg ? new Date(sinceArg.slice('--since='.length)) : null;

function readRows() {
  if (!fs.existsSync(file)) {
    console.error(`Missing ${file}`);
    process.exit(1);
  }

  const rows = fs.readFileSync(file, 'utf8')
    .trim()
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line))
    .filter((row) => !since || new Date(row.openedAt) >= since)
    .sort((a, b) => new Date(a.openedAt) - new Date(b.openedAt));

  return rows;
}

function stats(rows) {
  let pnl = 0;
  let wins = 0;
  let tp = 0;
  let sl = 0;
  let time = 0;
  let noUpdate = 0;
  let grossWin = 0;
  let grossLoss = 0;
  let firstOpenedAt = null;
  let lastClosedAt = null;
  const sessions = new Set();

  for (const row of rows) {
    const p = row.pnlSol || 0;
    pnl += p;
    if (p > 0) {
      wins++;
      grossWin += p;
    }
    if (p < 0) grossLoss += Math.abs(p);
    if (row.closeReason === 'take_profit') tp++;
    if (row.closeReason === 'stop_loss') sl++;
    if (row.closeReason === 'time_exit') time++;
    if (row.closeReason === 'no_update_exit') noUpdate++;
    if (row.sessionId) sessions.add(row.sessionId);

    const opened = new Date(row.openedAt);
    const closed = new Date(row.closedAt || row.openedAt);
    if (!firstOpenedAt || opened < firstOpenedAt) firstOpenedAt = opened;
    if (!lastClosedAt || closed > lastClosedAt) lastClosedAt = closed;
  }

  const hours = firstOpenedAt && lastClosedAt ? (lastClosedAt - firstOpenedAt) / 36e5 : 0;

  return {
    n: rows.length,
    pnl: Number(pnl.toFixed(4)),
    winPct: rows.length ? Number((100 * wins / rows.length).toFixed(1)) : 0,
    pf: grossLoss ? Number((grossWin / grossLoss).toFixed(2)) : null,
    tp,
    sl,
    time,
    noUpdate,
    sessions: sessions.size,
    firstOpenedAt: firstOpenedAt?.toISOString() || null,
    lastClosedAt: lastClosedAt?.toISOString() || null,
    spanHours: Number(hours.toFixed(2)),
  };
}

function groupBy(rows, keyFn) {
  const map = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    const bucket = map.get(key) || [];
    bucket.push(row);
    map.set(key, bucket);
  }
  return map;
}

function printJsonRows(title, rows) {
  console.log(`\n## ${title}`);
  for (const row of rows) console.log(JSON.stringify(row));
}

function sortedMetricRows(map, keyName, options = {}) {
  const rows = [...map.entries()].map(([key, bucket]) => ({
    [keyName]: key,
    ...stats(bucket),
  }));

  if (options.minBucket) {
    return rows.filter((row) => row.n >= options.minBucket);
  }

  return rows;
}

function dowName(date) {
  return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][date.getUTCDay()];
}

const rows = readRows();
const all = stats(rows);
const progress = {
  target,
  totalCloses: rows.length,
  leftToTarget: Math.max(0, target - rows.length),
  estimatedActiveHours: Number((rows.length / 20).toFixed(1)),
  remainingActiveHours: Number((Math.max(0, target - rows.length) / 20).toFixed(1)),
};

console.log('# 2CQ V3 Regime Report');
console.log(JSON.stringify({
  generatedAt: new Date().toISOString(),
  source: file,
  since: since?.toISOString() || null,
}));

console.log('\n## All V3 Summary');
console.log(JSON.stringify(all));

console.log('\n## Progress');
console.log(JSON.stringify(progress));

const bySession = groupBy(rows, (row) => row.sessionId || 'unknown');
const sessionRows = sortedMetricRows(bySession, 'session')
  .sort((a, b) => String(a.firstOpenedAt).localeCompare(String(b.firstOpenedAt)));
printJsonRows('Sessions', sessionRows);

const byExactHour = groupBy(rows, (row) => new Date(row.openedAt).toISOString().slice(0, 13) + ':00Z');
const exactHourRows = sortedMetricRows(byExactHour, 'hour')
  .sort((a, b) => String(a.hour).localeCompare(String(b.hour)));
printJsonRows('Exact UTC Date-Hours', exactHourRows);

const byHourOfDay = groupBy(rows, (row) => new Date(row.openedAt).toISOString().slice(11, 13) + ':00Z');
const hourOfDayRows = sortedMetricRows(byHourOfDay, 'utcHour')
  .sort((a, b) => String(a.utcHour).localeCompare(String(b.utcHour)));
printJsonRows('UTC Hour-Of-Day Aggregate', hourOfDayRows);

const byDayOfWeek = groupBy(rows, (row) => dowName(new Date(row.openedAt)));
const dayRows = sortedMetricRows(byDayOfWeek, 'utcDay')
  .sort((a, b) => ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(a.utcDay) - ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(b.utcDay));
printJsonRows('UTC Day-Of-Week Aggregate', dayRows);

const byDayHour = groupBy(rows, (row) => {
  const date = new Date(row.openedAt);
  return `${dowName(date)} ${date.toISOString().slice(11, 13)}:00Z`;
});
const dayHourRows = sortedMetricRows(byDayHour, 'utcDayHour')
  .sort((a, b) => b.pnl - a.pnl);
printJsonRows(`Top ${topN} UTC Day-Hour Buckets`, dayHourRows.filter((r) => r.n >= minBucket).slice(0, topN));
printJsonRows(`Worst ${topN} UTC Day-Hour Buckets`, dayHourRows.filter((r) => r.n >= minBucket).slice(-topN).reverse());

const rankedExactHours = exactHourRows.filter((r) => r.n >= minBucket).sort((a, b) => b.pnl - a.pnl);
printJsonRows(`Top ${topN} Exact Date-Hours`, rankedExactHours.slice(0, topN));
printJsonRows(`Worst ${topN} Exact Date-Hours`, rankedExactHours.slice(-topN).reverse());

const rankedSessions = sessionRows.filter((r) => r.n >= minBucket).sort((a, b) => b.pnl - a.pnl);
printJsonRows(`Top ${topN} Sessions`, rankedSessions.slice(0, topN));
printJsonRows(`Worst ${topN} Sessions`, rankedSessions.slice(-topN).reverse());

const positiveExactHours = exactHourRows.filter((row) => row.n >= minBucket && row.pnl > 0);
const negativeExactHours = exactHourRows.filter((row) => row.n >= minBucket && row.pnl < 0);

console.log('\n## Decision Snapshot');
console.log(JSON.stringify({
  allTimeCandidate: all.n >= target && all.pnl > 0 && all.pf >= 1.3 ? 'PASS_PAPER_CANDIDATE' : 'KEEP_COLLECTING_OR_FILTER',
  liveReadyUnfiltered: all.n >= target && all.pf >= 1.5 ? 'POSSIBLE_TINY_LIVE_REVIEW' : 'NOT_LIVE_READY_UNFILTERED',
  regimeFilterNeeded: negativeExactHours.length > 0 && positiveExactHours.length > 0,
  positiveExactHourBuckets: positiveExactHours.length,
  negativeExactHourBuckets: negativeExactHours.length,
  minBucket,
}));
NODE

node - <<'NODE'
const fs = require('fs');
const file = 'package.json';
const pkg = JSON.parse(fs.readFileSync(file, 'utf8'));
pkg.scripts = pkg.scripts || {};
pkg.scripts['report:2cq-v3-regime'] = 'node scripts/twoCqV3RegimeReport.mjs';
fs.writeFileSync(file, JSON.stringify(pkg, null, 2) + '\n');
NODE
```

## Run The Report

All v3 data:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v3-regime
```

Since a specific timestamp:

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v3-regime -- --since=2026-07-08T03:29:00.000Z
```

Raise/lower the minimum closes needed before a bucket appears in top/worst rankings:

```bash
cd /root/bot/EdgeSniffer
MIN_BUCKET_CLOSES=40 TOP_N=20 npm run report:2cq-v3-regime
```

Save a permanent report:

```bash
cd /root/bot/EdgeSniffer
mkdir -p data/reports/regime
npm run report:2cq-v3-regime | tee "data/reports/regime/2cq-v3-regime-$(date -u +%Y%m%d-%H%M%S).txt"
```

## Interpretation Gates

At 1,000+ closes:

- `PF >= 1.3` and positive PnL means v3 is a real paper candidate.
- `PF >= 1.5` across all data means unfiltered v3 may be worth tiny live-review discussion.
- Strong top buckets plus weak bottom buckets means build a regime-filtered v4 rather than changing TP/SL.
- Repeated bad UTC hour/day buckets should be treated as skip candidates only after they repeat across multiple calendar days.
