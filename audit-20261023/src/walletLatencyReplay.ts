import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

type EntryRow = { mint: string; entryTs: number; sourceExitRoiPct: number };
type QuantReport = { wallet: string; entryRows: EntryRow[] };
type EventPoint = { ts: number; metric: number };
type StreamEvent = Record<string, unknown>;
const DELAYS_MS = [100, 250, 500, 1000];
const IMPACT_BPS = [50, 100, 250, 500];

function numberAt(row: StreamEvent, names: string[]): number | null {
  for (const name of names) {
    const value = row[name];
    if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
  }
  for (const childName of ["signal", "event", "position", "pending"]) {
    const child = row[childName];
    if (child && typeof child === "object") {
      const value = numberAt(child as StreamEvent, names);
      if (value != null) return value;
    }
  }
  return null;
}

function stringAt(row: StreamEvent, names: string[]): string | null {
  for (const name of names) {
    const value = row[name];
    if (typeof value === "string" && value) return value;
  }
  for (const childName of ["signal", "event", "position", "pending"]) {
    const child = row[childName];
    if (child && typeof child === "object") {
      const value = stringAt(child as StreamEvent, names);
      if (value) return value;
    }
  }
  return null;
}

function timestamp(row: StreamEvent): number | null {
  const raw = row.ts ?? row.timestamp ?? row.isoTs;
  if (typeof raw === "number") return raw < 10_000_000_000 ? raw * 1000 : raw;
  if (typeof raw === "string") {
    const numeric = Number(raw);
    if (Number.isFinite(numeric)) return numeric < 10_000_000_000 ? numeric * 1000 : numeric;
    const parsed = Date.parse(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function median(values: number[]): number | null {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid]! : (xs[mid - 1]! + xs[mid]!) / 2;
}

function summarize(values: number[]) {
  const wins = values.filter((value) => value > 0);
  const losses = values.filter((value) => value < 0);
  const grossWin = wins.reduce((sum, value) => sum + value, 0);
  const grossLoss = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  return {
    entries: values.length,
    winRatePct: (wins.length / Math.max(1, values.length)) * 100,
    avgRoiPct: values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length),
    medianRoiPct: median(values),
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? null : 0,
    worstRoiPct: values.length ? Math.min(...values) : null,
  };
}

async function main(): Promise<void> {
  const runDir = path.resolve(process.argv[2] ?? "");
  const eventFiles = process.argv.slice(3).map((file) => path.resolve(file));
  if (!process.argv[2] || !eventFiles.length) {
    throw new Error("usage: tsx src/walletLatencyReplay.ts RUN_DIR STREAM_EVENTS.jsonl [MORE.jsonl...]");
  }
  const state = JSON.parse(await readFile(path.join(runDir, "state.json"), "utf8")) as {
    wallets: Array<{ label: string; lane: string; status: string; output?: string }>;
  };
  const byMint = new Map<string, EventPoint[]>();
  let eventRows = 0;
  for (const file of eventFiles) {
    const text = await readFile(file, "utf8");
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const row = JSON.parse(line) as StreamEvent;
        const mint = stringAt(row, ["mint", "tokenMint", "outputMint"]);
        const ts = timestamp(row);
        const metric = numberAt(row, ["marketCapUsd", "inferredMarketCapUsd", "entryMarketCapUsd", "priceUsd", "price"]);
        if (!mint || ts == null || metric == null) continue;
        const points = byMint.get(mint) ?? [];
        points.push({ ts, metric });
        byMint.set(mint, points);
        eventRows++;
      } catch {}
    }
  }
  for (const points of byMint.values()) points.sort((a, b) => a.ts - b.ts);

  const results = [];
  for (const wallet of state.wallets.filter((item) => item.status === "completed" && item.output)) {
    const report = JSON.parse(await readFile(wallet.output!, "utf8")) as QuantReport;
    const matched: Array<{ row: EntryRow; base: EventPoint; delayed: Map<number, EventPoint> }> = [];
    for (const row of report.entryRows ?? []) {
      const points = byMint.get(row.mint);
      if (!points?.length || !Number.isFinite(row.sourceExitRoiPct)) continue;
      const sourceMs = row.entryTs * 1000;
      const base = points.find((point) => point.ts >= sourceMs - 500 && point.ts <= sourceMs + 1500);
      if (!base) continue;
      const delayed = new Map<number, EventPoint>();
      for (const delay of DELAYS_MS) {
        const point = points.find((candidate) => candidate.ts >= base.ts + delay && candidate.ts <= base.ts + delay + 5000);
        if (point) delayed.set(delay, point);
      }
      matched.push({ row, base, delayed });
    }
    for (const delayMs of DELAYS_MS) {
      for (const impactBps of IMPACT_BPS) {
        const adjusted = matched.flatMap(({ row, base, delayed }) => {
          const point = delayed.get(delayMs);
          if (!point || point.metric <= 0 || base.metric <= 0) return [];
          const entryPriceMultiple = point.metric / base.metric;
          const impactMultiple = 1 - impactBps / 10_000;
          const roi = (((1 + row.sourceExitRoiPct / 100) / entryPriceMultiple) * impactMultiple * impactMultiple - 1) * 100;
          return Number.isFinite(roi) ? [roi] : [];
        });
        results.push({ label: wallet.label, lane: wallet.lane, delayMs, roundTripImpactBps: impactBps * 2, coverageEntries: adjusted.length, ...summarize(adjusted) });
      }
    }
  }
  const output = {
    generatedAt: new Date().toISOString(),
    methodology: {
      eventFiles,
      eventRows,
      mints: byMint.size,
      delaysMs: DELAYS_MS,
      oneWayImpactBps: IMPACT_BPS,
      note: "Delay uses millisecond local stream events and MC/price ratios. Impact is a stress assumption applied on entry and exit, not a measured fill.",
    },
    results,
  };
  await writeFile(path.join(runDir, "latency-impact-replay.json"), `${JSON.stringify(output, null, 2)}\n`, "utf8");
  const lines = [
    "# Latency And Impact Replay",
    "",
    output.methodology.note,
    "",
    "| Wallet | Lane | Delay | Round-trip impact | Coverage | Win | Median ROI | PF | Worst |",
    "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ...results.map((row) => `| ${row.label} | ${row.lane} | ${row.delayMs}ms | ${row.roundTripImpactBps}bps | ${row.coverageEntries} | ${row.winRatePct.toFixed(1)}% | ${row.medianRoiPct?.toFixed(2) ?? "-"}% | ${row.profitFactor?.toFixed(2) ?? "-"} | ${row.worstRoiPct?.toFixed(2) ?? "-"}% |`),
  ];
  await writeFile(path.join(runDir, "latency-impact-replay.md"), `${lines.join("\n")}\n`, "utf8");
  console.log(`Latency replay exported: ${runDir}`);
  console.log(`events=${eventRows} mints=${byMint.size} scenarios=${results.length}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});