import "dotenv/config";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

interface RawSignal {
  unixSeconds: number;
  timestamp: string;
  mint: string;
  boostAmount: number;
  totalBoosts: number;
  currentMcUsd: number | null;
  firstBoostMcUsd: number | null;
  athMcUsd: number | null;
  growthPct: number | null;
}

interface ProjectSummary {
  mint: string;
  signalCount: number;
  firstCallTs: string;
  firstCallUnix: number;
  lastCallTs: string;
  lastCallUnix: number;
  daysBetween: number;
  firstCallMcUsd: number | null;
  lastCallMcUsd: number | null;
  athMcUsd: number | null;
  signalGrowthPct: number | null;
  maxTotalBoosts: number;
  maxBoostAmount: number;
  totalBoostAmountSummed: number;
}

function num(s: string): number | null {
  if (!s || s.length === 0) return null;
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : null;
}

function readSignalsCsv(filePath: string): RawSignal[] {
  const text = readFileSync(filePath, "utf8");
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length < 2) return [];
  const header = lines[0].split(",");
  const idx = (name: string): number => header.indexOf(name);

  const ix = {
    timestamp: idx("timestamp"),
    unix: idx("unix_seconds"),
    mint: idx("mint"),
    boost: idx("boost_amount"),
    total: idx("total_boosts"),
    cur: idx("current_mc_usd"),
    fb: idx("first_boost_mc_usd"),
    ath: idx("ath_mc_usd"),
    grow: idx("growth_pct"),
  };
  if (ix.mint < 0 || ix.unix < 0) {
    throw new Error("signals.csv missing required columns (mint, unix_seconds)");
  }

  const rows: RawSignal[] = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(",");
    if (c.length < header.length) continue;
    rows.push({
      timestamp: c[ix.timestamp] ?? "",
      unixSeconds: num(c[ix.unix]) ?? 0,
      mint: c[ix.mint],
      boostAmount: num(c[ix.boost]) ?? 0,
      totalBoosts: num(c[ix.total]) ?? 0,
      currentMcUsd: ix.cur >= 0 ? num(c[ix.cur]) : null,
      firstBoostMcUsd: ix.fb >= 0 ? num(c[ix.fb]) : null,
      athMcUsd: ix.ath >= 0 ? num(c[ix.ath]) : null,
      growthPct: ix.grow >= 0 ? num(c[ix.grow]) : null,
    });
  }
  return rows;
}

function summarize(rows: RawSignal[]): ProjectSummary {
  // rows are all for the same mint, sorted oldest→newest
  const first = rows[0];
  const last = rows[rows.length - 1];
  const daysBetween =
    last.unixSeconds && first.unixSeconds
      ? Math.round(((last.unixSeconds - first.unixSeconds) / 86400) * 10) / 10
      : 0;

  let firstMc = first.firstBoostMcUsd ?? first.currentMcUsd;
  if (firstMc == null) {
    for (const r of rows) {
      if (r.firstBoostMcUsd != null) {
        firstMc = r.firstBoostMcUsd;
        break;
      }
    }
  }
  const lastMc = last.currentMcUsd;
  const athMc = rows
    .map((r) => r.athMcUsd)
    .filter((v): v is number => v != null && v > 0)
    .reduce((a, b) => Math.max(a, b), 0) || null;

  const growthPct =
    firstMc != null && lastMc != null && firstMc > 0
      ? ((lastMc - firstMc) / firstMc) * 100
      : null;

  return {
    mint: first.mint,
    signalCount: rows.length,
    firstCallTs: first.timestamp,
    firstCallUnix: first.unixSeconds,
    lastCallTs: last.timestamp,
    lastCallUnix: last.unixSeconds,
    daysBetween,
    firstCallMcUsd: firstMc,
    lastCallMcUsd: lastMc,
    athMcUsd: athMc,
    signalGrowthPct: growthPct,
    maxTotalBoosts: Math.max(...rows.map((r) => r.totalBoosts)),
    maxBoostAmount: Math.max(...rows.map((r) => r.boostAmount)),
    totalBoostAmountSummed: rows.reduce((a, r) => a + r.boostAmount, 0),
  };
}

function fmtMc(n: number | null): string {
  if (n == null) return "n/a";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function fmtPct(n: number | null): string {
  if (n == null) return "n/a";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
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

function statLine(label: string, values: number[], formatter: (n: number) => string = (n) => n.toFixed(1)): void {
  if (values.length === 0) {
    console.log(`  ${label.padEnd(38)} n=0`);
    return;
  }
  const sorted = [...values].sort((a, b) => a - b);
  console.log(
    `  ${label.padEnd(38)} mean=${formatter(mean(sorted)).padStart(10)}  median=${formatter(quantile(sorted, 0.5)).padStart(10)}  p25=${formatter(
      quantile(sorted, 0.25)
    ).padStart(10)}  p75=${formatter(quantile(sorted, 0.75)).padStart(10)}  p90=${formatter(quantile(sorted, 0.9)).padStart(10)}  max=${formatter(
      sorted[sorted.length - 1]
    ).padStart(10)}`
  );
}

function main(): void {
  const inputPath =
    process.env.PROJECTS_INPUT ?? path.resolve(process.cwd(), "signals.csv");
  const outputPath =
    process.env.PROJECTS_OUTPUT ?? path.resolve(process.cwd(), "projects.csv");

  const rows = readSignalsCsv(inputPath);
  if (rows.length === 0) {
    console.error(`No rows found in ${inputPath}. Run \`npm run snipe:extract\` first.`);
    process.exit(1);
  }

  // Group by mint, sort each group oldest→newest.
  const byMint = new Map<string, RawSignal[]>();
  for (const r of rows) {
    const arr = byMint.get(r.mint) ?? [];
    arr.push(r);
    byMint.set(r.mint, arr);
  }
  for (const arr of byMint.values()) {
    arr.sort((a, b) => a.unixSeconds - b.unixSeconds);
  }

  const projects = Array.from(byMint.values()).map(summarize);

  // Write CSV: one row per project.
  const cols = [
    "mint",
    "signal_count",
    "first_call_ts",
    "last_call_ts",
    "days_between",
    "first_call_mc_usd",
    "last_call_mc_usd",
    "ath_mc_usd",
    "signal_growth_pct",
    "max_total_boosts",
    "max_boost_amount",
    "total_boost_amount_summed",
  ];
  const out: string[] = [cols.join(",")];
  const sortedForCsv = [...projects].sort(
    (a, b) => b.lastCallUnix - a.lastCallUnix
  );
  for (const p of sortedForCsv) {
    out.push(
      [
        p.mint,
        p.signalCount,
        p.firstCallTs,
        p.lastCallTs,
        p.daysBetween,
        p.firstCallMcUsd ?? "",
        p.lastCallMcUsd ?? "",
        p.athMcUsd ?? "",
        p.signalGrowthPct != null ? p.signalGrowthPct.toFixed(2) : "",
        p.maxTotalBoosts,
        p.maxBoostAmount,
        p.totalBoostAmountSummed,
      ].join(",")
    );
  }
  writeFileSync(outputPath, out.join("\n") + "\n", "utf8");

  // Console: stats-only report.
  const reSignaled = projects.filter((p) => p.signalCount > 1);
  const withGrowth = projects.filter((p) => p.signalGrowthPct != null);
  const projectsWithFirstMc = projects.filter((p) => p.firstCallMcUsd != null);
  const projectsWithLastMc = projects.filter((p) => p.lastCallMcUsd != null);

  console.log("");
  console.log("=== Projects report ===");
  console.log(`Signals scanned       : ${rows.length}`);
  console.log(`Unique projects       : ${projects.length}`);
  console.log(
    `Re-signaled (>1 post) : ${reSignaled.length} / ${projects.length} (${(
      (reSignaled.length / Math.max(1, projects.length)) *
      100
    ).toFixed(1)}%)`
  );
  console.log(`Output CSV            : ${outputPath}`);

  // Per-project stats
  console.log("");
  console.log("=== Per-project stats ===");
  statLine(
    "Signal count per project",
    projects.map((p) => p.signalCount),
    (n) => n.toFixed(1)
  );
  statLine(
    "Days actively boosted (first→last)",
    reSignaled.map((p) => p.daysBetween),
    (n) => n.toFixed(1)
  );
  statLine(
    "Max Total Boosts ever (per project)",
    projects.map((p) => p.maxTotalBoosts),
    (n) => n.toFixed(0)
  );
  statLine(
    "Max Boost Amount ever (per project)",
    projects.map((p) => p.maxBoostAmount),
    (n) => n.toFixed(0)
  );
  statLine(
    "Total Boost Amount summed",
    projects.map((p) => p.totalBoostAmountSummed),
    (n) => n.toFixed(0)
  );

  // MC stats
  console.log("");
  console.log("=== MC at first call (entry if sniping the first signal) ===");
  statLine(
    "first_call_mc_usd",
    projectsWithFirstMc.map((p) => p.firstCallMcUsd!),
    fmtMc
  );

  console.log("");
  console.log("=== MC at last call ===");
  statLine(
    "last_call_mc_usd",
    projectsWithLastMc.map((p) => p.lastCallMcUsd!),
    fmtMc
  );

  // Growth stats
  console.log("");
  console.log("=== Growth from first call → last call ===");
  if (withGrowth.length === 0) {
    console.log("  (no projects with computable growth — MC fields missing in signals)");
  } else {
    const gains = withGrowth
      .map((p) => p.signalGrowthPct!)
      .sort((a, b) => a - b);
    statLine("growth_pct", gains, (n) => `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`);
    const pumped = gains.filter((g) => g > 0).length;
    const declined = gains.filter((g) => g < 0).length;
    const flat = gains.filter((g) => g === 0).length;
    console.log("");
    console.log(
      `  Pumped   (>0%)  : ${pumped} / ${withGrowth.length} (${(
        (pumped / withGrowth.length) *
        100
      ).toFixed(1)}%)`
    );
    console.log(
      `  Declined (<0%)  : ${declined} / ${withGrowth.length} (${(
        (declined / withGrowth.length) *
        100
      ).toFixed(1)}%)`
    );
    if (flat > 0) {
      console.log(
        `  Flat     (=0%)  : ${flat} / ${withGrowth.length} (${(
          (flat / withGrowth.length) *
          100
        ).toFixed(1)}%)`
      );
    }
    console.log("");
    console.log("  Hits at growth thresholds:");
    for (const t of [50, 100, 200, 500, 1000]) {
      const n = gains.filter((g) => g >= t).length;
      const pct = ((n / withGrowth.length) * 100).toFixed(1);
      console.log(
        `    >= +${String(t).padStart(4)}% : ${String(n).padStart(4)} / ${withGrowth.length} (${pct}%)`
      );
    }
  }

  // Per-signal stats (over raw signals.csv)
  const allBoostAmount = rows.map((r) => r.boostAmount).filter((n) => n > 0);
  const allTotalBoosts = rows.map((r) => r.totalBoosts).filter((n) => n > 0);
  const allReportedGrowth = rows
    .map((r) => r.growthPct)
    .filter((n): n is number => n != null);
  const allCurrentMc = rows
    .map((r) => r.currentMcUsd)
    .filter((n): n is number => n != null && n > 0);

  console.log("");
  console.log("=== Per-signal stats (every post in the channel) ===");
  statLine("Boost Amount per post", allBoostAmount, (n) => n.toFixed(1));
  statLine("Total Boosts at post time", allTotalBoosts, (n) => n.toFixed(1));
  statLine("Current MC at post time", allCurrentMc, fmtMc);
  if (allReportedGrowth.length > 0) {
    statLine(
      "Growth % reported in signal",
      allReportedGrowth,
      (n) => `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`
    );
  }

  console.log("");
  console.log(
    "Note: 'growth' uses the channel's reported MC fields (First Boost MC → last Current MC). It captures the project's trajectory while the channel was still posting it — not all-time peak. CSV has the per-project rows for further analysis."
  );
  process.exit(0);
}

main();
