import "dotenv/config";
import { readFileSync } from "node:fs";
import path from "node:path";

interface Project {
  mint: string;
  signalCount: number;
  firstCallMcUsd: number | null;
  lastCallMcUsd: number | null;
  athMcUsd: number | null;
  signalGrowthPct: number | null;
  maxTotalBoosts: number;
  maxBoostAmount: number;
  totalBoostAmountSummed: number;
  firstSignalTotalBoosts: number | null;
  firstSignalBoostAmount: number | null;
  athGainPct: number | null;
}

function num(s: string): number | null {
  if (!s || s.length === 0) return null;
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : null;
}

function readCsv(filePath: string): { header: string[]; rows: string[][] } {
  const text = readFileSync(filePath, "utf8");
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length === 0) return { header: [], rows: [] };
  const header = lines[0].split(",");
  const rows = lines.slice(1).map((l) => l.split(","));
  return { header, rows };
}

function loadProjects(
  projectsCsv: string,
  signalsCsv: string
): Project[] {
  // First-signal-per-mint from signals.csv (we need this to know how popular
  // the project was when the channel FIRST noticed it).
  const firstSignalByMint = new Map<
    string,
    { totalBoosts: number; boostAmount: number; unix: number }
  >();
  {
    const { header, rows } = readCsv(signalsCsv);
    const idx = (n: string) => header.indexOf(n);
    const ix = {
      mint: idx("mint"),
      unix: idx("unix_seconds"),
      total: idx("total_boosts"),
      boost: idx("boost_amount"),
    };
    if (ix.mint < 0) {
      throw new Error(`signals.csv missing 'mint' column`);
    }
    for (const r of rows) {
      const mint = r[ix.mint];
      const unix = num(r[ix.unix]) ?? 0;
      const total = num(r[ix.total]) ?? 0;
      const boost = num(r[ix.boost]) ?? 0;
      const prev = firstSignalByMint.get(mint);
      if (!prev || unix < prev.unix) {
        firstSignalByMint.set(mint, {
          totalBoosts: total,
          boostAmount: boost,
          unix,
        });
      }
    }
  }

  // Now load projects.csv and merge.
  const { header, rows } = readCsv(projectsCsv);
  const idx = (n: string) => header.indexOf(n);
  const ix = {
    mint: idx("mint"),
    signalCount: idx("signal_count"),
    fcMc: idx("first_call_mc_usd"),
    lcMc: idx("last_call_mc_usd"),
    athMc: idx("ath_mc_usd"),
    growth: idx("signal_growth_pct"),
    maxTotal: idx("max_total_boosts"),
    maxBoost: idx("max_boost_amount"),
    totalSum: idx("total_boost_amount_summed"),
  };
  if (ix.mint < 0) throw new Error("projects.csv missing 'mint' column");

  const result: Project[] = [];
  for (const r of rows) {
    const mint = r[ix.mint];
    const fcMc = num(r[ix.fcMc]);
    const athMc = num(r[ix.athMc]);
    const first = firstSignalByMint.get(mint);
    const athGain =
      athMc != null && fcMc != null && fcMc > 0
        ? ((athMc - fcMc) / fcMc) * 100
        : null;
    result.push({
      mint,
      signalCount: num(r[ix.signalCount]) ?? 0,
      firstCallMcUsd: fcMc,
      lastCallMcUsd: num(r[ix.lcMc]),
      athMcUsd: athMc,
      signalGrowthPct: num(r[ix.growth]),
      maxTotalBoosts: num(r[ix.maxTotal]) ?? 0,
      maxBoostAmount: num(r[ix.maxBoost]) ?? 0,
      totalBoostAmountSummed: num(r[ix.totalSum]) ?? 0,
      firstSignalTotalBoosts: first?.totalBoosts ?? null,
      firstSignalBoostAmount: first?.boostAmount ?? null,
      athGainPct: athGain,
    });
  }
  return result;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return NaN;
  const i = Math.floor(sorted.length * q);
  return sorted[Math.min(i, sorted.length - 1)];
}

function mean(values: number[]): number {
  if (values.length === 0) return NaN;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function compare(
  label: string,
  winnerVals: number[],
  loserVals: number[],
  formatter: (n: number) => string = (n) => n.toFixed(1)
): void {
  const ws = [...winnerVals].sort((a, b) => a - b);
  const ls = [...loserVals].sort((a, b) => a - b);
  const wMed = ws.length ? formatter(quantile(ws, 0.5)) : "n/a";
  const lMed = ls.length ? formatter(quantile(ls, 0.5)) : "n/a";
  const wMean = ws.length ? formatter(mean(ws)) : "n/a";
  const lMean = ls.length ? formatter(mean(ls)) : "n/a";
  const wP75 = ws.length ? formatter(quantile(ws, 0.75)) : "n/a";
  const lP75 = ls.length ? formatter(quantile(ls, 0.75)) : "n/a";
  const lift =
    ls.length && ws.length && quantile(ls, 0.5) > 0
      ? `${((quantile(ws, 0.5) / quantile(ls, 0.5) - 1) * 100).toFixed(0)}%`
      : "—";
  console.log(`  ${label.padEnd(34)}`);
  console.log(
    `    winners (n=${ws.length})  median=${wMed.padStart(10)}  mean=${wMean.padStart(10)}  p75=${wP75.padStart(10)}`
  );
  console.log(
    `    losers  (n=${ls.length})  median=${lMed.padStart(10)}  mean=${lMean.padStart(10)}  p75=${lP75.padStart(10)}`
  );
  console.log(`    winner-median vs loser-median lift: ${lift}`);
}

function bucketWinRate<T>(
  label: string,
  rows: T[],
  pickKey: (r: T) => number,
  isWinner: (r: T) => boolean,
  edges: { lo: number; hi: number; label: string }[]
): void {
  console.log(`  ${label}`);
  for (const e of edges) {
    const inBucket = rows.filter((r) => {
      const v = pickKey(r);
      return v >= e.lo && v < e.hi;
    });
    if (inBucket.length === 0) continue;
    const wins = inBucket.filter(isWinner).length;
    const wr = ((wins / inBucket.length) * 100).toFixed(1);
    const bar = "█".repeat(Math.round(parseFloat(wr) / 5));
    console.log(
      `    ${e.label.padEnd(14)}  n=${String(inBucket.length).padStart(4)}  winRate=${wr.padStart(5)}%  ${bar}`
    );
  }
}

function fmtMc(n: number): string {
  if (!Number.isFinite(n)) return "n/a";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function main(): void {
  const projectsPath =
    process.env.PATTERNS_PROJECTS ??
    path.resolve(process.cwd(), "projects.csv");
  const signalsPath =
    process.env.PATTERNS_SIGNALS ??
    path.resolve(process.cwd(), "signals.csv");
  const winThresholdPct = parseFloat(
    process.env.PATTERNS_WIN_THRESHOLD ?? "100"
  );

  console.log(`[patterns] projects=${projectsPath} signals=${signalsPath}`);
  console.log(`[patterns] win threshold = ATH gain >= +${winThresholdPct}%`);

  const projects = loadProjects(projectsPath, signalsPath);
  const withGain = projects.filter((p) => p.athGainPct != null);
  console.log("");
  console.log(
    `Total projects=${projects.length}  with computable ATH gain=${withGain.length}`
  );

  const winners = withGain.filter(
    (p) => (p.athGainPct ?? -Infinity) >= winThresholdPct
  );
  const losers = withGain.filter(
    (p) => (p.athGainPct ?? -Infinity) < winThresholdPct
  );
  const winRate = withGain.length
    ? ((winners.length / withGain.length) * 100).toFixed(1)
    : "n/a";

  console.log(
    `Winners: ${winners.length}  Losers: ${losers.length}  Overall win rate: ${winRate}%`
  );

  // Headline ATH gain distribution
  if (withGain.length > 0) {
    const sorted = withGain
      .map((p) => p.athGainPct!)
      .sort((a, b) => a - b);
    console.log("");
    console.log(
      `ATH gain distribution: median=+${quantile(sorted, 0.5).toFixed(0)}%  p75=+${quantile(sorted, 0.75).toFixed(0)}%  p90=+${quantile(sorted, 0.9).toFixed(0)}%  max=+${sorted[sorted.length - 1].toFixed(0)}%`
    );
    for (const t of [50, 100, 200, 500, 1000, 5000]) {
      const n = sorted.filter((g) => g >= t).length;
      const pct = ((n / sorted.length) * 100).toFixed(1);
      console.log(
        `  ATH gain >= +${String(t).padStart(5)}%  ${String(n).padStart(4)} / ${sorted.length} (${pct}%)`
      );
    }
  }

  if (winners.length === 0 || losers.length === 0) {
    console.log("");
    console.log(
      "Not enough variety to compare. Try a different PATTERNS_WIN_THRESHOLD."
    );
    process.exit(0);
  }

  console.log("");
  console.log("=== Feature comparison: winners vs losers ===");
  compare(
    "Max Total Boosts (per project)",
    winners.map((w) => w.maxTotalBoosts),
    losers.map((l) => l.maxTotalBoosts),
    (n) => n.toFixed(0)
  );
  console.log("");
  compare(
    "Max Boost Amount (per project)",
    winners.map((w) => w.maxBoostAmount),
    losers.map((l) => l.maxBoostAmount),
    (n) => n.toFixed(0)
  );
  console.log("");
  compare(
    "Total Boost Amount summed",
    winners.map((w) => w.totalBoostAmountSummed),
    losers.map((l) => l.totalBoostAmountSummed),
    (n) => n.toFixed(0)
  );
  console.log("");
  compare(
    "Signal count (re-posted N times)",
    winners.map((w) => w.signalCount),
    losers.map((l) => l.signalCount),
    (n) => n.toFixed(0)
  );
  console.log("");
  compare(
    "First-signal Total Boosts",
    winners
      .map((w) => w.firstSignalTotalBoosts)
      .filter((n): n is number => n != null),
    losers
      .map((l) => l.firstSignalTotalBoosts)
      .filter((n): n is number => n != null),
    (n) => n.toFixed(0)
  );
  console.log("");
  compare(
    "First-signal Boost Amount",
    winners
      .map((w) => w.firstSignalBoostAmount)
      .filter((n): n is number => n != null),
    losers
      .map((l) => l.firstSignalBoostAmount)
      .filter((n): n is number => n != null),
    (n) => n.toFixed(0)
  );
  console.log("");
  compare(
    "Entry MC at first signal",
    winners
      .map((w) => w.firstCallMcUsd)
      .filter((n): n is number => n != null && n > 0),
    losers
      .map((l) => l.firstCallMcUsd)
      .filter((n): n is number => n != null && n > 0),
    fmtMc
  );

  console.log("");
  console.log(`=== Win rate by bucket (winner = ATH gain >= +${winThresholdPct}%) ===`);
  console.log("");
  bucketWinRate(
    "By Max Total Boosts (cumulative project hype):",
    withGain,
    (p) => p.maxTotalBoosts,
    (p) => (p.athGainPct ?? -Infinity) >= winThresholdPct,
    [
      { lo: 1, hi: 2, label: "1" },
      { lo: 2, hi: 6, label: "2-5" },
      { lo: 6, hi: 16, label: "6-15" },
      { lo: 16, hi: 51, label: "16-50" },
      { lo: 51, hi: 151, label: "51-150" },
      { lo: 151, hi: Infinity, label: "151+" },
    ]
  );
  console.log("");
  bucketWinRate(
    "By Max Boost Amount (largest single buy):",
    withGain,
    (p) => p.maxBoostAmount,
    (p) => (p.athGainPct ?? -Infinity) >= winThresholdPct,
    [
      { lo: 1, hi: 10, label: "1-9" },
      { lo: 10, hi: 25, label: "10-24" },
      { lo: 25, hi: 50, label: "25-49" },
      { lo: 50, hi: 100, label: "50-99" },
      { lo: 100, hi: 250, label: "100-249" },
      { lo: 250, hi: Infinity, label: "250+" },
    ]
  );
  console.log("");
  bucketWinRate(
    "By Signal Count (channel re-posts):",
    withGain,
    (p) => p.signalCount,
    (p) => (p.athGainPct ?? -Infinity) >= winThresholdPct,
    [
      { lo: 1, hi: 2, label: "1 (one-shot)" },
      { lo: 2, hi: 4, label: "2-3" },
      { lo: 4, hi: 10, label: "4-9" },
      { lo: 10, hi: Infinity, label: "10+" },
    ]
  );
  console.log("");
  bucketWinRate(
    "By First-Signal Total Boosts (how 'discovered' was project at first call):",
    withGain.filter((p) => p.firstSignalTotalBoosts != null),
    (p) => p.firstSignalTotalBoosts!,
    (p) => (p.athGainPct ?? -Infinity) >= winThresholdPct,
    [
      { lo: 1, hi: 2, label: "1 (1st boost ever)" },
      { lo: 2, hi: 6, label: "2-5" },
      { lo: 6, hi: 16, label: "6-15" },
      { lo: 16, hi: Infinity, label: "16+" },
    ]
  );
  console.log("");
  bucketWinRate(
    "By Entry MC at first signal:",
    withGain.filter((p) => p.firstCallMcUsd != null && p.firstCallMcUsd > 0),
    (p) => p.firstCallMcUsd!,
    (p) => (p.athGainPct ?? -Infinity) >= winThresholdPct,
    [
      { lo: 0, hi: 10_000, label: "<$10K" },
      { lo: 10_000, hi: 25_000, label: "$10-25K" },
      { lo: 25_000, hi: 50_000, label: "$25-50K" },
      { lo: 50_000, hi: 100_000, label: "$50-100K" },
      { lo: 100_000, hi: 500_000, label: "$100-500K" },
      { lo: 500_000, hi: Infinity, label: ">$500K" },
    ]
  );

  console.log("");
  console.log(
    "Reading the buckets: the highest win-rate bucket within each feature is your edge. The width of the gap (e.g. 8% → 60%) tells you how predictive that feature is."
  );
  console.log(
    `Tweak via: PATTERNS_WIN_THRESHOLD=200 npm run snipe:patterns (test stricter winner definitions)`
  );
  process.exit(0);
}

main();
