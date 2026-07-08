# EdgeSniffer 2CQ V3 Filter Simulation

Use this after the 2CQ v3 paper lane reaches 1,000+ closes.

This creates a VPS-local simulation report that compares the unfiltered v3 profile against simple regime filters:

- Baseline: all v3 closes
- Skip repeated weak UTC hours
- Trade only stronger UTC hour-of-day buckets
- Chronological train/test split
- Best/worst filter candidates by PF, PnL, and retained volume

This is still paper analysis. It should guide a v4 candidate, not directly switch live trading.

## Install The Filter Sim Script

Run this on the VPS:

```bash
cd /root/bot/EdgeSniffer
cat > scripts/twoCqV3FilterSim.mjs <<'NODE'
import fs from 'node:fs';
import path from 'node:path';

const root = '/root/bot/EdgeSniffer';
const file = path.join(root, 'data/2cq-v3-frozen-runner-closed.jsonl');
const minHourCloses = Number(process.env.MIN_HOUR_CLOSES || 40);
const minKeepPct = Number(process.env.MIN_KEEP_PCT || 25);
const targetPf = Number(process.env.TARGET_PF || 1.5);

function readRows() {
  if (!fs.existsSync(file)) {
    console.error(`Missing ${file}`);
    process.exit(1);
  }

  return fs.readFileSync(file, 'utf8')
    .trim()
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line))
    .sort((a, b) => new Date(a.openedAt) - new Date(b.openedAt));
}

function hour(row) {
  return new Date(row.openedAt).toISOString().slice(11, 13);
}

function exactHour(row) {
  return new Date(row.openedAt).toISOString().slice(0, 13) + ':00Z';
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
  }

  return {
    n: rows.length,
    pnl: Number(pnl.toFixed(4)),
    winPct: rows.length ? Number((100 * wins / rows.length).toFixed(1)) : 0,
    pf: grossLoss ? Number((grossWin / grossLoss).toFixed(2)) : null,
    tp,
    sl,
    time,
    noUpdate,
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

function metricRows(map, keyName) {
  return [...map.entries()]
    .map(([key, rows]) => ({ [keyName]: key, ...stats(rows) }))
    .sort((a, b) => String(a[keyName]).localeCompare(String(b[keyName])));
}

function runFilter(label, rows, predicate) {
  const kept = rows.filter(predicate);
  const s = stats(kept);
  return {
    label,
    kept: `${kept.length}/${rows.length}`,
    keepPct: rows.length ? Number((100 * kept.length / rows.length).toFixed(1)) : 0,
    ...s,
  };
}

function print(title, rows) {
  console.log(`\n## ${title}`);
  for (const row of rows) console.log(JSON.stringify(row));
}

function buildHourSets(rows) {
  const hourStats = metricRows(groupBy(rows, hour), 'utcHour');
  const weakN40 = new Set(hourStats.filter((h) => h.n >= minHourCloses && h.pnl < 0).map((h) => h.utcHour));
  const weakPfBelow1 = new Set(hourStats.filter((h) => h.n >= minHourCloses && (h.pf ?? 0) < 1).map((h) => h.utcHour));
  const strongPf13 = new Set(hourStats.filter((h) => h.n >= minHourCloses && (h.pf ?? 0) >= 1.3 && h.pnl > 0).map((h) => h.utcHour));
  const strongPf15 = new Set(hourStats.filter((h) => h.n >= minHourCloses && (h.pf ?? 0) >= 1.5 && h.pnl > 0).map((h) => h.utcHour));
  const positiveN40 = new Set(hourStats.filter((h) => h.n >= minHourCloses && h.pnl > 0).map((h) => h.utcHour));

  return { hourStats, weakN40, weakPfBelow1, strongPf13, strongPf15, positiveN40 };
}

function fmtSet(set) {
  return [...set].sort().map((h) => `${h}:00Z`);
}

function evaluateCandidates(rows, prefix = '') {
  const { hourStats, weakN40, weakPfBelow1, strongPf13, strongPf15, positiveN40 } = buildHourSets(rows);

  const candidates = [
    runFilter(`${prefix}BASELINE_ALL`, rows, () => true),
    runFilter(`${prefix}SKIP_NEGATIVE_HOURS_N${minHourCloses}`, rows, (row) => !weakN40.has(hour(row))),
    runFilter(`${prefix}SKIP_PF_LT_1_HOURS_N${minHourCloses}`, rows, (row) => !weakPfBelow1.has(hour(row))),
    runFilter(`${prefix}ONLY_POSITIVE_HOURS_N${minHourCloses}`, rows, (row) => positiveN40.has(hour(row))),
    runFilter(`${prefix}ONLY_PF_GTE_1_3_HOURS_N${minHourCloses}`, rows, (row) => strongPf13.has(hour(row))),
    runFilter(`${prefix}ONLY_PF_GTE_1_5_HOURS_N${minHourCloses}`, rows, (row) => strongPf15.has(hour(row))),
    runFilter(`${prefix}MANUAL_SKIP_02_14`, rows, (row) => !new Set(['02', '14']).has(hour(row))),
    runFilter(`${prefix}MANUAL_SKIP_02_06_09_14`, rows, (row) => !new Set(['02', '06', '09', '14']).has(hour(row))),
    runFilter(`${prefix}MANUAL_CORE_00_01_03_13_15_16_19_21_22`, rows, (row) => new Set(['00', '01', '03', '13', '15', '16', '19', '21', '22']).has(hour(row))),
  ];

  return {
    hourStats,
    sets: {
      weakN40: fmtSet(weakN40),
      weakPfBelow1: fmtSet(weakPfBelow1),
      positiveN40: fmtSet(positiveN40),
      strongPf13: fmtSet(strongPf13),
      strongPf15: fmtSet(strongPf15),
    },
    candidates,
  };
}

function splitRows(rows) {
  const splitIndex = Math.floor(rows.length * 0.7);
  return {
    train: rows.slice(0, splitIndex),
    test: rows.slice(splitIndex),
  };
}

function applyTrainDerivedFilters(train, test) {
  const { sets } = evaluateCandidates(train);
  const weak = new Set(sets.weakN40.map((h) => h.slice(0, 2)));
  const weakPf = new Set(sets.weakPfBelow1.map((h) => h.slice(0, 2)));
  const strong13 = new Set(sets.strongPf13.map((h) => h.slice(0, 2)));
  const strong15 = new Set(sets.strongPf15.map((h) => h.slice(0, 2)));
  const positive = new Set(sets.positiveN40.map((h) => h.slice(0, 2)));

  return [
    runFilter('TEST_BASELINE_ALL', test, () => true),
    runFilter('TEST_TRAIN_SKIP_NEGATIVE_HOURS', test, (row) => !weak.has(hour(row))),
    runFilter('TEST_TRAIN_SKIP_PF_LT_1_HOURS', test, (row) => !weakPf.has(hour(row))),
    runFilter('TEST_TRAIN_ONLY_POSITIVE_HOURS', test, (row) => positive.has(hour(row))),
    runFilter('TEST_TRAIN_ONLY_PF_GTE_1_3_HOURS', test, (row) => strong13.has(hour(row))),
    runFilter('TEST_TRAIN_ONLY_PF_GTE_1_5_HOURS', test, (row) => strong15.has(hour(row))),
  ];
}

const rows = readRows();
const allStats = stats(rows);
const exactHourStats = metricRows(groupBy(rows, exactHour), 'hour');
const exactQualified = exactHourStats.filter((h) => h.n >= 20);
const { hourStats, sets, candidates } = evaluateCandidates(rows);
const ranked = candidates
  .filter((c) => c.keepPct >= minKeepPct)
  .sort((a, b) => (b.pf ?? 0) - (a.pf ?? 0));

const { train, test } = splitRows(rows);
const trainEval = evaluateCandidates(train);
const testCandidates = applyTrainDerivedFilters(train, test);

console.log('# 2CQ V3 Filter Simulation');
console.log(JSON.stringify({
  generatedAt: new Date().toISOString(),
  source: file,
  totalCloses: rows.length,
  minHourCloses,
  minKeepPct,
  targetPf,
}));

console.log('\n## Baseline');
console.log(JSON.stringify(allStats));

print('UTC Hour-Of-Day Stats', hourStats);
console.log('\n## Learned Hour Sets');
console.log(JSON.stringify(sets));

print('Candidate Filters', candidates);
print('Ranked Candidate Filters By PF', ranked);

print('Top Exact Date-Hours', [...exactQualified].sort((a, b) => b.pnl - a.pnl).slice(0, 12));
print('Worst Exact Date-Hours', [...exactQualified].sort((a, b) => a.pnl - b.pnl).slice(0, 12));

console.log('\n## Chronological Split');
console.log(JSON.stringify({
  train: { from: train[0]?.openedAt || null, to: train.at(-1)?.openedAt || null, ...stats(train) },
  test: { from: test[0]?.openedAt || null, to: test.at(-1)?.openedAt || null, ...stats(test) },
}));

console.log('\n## Train-Learned Filters Applied To Test');
for (const row of testCandidates) console.log(JSON.stringify(row));

console.log('\n## Decision Hints');
const best = ranked[0];
console.log(JSON.stringify({
  baselinePf: allStats.pf,
  baselinePnl: allStats.pnl,
  bestFilter: best?.label || null,
  bestFilterPf: best?.pf ?? null,
  bestFilterPnl: best?.pnl ?? null,
  bestFilterKeepPct: best?.keepPct ?? null,
  targetPfReached: (best?.pf ?? 0) >= targetPf,
  warning: 'Hour filters are in-sample unless confirmed by train/test or future forward paper.',
}));
NODE

node - <<'NODE'
const fs = require('fs');
const file = 'package.json';
const pkg = JSON.parse(fs.readFileSync(file, 'utf8'));
pkg.scripts = pkg.scripts || {};
pkg.scripts['report:2cq-v3-filter-sim'] = 'node scripts/twoCqV3FilterSim.mjs';
fs.writeFileSync(file, JSON.stringify(pkg, null, 2) + '\n');
NODE
```

## Run The Filter Sim

```bash
cd /root/bot/EdgeSniffer
npm run report:2cq-v3-filter-sim
```

Save a permanent copy:

```bash
cd /root/bot/EdgeSniffer
mkdir -p data/reports/regime
npm run report:2cq-v3-filter-sim | tee "data/reports/regime/2cq-v3-filter-sim-$(date -u +%Y%m%d-%H%M%S).txt"
```

Optional stricter bucket settings:

```bash
cd /root/bot/EdgeSniffer
MIN_HOUR_CLOSES=60 MIN_KEEP_PCT=30 npm run report:2cq-v3-filter-sim
```

## How To Read It

Prioritize filters that:

- improve PF materially vs baseline
- keep enough trade volume
- keep PnL positive
- do not rely only on one tiny 20-close hour
- survive the train/test section

If the best in-sample filter fails on the chronological test section, keep collecting and do not promote it.
