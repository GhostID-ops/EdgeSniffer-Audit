import fs from "node:fs";
import path from "node:path";

const files = process.argv.slice(2);
const targets = files.length
  ? files
  : fs
      .readdirSync("data")
      .filter((name) => /^path-wallet-audit-.*\.json$/.test(name))
      .map((name) => path.join("data", name));

function avg(values) {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function pct(count, total) {
  return total ? (count / total) * 100 : 0;
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtUsd(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `$${Math.round(value).toLocaleString()}`;
}

function fmtNum(value, digits = 2) {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toFixed(digits);
}

function walletFromFile(file) {
  return path.basename(file).replace(/^path-wallet-audit-/, "").replace(/\.json$/, "");
}

function returnsSummary(values) {
  const xs = values.filter(Number.isFinite);
  const wins = xs.filter((value) => value > 0);
  const losses = xs.filter((value) => value < 0);
  const profit = wins.reduce((sum, value) => sum + value, 0);
  const loss = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  return {
    n: xs.length,
    avg: avg(xs),
    median: median(xs),
    winRate: pct(wins.length, xs.length),
    profitFactor: loss > 0 ? profit / loss : profit > 0 ? Infinity : 0,
  };
}

function summarizeBucket(name, rows, predicate) {
  const xs = rows.filter(predicate);
  const source = returnsSummary(xs.map((row) => row.sourceExitPct).filter(Number.isFinite));
  return {
    name,
    n: xs.length,
    medMc: median(xs.map((row) => row.entryMarketCapUsdApprox)),
    medBuySol: median(xs.map((row) => row.sourceBuySol)),
    hit30: pct(xs.filter((row) => row.mfePct >= 30).length, xs.length),
    hit50: pct(xs.filter((row) => row.mfePct >= 50).length, xs.length),
    hit100: pct(xs.filter((row) => row.mfePct >= 100).length, xs.length),
    medMfe: median(xs.map((row) => row.mfePct)),
    medMae: median(xs.map((row) => row.maePct)),
    sourceAvg: source.avg,
    sourceMedian: source.median,
    sourceWinRate: source.winRate,
    sourcePf: source.profitFactor,
  };
}

function scoreBucket(bucket) {
  if (bucket.n < 8) return -Infinity;
  return (
    bucket.sourceAvg * 2 +
    bucket.sourceMedian +
    bucket.sourceWinRate / 10 +
    Math.min(bucket.sourcePf, 5) * 3 +
    bucket.hit50 / 10 -
    Math.max(0, -bucket.medMae - 60) / 5
  );
}

for (const file of targets) {
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  const rows = data.rows.filter(
    (row) => Number.isFinite(row.mfePct) && Number.isFinite(row.maePct)
  );
  const wallet = walletFromFile(file);
  const buckets = [
    summarizeBucket("all", rows, () => true),
    summarizeBucket("mc_lt_5k", rows, (row) => (row.entryMarketCapUsdApprox ?? Infinity) < 5_000),
    summarizeBucket("mc_5k_8k", rows, (row) => {
      const mc = row.entryMarketCapUsdApprox;
      return mc >= 5_000 && mc < 8_000;
    }),
    summarizeBucket("mc_8k_12k", rows, (row) => {
      const mc = row.entryMarketCapUsdApprox;
      return mc >= 8_000 && mc < 12_000;
    }),
    summarizeBucket("mc_12k_20k", rows, (row) => {
      const mc = row.entryMarketCapUsdApprox;
      return mc >= 12_000 && mc < 20_000;
    }),
    summarizeBucket("mc_gte_20k", rows, (row) => (row.entryMarketCapUsdApprox ?? -Infinity) >= 20_000),
    summarizeBucket("earlier_lte_5", rows, (row) => (row.lead?.earlierBuys ?? 999) <= 5),
    summarizeBucket("earlier_gt_40", rows, (row) => (row.lead?.earlierBuys ?? 0) > 40),
  ];
  const best = [...buckets].sort((a, b) => scoreBucket(b) - scoreBucket(a))[0];
  const all = buckets[0];
  console.log(
    [
      wallet.slice(0, 8),
      `sample=${rows.length}`,
      `range=${data.sample?.oldestTx?.slice(0, 10) ?? "?"}->${data.sample?.newestTx?.slice(0, 10) ?? "?"}`,
      `all hit30=${fmtPct(all.hit30)} hit50=${fmtPct(all.hit50)} hit100=${fmtPct(all.hit100)}`,
      `all source avg=${fmtPct(all.sourceAvg)} med=${fmtPct(all.sourceMedian)} wr=${fmtPct(all.sourceWinRate)} pf=${fmtNum(all.sourcePf)}`,
      `best=${best.name} n=${best.n} mc=${fmtUsd(best.medMc)} hit50=${fmtPct(best.hit50)} srcAvg=${fmtPct(best.sourceAvg)} srcMed=${fmtPct(best.sourceMedian)} srcWr=${fmtPct(best.sourceWinRate)} pf=${fmtNum(best.sourcePf)} mae=${fmtPct(best.medMae)}`,
    ].join(" | ")
  );
}
