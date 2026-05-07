import "dotenv/config";
import { readFileSync } from "node:fs";
import path from "node:path";

interface Row {
  mint: string;
  unixSeconds: number;
  boostAmount: number;
  totalBoosts: number;
  firstBoostMcUsd: number | null;
  athMcUsd: number | null;
  pairFound: boolean;
  currentMcUsd: number | null;
  liquidityUsd: number | null;
  gainVsFirstBoostPct: number | null;
  daysSinceSignal: number | null;
}

function num(s: string): number | null {
  if (!s || s.length === 0) return null;
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : null;
}

function readEnriched(filePath: string): Row[] {
  const text = readFileSync(filePath, "utf8");
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length < 2) return [];
  const header = lines[0].split(",");
  const idx = (name: string): number => header.indexOf(name);

  const ix = {
    mint: idx("mint"),
    unix: idx("unix_seconds"),
    boost: idx("boost_amount"),
    total: idx("total_boosts"),
    fbMc: idx("first_boost_mc_usd"),
    athMc: idx("ath_mc_usd"),
    pairFound: idx("pair_found"),
    currentMc: idx("current_mc_usd"),
    liq: idx("liquidity_usd"),
    gain: idx("gain_vs_first_boost_pct"),
    days: idx("days_since_signal"),
  };

  const rows: Row[] = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(",");
    if (c.length < header.length) continue;
    rows.push({
      mint: c[ix.mint],
      unixSeconds: num(c[ix.unix]) ?? 0,
      boostAmount: num(c[ix.boost]) ?? 0,
      totalBoosts: num(c[ix.total]) ?? 0,
      firstBoostMcUsd: num(c[ix.fbMc]),
      athMcUsd: num(c[ix.athMc]),
      pairFound: c[ix.pairFound] === "true",
      currentMcUsd: num(c[ix.currentMc]),
      liquidityUsd: num(c[ix.liq]),
      gainVsFirstBoostPct: num(c[ix.gain]),
      daysSinceSignal: num(c[ix.days]),
    });
  }
  return rows;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const i = Math.floor(sorted.length * q);
  return sorted[Math.min(i, sorted.length - 1)];
}

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function fmt(n: number, digits: number = 1): string {
  if (!Number.isFinite(n)) return "n/a";
  return n.toFixed(digits);
}

function distSummary(label: string, values: number[]): void {
  if (values.length === 0) {
    console.log(`${label}: n=0`);
    return;
  }
  const sorted = [...values].sort((a, b) => a - b);
  console.log(
    `${label}: n=${sorted.length} mean=${fmt(mean(sorted))} p25=${fmt(quantile(sorted, 0.25))} p50=${fmt(
      quantile(sorted, 0.5)
    )} p75=${fmt(quantile(sorted, 0.75))} p90=${fmt(quantile(sorted, 0.9))} max=${fmt(sorted[sorted.length - 1])}`
  );
}

function bucketize<T>(rows: T[], pickKey: (r: T) => number, edges: number[]): Map<string, T[]> {
  const result = new Map<string, T[]>();
  for (let i = 0; i < edges.length; i++) {
    const lo = edges[i];
    const hi = i + 1 < edges.length ? edges[i + 1] : Infinity;
    const label = hi === Infinity ? `>=${lo}` : `${lo}-${hi - 1}`;
    result.set(label, []);
  }
  for (const r of rows) {
    const v = pickKey(r);
    for (let i = 0; i < edges.length; i++) {
      const lo = edges[i];
      const hi = i + 1 < edges.length ? edges[i + 1] : Infinity;
      if (v >= lo && v < hi) {
        const label = hi === Infinity ? `>=${lo}` : `${lo}-${hi - 1}`;
        result.get(label)!.push(r);
        break;
      }
    }
  }
  return result;
}

async function main(): Promise<void> {
  const inputPath =
    process.env.ANALYZE_INPUT ??
    path.resolve(process.cwd(), "signals_enriched.csv");

  const winThresholdPct = parseFloat(process.env.ANALYZE_WIN_THRESHOLD ?? "50");

  console.log(`[analyze] input=${inputPath} winThreshold=+${winThresholdPct}%`);

  const all = readEnriched(inputPath);
  console.log(`[analyze] loaded ${all.length} rows`);

  // Dedupe: one row per mint (the FIRST signal chronologically), so the
  // analysis answers "if I sniped the first time this project appeared".
  const firstByMint = new Map<string, Row>();
  for (const r of all) {
    const prev = firstByMint.get(r.mint);
    if (!prev || r.unixSeconds < prev.unixSeconds) {
      firstByMint.set(r.mint, r);
    }
  }
  const unique = Array.from(firstByMint.values());
  console.log(`[analyze] ${unique.length} unique mints (first signal per project)`);

  const alive = unique.filter((r) => r.pairFound);
  const dead = unique.filter((r) => !r.pairFound);
  console.log(
    `[analyze] alive on DexScreener: ${alive.length} / dead-or-untracked: ${dead.length}`
  );

  console.log("");
  console.log("=== Signal characteristics (all unique mints) ===");
  distSummary(
    "boost_amount    ",
    unique.map((r) => r.boostAmount).filter((n) => n > 0)
  );
  distSummary(
    "total_boosts    ",
    unique.map((r) => r.totalBoosts).filter((n) => n > 0)
  );
  distSummary(
    "first_boost_mc  ",
    unique
      .map((r) => r.firstBoostMcUsd)
      .filter((n): n is number => n != null && n > 0)
  );

  console.log("");
  console.log("=== Outcome (gain from first-boost MC to TODAY) ===");
  console.log(
    "Note: this is buy-and-hold-until-now PnL. Real snipers exit faster, so peak intra-day gains are likely higher than shown here."
  );
  const gains = alive
    .map((r) => r.gainVsFirstBoostPct)
    .filter((n): n is number => n != null);
  distSummary("gain_pct        ", gains);

  const winThresh = winThresholdPct;
  const wins = alive.filter(
    (r) =>
      r.gainVsFirstBoostPct != null && r.gainVsFirstBoostPct >= winThresh
  );
  const losses = alive.filter(
    (r) =>
      r.gainVsFirstBoostPct != null && r.gainVsFirstBoostPct < winThresh
  );
  const winRate =
    alive.length > 0 ? ((wins.length / alive.length) * 100).toFixed(1) : "n/a";
  console.log("");
  console.log(
    `Win rate (gain >= ${winThresh}%): ${wins.length} / ${alive.length} = ${winRate}%`
  );

  if (wins.length > 0) {
    console.log("");
    console.log("=== Among WINNERS only ===");
    distSummary(
      "winner_gain_pct ",
      wins.map((r) => r.gainVsFirstBoostPct!).filter(Number.isFinite)
    );
    distSummary(
      "winner_total_boosts",
      wins.map((r) => r.totalBoosts).filter((n) => n > 0)
    );
    distSummary(
      "winner_boost_amount",
      wins.map((r) => r.boostAmount).filter((n) => n > 0)
    );
  }

  if (losses.length > 0) {
    console.log("");
    console.log("=== Among LOSERS / FLAT ===");
    distSummary(
      "loser_total_boosts",
      losses.map((r) => r.totalBoosts).filter((n) => n > 0)
    );
    distSummary(
      "loser_boost_amount",
      losses.map((r) => r.boostAmount).filter((n) => n > 0)
    );
  }

  console.log("");
  console.log("=== Win rate by Total Boosts bucket ===");
  console.log("(buy at signal, hold to today, win = gain >= threshold)");
  const totalBuckets = bucketize(alive, (r) => r.totalBoosts, [
    1, 5, 10, 25, 50, 100, 250, 500, 1000,
  ]);
  for (const [label, group] of totalBuckets) {
    if (group.length === 0) continue;
    const w = group.filter(
      (r) => r.gainVsFirstBoostPct != null && r.gainVsFirstBoostPct >= winThresh
    ).length;
    const wr = ((w / group.length) * 100).toFixed(1);
    const groupGains = group
      .map((r) => r.gainVsFirstBoostPct)
      .filter((n): n is number => n != null);
    const medGain = groupGains.length
      ? quantile([...groupGains].sort((a, b) => a - b), 0.5).toFixed(1)
      : "n/a";
    console.log(
      `  total_boosts ${label.padEnd(9)} n=${String(group.length).padStart(4)} winRate=${wr.padStart(5)}% medianGain=${medGain.padStart(7)}%`
    );
  }

  console.log("");
  console.log("=== Win rate by Boost Amount bucket ===");
  const boostBuckets = bucketize(alive, (r) => r.boostAmount, [
    1, 5, 10, 25, 50, 100, 250, 500,
  ]);
  for (const [label, group] of boostBuckets) {
    if (group.length === 0) continue;
    const w = group.filter(
      (r) => r.gainVsFirstBoostPct != null && r.gainVsFirstBoostPct >= winThresh
    ).length;
    const wr = ((w / group.length) * 100).toFixed(1);
    const groupGains = group
      .map((r) => r.gainVsFirstBoostPct)
      .filter((n): n is number => n != null);
    const medGain = groupGains.length
      ? quantile([...groupGains].sort((a, b) => a - b), 0.5).toFixed(1)
      : "n/a";
    console.log(
      `  boost_amount ${label.padEnd(9)} n=${String(group.length).padStart(4)} winRate=${wr.padStart(5)}% medianGain=${medGain.padStart(7)}%`
    );
  }

  // TP suggestion: among winners, the median post-entry gain is a reasonable
  // baseline TP — captures the typical winning move without holding to peak.
  if (wins.length >= 5) {
    const winnerGains = wins
      .map((r) => r.gainVsFirstBoostPct!)
      .sort((a, b) => a - b);
    const tpP25 = quantile(winnerGains, 0.25);
    const tpP50 = quantile(winnerGains, 0.5);
    const tpP75 = quantile(winnerGains, 0.75);
    console.log("");
    console.log("=== Take-profit candidates (based on winner gain distribution) ===");
    console.log(
      `Conservative (capture ~75% of winners): TP at +${tpP25.toFixed(0)}%`
    );
    console.log(
      `Balanced (median winner): TP at +${tpP50.toFixed(0)}%`
    );
    console.log(
      `Aggressive (top 25% winners): TP at +${tpP75.toFixed(0)}%`
    );
    console.log(
      "These are buy-and-hold gains. If you exit earlier (intraday), pick LOWER TPs — true intraday peaks are typically higher than these snapshots."
    );
  } else {
    console.log("");
    console.log("(Not enough winners yet for a TP recommendation. Need >= 5 winners.)");
  }

  console.log("");
  console.log(
    "Caveat: gain_vs_first_boost_pct is computed at the time you ran enrichSignals. Tokens that pumped and dumped weeks ago will show as flat/negative here, even though sniping them at signal time would have been profitable. For true intraday backtest data, run a forward live-recorder over the next few days."
  );

  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
