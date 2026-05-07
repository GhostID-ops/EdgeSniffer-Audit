import "dotenv/config";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

interface RawSignal {
  unix: number;
  mint: string;
  totalBoosts: number;
  boostAmount: number;
  currentMc: number | null;
  ath: number | null;
}

interface SignalForward {
  unix: number;
  mint: string;
  signalIndex: number; // 1 = first signal for this mint, 2 = second, etc.
  totalBoostsAtSignal: number;
  boostAmountAtSignal: number;
  currentMc: number | null;
  athAtSignal: number | null;
  hasFutureSignal: boolean;
  // forwardMaxAth = max ATH recorded across all later signals for same mint.
  // Note: this includes any pre-signal peak, since ATH is cumulative.
  forwardMaxAth: number | null;
  // newPeakAfterSignal = forwardMaxAth > athAtSignal — the project hit a new
  // high somewhere in (this signal time, last signal time]. This is the only
  // unambiguous "post-signal pump" measure we can derive from the data.
  newPeakAfterSignal: boolean;
  // Definite forward gain: gain from current_mc to forwardMaxAth, but ONLY
  // when a new peak was set post-signal. Zero otherwise (we don't credit
  // unconfirmed bounce-backs to old peaks).
  forwardGainDefinitePct: number | null;
}

function num(s: string): number | null {
  if (!s || s.length === 0) return null;
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : null;
}

function readSignals(filePath: string): RawSignal[] {
  const text = readFileSync(filePath, "utf8");
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  const header = lines[0].split(",");
  const idx = (n: string) => header.indexOf(n);
  const ix = {
    unix: idx("unix_seconds"),
    mint: idx("mint"),
    boost: idx("boost_amount"),
    total: idx("total_boosts"),
    cur: idx("current_mc_usd"),
    ath: idx("ath_mc_usd"),
  };
  const out: RawSignal[] = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(",");
    if (c.length < header.length) continue;
    out.push({
      unix: num(c[ix.unix]) ?? 0,
      mint: c[ix.mint],
      totalBoosts: num(c[ix.total]) ?? 0,
      boostAmount: num(c[ix.boost]) ?? 0,
      currentMc: num(c[ix.cur]),
      ath: num(c[ix.ath]),
    });
  }
  return out;
}

function buildForward(rawSignals: RawSignal[]): SignalForward[] {
  // Group by mint, sort each group by unix.
  const byMint = new Map<string, RawSignal[]>();
  for (const r of rawSignals) {
    const arr = byMint.get(r.mint) ?? [];
    arr.push(r);
    byMint.set(r.mint, arr);
  }
  for (const arr of byMint.values()) {
    arr.sort((a, b) => a.unix - b.unix);
  }

  const all: SignalForward[] = [];
  for (const [mint, arr] of byMint) {
    for (let i = 0; i < arr.length; i++) {
      const s = arr[i];
      const later = arr.slice(i + 1);
      const hasFuture = later.length > 0;
      let forwardMaxAth: number | null = null;
      for (const l of later) {
        if (l.ath != null && (forwardMaxAth == null || l.ath > forwardMaxAth)) {
          forwardMaxAth = l.ath;
        }
      }
      const newPeak =
        hasFuture &&
        forwardMaxAth != null &&
        s.ath != null &&
        forwardMaxAth > s.ath;
      let forwardGainPct: number | null = null;
      if (newPeak && s.currentMc != null && s.currentMc > 0 && forwardMaxAth != null) {
        forwardGainPct = ((forwardMaxAth - s.currentMc) / s.currentMc) * 100;
      } else if (hasFuture) {
        forwardGainPct = 0; // no new peak after this signal
      }
      all.push({
        unix: s.unix,
        mint,
        signalIndex: i + 1,
        totalBoostsAtSignal: s.totalBoosts,
        boostAmountAtSignal: s.boostAmount,
        currentMc: s.currentMc,
        athAtSignal: s.ath,
        hasFutureSignal: hasFuture,
        forwardMaxAth,
        newPeakAfterSignal: newPeak,
        forwardGainDefinitePct: forwardGainPct,
      });
    }
  }
  return all;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return NaN;
  const i = Math.floor(sorted.length * q);
  return sorted[Math.min(i, sorted.length - 1)];
}

function bucketByTotalBoosts<T>(
  rows: T[],
  pickKey: (r: T) => number
): Map<string, T[]> {
  const buckets: { lo: number; hi: number; label: string }[] = [
    { lo: 1, hi: 2, label: "1 (one-shot)" },
    { lo: 2, hi: 3, label: "2" },
    { lo: 3, hi: 5, label: "3-4" },
    { lo: 5, hi: 7, label: "5-6" },
    { lo: 7, hi: 11, label: "7-10" },
    { lo: 11, hi: 26, label: "11-25" },
    { lo: 26, hi: Infinity, label: "26+" },
  ];
  const result = new Map<string, T[]>();
  for (const b of buckets) result.set(b.label, []);
  for (const r of rows) {
    const v = pickKey(r);
    for (const b of buckets) {
      if (v >= b.lo && v < b.hi) {
        result.get(b.label)!.push(r);
        break;
      }
    }
  }
  return result;
}

function pad(s: string | number, n: number): string {
  return String(s).padStart(n);
}

function main(): void {
  const inputPath =
    process.env.FORWARD_INPUT ?? path.resolve(process.cwd(), "signals.csv");
  const csvOut =
    process.env.FORWARD_OUTPUT ??
    path.resolve(process.cwd(), "signals_forward.csv");

  const raw = readSignals(inputPath);
  const forward = buildForward(raw);
  const usable = forward.filter((f) => f.hasFutureSignal && f.currentMc != null);

  console.log("");
  console.log("=== Forward gain analysis ===");
  console.log(`Total signals               : ${forward.length}`);
  console.log(`With at least one future signal (analyzable): ${usable.length}`);
  console.log(`(last-signal-of-mint excluded — no forward window)`);

  // Headline: across all analyzable signals, what fraction had a new peak after the signal?
  const newPeakSignals = usable.filter((u) => u.newPeakAfterSignal);
  const pctNewPeak = ((newPeakSignals.length / usable.length) * 100).toFixed(1);
  console.log(
    `Signals with a NEW post-signal peak: ${newPeakSignals.length} / ${usable.length} (${pctNewPeak}%)`
  );

  if (newPeakSignals.length > 0) {
    const gains = newPeakSignals
      .map((s) => s.forwardGainDefinitePct!)
      .sort((a, b) => a - b);
    console.log(
      `Forward-gain distribution (only signals where new peak was set):`
    );
    console.log(
      `  median=+${quantile(gains, 0.5).toFixed(0)}%  p75=+${quantile(gains, 0.75).toFixed(0)}%  p90=+${quantile(gains, 0.9).toFixed(0)}%  max=+${gains[gains.length - 1].toFixed(0)}%`
    );
  }

  // Bucket by total_boosts AT the signal — the question is "if I snipe AT this
  // moment when total_boosts = N, what's the chance of further upside?"
  console.log("");
  console.log("=== Win rate by Total Boosts AT signal time ===");
  console.log(
    "Win = price reaches a NEW HIGH after this signal big enough to clear TP threshold."
  );
  console.log("");
  console.log(
    `Bucket           n      newPeak%   TP+50%    TP+80%    TP+100%   TP+200%   medianGain`
  );
  console.log(
    "----------------------------------------------------------------------------------"
  );
  const buckets = bucketByTotalBoosts(usable, (r) => r.totalBoostsAtSignal);
  for (const [label, group] of buckets) {
    if (group.length === 0) continue;
    const newPeak = group.filter((g) => g.newPeakAfterSignal).length;
    const newPeakPct = ((newPeak / group.length) * 100).toFixed(1);
    const tp50 =
      group.filter((g) => (g.forwardGainDefinitePct ?? 0) >= 50).length;
    const tp80 =
      group.filter((g) => (g.forwardGainDefinitePct ?? 0) >= 80).length;
    const tp100 =
      group.filter((g) => (g.forwardGainDefinitePct ?? 0) >= 100).length;
    const tp200 =
      group.filter((g) => (g.forwardGainDefinitePct ?? 0) >= 200).length;
    const gains = group
      .map((g) => g.forwardGainDefinitePct ?? 0)
      .sort((a, b) => a - b);
    const median = quantile(gains, 0.5);

    const fmt = (n: number, d: number) => `${((n / d) * 100).toFixed(1)}%`;
    console.log(
      `${label.padEnd(15)}  ${pad(group.length, 5)}  ${pad(newPeakPct + "%", 8)}  ${pad(fmt(tp50, group.length), 7)}  ${pad(fmt(tp80, group.length), 7)}  ${pad(fmt(tp100, group.length), 7)}  ${pad(fmt(tp200, group.length), 7)}  +${median.toFixed(0)}%`
    );
  }

  // Also bucket by signal index (1st, 2nd, 3rd... signal for the project) —
  // tells us when in the project's lifecycle to snipe.
  console.log("");
  console.log("=== Win rate by signal position in project's lifecycle ===");
  console.log("(How many times has this project been signaled BEFORE this one?)");
  console.log("");
  console.log(
    `Signal #         n      newPeak%   TP+50%    TP+80%    TP+100%   TP+200%   medianGain`
  );
  console.log(
    "----------------------------------------------------------------------------------"
  );
  const positionBuckets: { lo: number; hi: number; label: string }[] = [
    { lo: 1, hi: 2, label: "1st" },
    { lo: 2, hi: 3, label: "2nd" },
    { lo: 3, hi: 4, label: "3rd" },
    { lo: 4, hi: 6, label: "4th-5th" },
    { lo: 6, hi: 11, label: "6th-10th" },
    { lo: 11, hi: Infinity, label: "11th+" },
  ];
  for (const b of positionBuckets) {
    const group = usable.filter(
      (u) => u.signalIndex >= b.lo && u.signalIndex < b.hi
    );
    if (group.length === 0) continue;
    const newPeak = group.filter((g) => g.newPeakAfterSignal).length;
    const newPeakPct = ((newPeak / group.length) * 100).toFixed(1);
    const tp50 =
      group.filter((g) => (g.forwardGainDefinitePct ?? 0) >= 50).length;
    const tp80 =
      group.filter((g) => (g.forwardGainDefinitePct ?? 0) >= 80).length;
    const tp100 =
      group.filter((g) => (g.forwardGainDefinitePct ?? 0) >= 100).length;
    const tp200 =
      group.filter((g) => (g.forwardGainDefinitePct ?? 0) >= 200).length;
    const gains = group
      .map((g) => g.forwardGainDefinitePct ?? 0)
      .sort((a, b) => a - b);
    const median = quantile(gains, 0.5);
    const fmt = (n: number, d: number) => `${((n / d) * 100).toFixed(1)}%`;
    console.log(
      `${b.label.padEnd(15)}  ${pad(group.length, 5)}  ${pad(newPeakPct + "%", 8)}  ${pad(fmt(tp50, group.length), 7)}  ${pad(fmt(tp80, group.length), 7)}  ${pad(fmt(tp100, group.length), 7)}  ${pad(fmt(tp200, group.length), 7)}  +${median.toFixed(0)}%`
    );
  }

  // Write full per-signal CSV for spreadsheet exploration.
  const cols = [
    "unix_seconds",
    "mint",
    "signal_index",
    "total_boosts_at_signal",
    "boost_amount_at_signal",
    "current_mc",
    "ath_at_signal",
    "has_future_signal",
    "forward_max_ath",
    "new_peak_after_signal",
    "forward_gain_definite_pct",
  ];
  const lines = [cols.join(",")];
  for (const f of forward) {
    lines.push(
      [
        f.unix,
        f.mint,
        f.signalIndex,
        f.totalBoostsAtSignal,
        f.boostAmountAtSignal,
        f.currentMc ?? "",
        f.athAtSignal ?? "",
        f.hasFutureSignal ? "true" : "false",
        f.forwardMaxAth ?? "",
        f.newPeakAfterSignal ? "true" : "false",
        f.forwardGainDefinitePct != null
          ? f.forwardGainDefinitePct.toFixed(2)
          : "",
      ].join(",")
    );
  }
  writeFileSync(csvOut, lines.join("\n") + "\n", "utf8");

  console.log("");
  console.log(`Per-signal forward CSV → ${csvOut}`);
  console.log("");
  console.log(
    "How to read: 'newPeak%' is the share of signals where the project hit a NEW high afterwards. 'TP+X%' is the share where that new high was big enough to clear that TP from the snipe entry. 'medianGain' includes signals with no new peak as 0%."
  );
  process.exit(0);
}

main();
