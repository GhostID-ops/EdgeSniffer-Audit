import { createReadStream } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createInterface } from "node:readline";
import {
  median,
  simulateExit,
  type CausalProfile,
  type ExitResult,
  type PathPoint,
} from "./walletCausalResearch.js";

type SweepReport = {
  wallet: string;
  controlValidity: { eligible: boolean; reason: string | null };
  controlComparableProfiles: Array<{
    profile: CausalProfile;
    sourceProfileId: string;
    droppedGates: string[];
  }>;
};

type StreamPoint = {
  mint: string;
  timestampMs: number;
  marketCapUsd: number;
  buys: number | null;
  wallets: number | null;
  buyVolumeSol: number | null;
};

type TriggeredPath = {
  mint: string;
  triggerMs: number;
  points: StreamPoint[];
};

const DELAYS_MS = [100, 250, 500, 1000];
const MAX_DELAY_POINT_GAP_MS = Number(
  process.env.WALLET_FORWARD_MAX_DELAY_POINT_GAP_MS ?? "5000"
);
const ROUND_TRIP_COST_BPS = [100, 250, 500, 1000];
const EXIT_RULES = [
  { id: "tp10_sl10_20s", takeProfitPct: 10, stopLossPct: -10, maxHoldSeconds: 20 },
  { id: "tp15_sl10_30s", takeProfitPct: 15, stopLossPct: -10, maxHoldSeconds: 30 },
  { id: "tp20_sl10_30s", takeProfitPct: 20, stopLossPct: -10, maxHoldSeconds: 30 },
  { id: "tp20_sl15_60s", takeProfitPct: 20, stopLossPct: -15, maxHoldSeconds: 60 },
  { id: "tp30_sl15_60s", takeProfitPct: 30, stopLossPct: -15, maxHoldSeconds: 60 },
  { id: "tp35_sl15_90s", takeProfitPct: 35, stopLossPct: -15, maxHoldSeconds: 90 },
  { id: "tp50_sl20_120s", takeProfitPct: 50, stopLossPct: -20, maxHoldSeconds: 120 },
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function numberAt(
  value: unknown,
  names: string[],
  depth = 0
): number | null {
  if (!isRecord(value) || depth > 4) return null;
  for (const name of names) {
    const raw = value[name];
    const parsed = typeof raw === "number" ? raw : Number(raw);
    if (Number.isFinite(parsed)) return parsed;
  }
  for (const child of ["signal", "event", "position", "pending", "context"]) {
    const parsed = numberAt(value[child], names, depth + 1);
    if (parsed != null) return parsed;
  }
  return null;
}

function stringAt(
  value: unknown,
  names: string[],
  depth = 0
): string | null {
  if (!isRecord(value) || depth > 4) return null;
  for (const name of names) {
    const raw = value[name];
    if (typeof raw === "string" && raw) return raw;
  }
  for (const child of ["signal", "event", "position", "pending", "context"]) {
    const parsed = stringAt(value[child], names, depth + 1);
    if (parsed) return parsed;
  }
  return null;
}

function normalize(value: unknown): StreamPoint | null {
  const mint = stringAt(value, ["mint", "tokenMint", "outputMint"]);
  const rawTimestamp = numberAt(value, ["timestamp", "ts"]);
  const iso = stringAt(value, ["createdAt", "at", "isoTs"]);
  const timestampMs =
    rawTimestamp != null
      ? rawTimestamp < 10_000_000_000
        ? rawTimestamp * 1000
        : rawTimestamp
      : iso
        ? Date.parse(iso)
        : NaN;
  const marketCapUsd = numberAt(value, [
    "marketCapUsd",
    "inferredMarketCapUsd",
    "entryMarketCapUsd",
  ]);
  if (!mint || !Number.isFinite(timestampMs) || !marketCapUsd || marketCapUsd <= 0) {
    return null;
  }
  return {
    mint,
    timestampMs,
    marketCapUsd,
    buys: numberAt(value, ["buyCount60", "buys60"]),
    wallets: numberAt(value, ["uniqueWallets60", "wallets60"]),
    buyVolumeSol: numberAt(value, [
      "volumeBuySol60",
      "buyVolumeSol60",
      "buyVolume60",
    ]),
  };
}

function triggerPasses(point: StreamPoint, profile: CausalProfile): boolean {
  return (
    profile.minIndexLead === 0 &&
    profile.creation === "any" &&
    profile.identity === "none" &&
    (point.buys ?? -Infinity) >= profile.minBuys &&
    (point.wallets ?? -Infinity) >= profile.minWallets &&
    (point.buyVolumeSol ?? -Infinity) >= profile.minBuyVolumeSol
  );
}

function summarize(results: ExitResult[]) {
  const grossWin = results
    .filter((row) => row.roiPct > 0)
    .reduce((sum, row) => sum + row.roiPct, 0);
  const grossLoss = Math.abs(
    results
      .filter((row) => row.roiPct < 0)
      .reduce((sum, row) => sum + row.roiPct, 0)
  );
  return {
    entries: results.length,
    winRatePct:
      (results.filter((row) => row.roiPct > 0).length /
        Math.max(1, results.length)) *
      100,
    medianRoiPct: median(results.map((row) => row.roiPct)),
    avgRoiPct:
      results.reduce((sum, row) => sum + row.roiPct, 0) /
      Math.max(1, results.length),
    profitFactor:
      grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? null : 0,
    equalBetPnlSol:
      results.reduce((sum, row) => sum + 0.5 * (row.roiPct / 100), 0),
    takeProfits: results.filter((row) => row.reason === "take_profit").length,
    stopLosses: results.filter((row) => row.reason === "stop_loss").length,
  };
}

export async function runForwardReplay(
  sweepPath: string,
  eventFiles: string[],
  outputTag: string
): Promise<void> {
  const sweep = JSON.parse(
    (await readFile(sweepPath, "utf8")).replace(/^\uFEFF/, "")
  ) as SweepReport;
  const projected = sweep.controlComparableProfiles[0];
  const profile = projected?.profile;
  if (!profile || !projected) {
    throw new Error(
      "Sweep has no train-viable profile that can be evaluated on market controls"
    );
  }
  const triggers = new Map<string, number>();
  let parsedRows = 0;
  for (const file of eventFiles) {
    const reader = createInterface({
      input: createReadStream(file, { encoding: "utf8" }),
      crlfDelay: Infinity,
    });
    for await (const line of reader) {
      if (!line.trim()) continue;
      try {
        const point = normalize(JSON.parse(line.replace(/^\uFEFF/, "")));
        if (!point) continue;
        parsedRows += 1;
        if (triggerPasses(point, profile)) {
          triggers.set(
            point.mint,
            Math.min(triggers.get(point.mint) ?? point.timestampMs, point.timestampMs)
          );
        }
      } catch {}
    }
  }
  const byMint = new Map<string, TriggeredPath>(
    [...triggers.entries()].map(([mint, triggerMs]) => [
      mint,
      { mint, triggerMs, points: [] },
    ])
  );
  for (const file of eventFiles) {
    const reader = createInterface({
      input: createReadStream(file, { encoding: "utf8" }),
      crlfDelay: Infinity,
    });
    for await (const line of reader) {
      if (!line.trim()) continue;
      try {
        const point = normalize(JSON.parse(line.replace(/^\uFEFF/, "")));
        if (!point) continue;
        const current = byMint.get(point.mint);
        if (
          !current ||
          point.timestampMs < current.triggerMs ||
          point.timestampMs > current.triggerMs + 120_000 ||
          current.points.length >= 1_000
        ) {
          continue;
        }
        current.points.push(point);
      } catch {}
    }
  }
  const paths = [...byMint.values()]
    .map((row) => ({
      ...row,
      points: row.points
        .sort((a, b) => a.timestampMs - b.timestampMs)
        .filter(
          (point, index, points) =>
            index === 0 ||
            point.timestampMs !== points[index - 1]!.timestampMs ||
            point.marketCapUsd !== points[index - 1]!.marketCapUsd
        ),
    }))
    .sort((a, b) => a.triggerMs - b.triggerMs);
  const splitIndex = Math.floor(paths.length * 0.7);
  const splitMs = paths[Math.max(0, splitIndex - 1)]?.triggerMs ?? Infinity;
  const scenarios = [];
  for (const delayMs of DELAYS_MS) {
    for (const roundTripCostBps of ROUND_TRIP_COST_BPS) {
      for (const rule of EXIT_RULES) {
        const train: ExitResult[] = [];
        const test: ExitResult[] = [];
        for (const row of paths) {
          const delayed = row.points.find(
            (point) =>
              point.timestampMs >= row.triggerMs + delayMs &&
              point.timestampMs <=
                row.triggerMs + delayMs + MAX_DELAY_POINT_GAP_MS
          );
          if (!delayed) continue;
          const result = simulateExit(
            {
              timestamp: delayed.timestampMs / 1000,
              marketCapUsd: delayed.marketCapUsd,
            },
            row.points
              .filter((point) => point.timestampMs > delayed.timestampMs)
              .map(
                (point): PathPoint => ({
                  timestamp: point.timestampMs / 1000,
                  marketCapUsd: point.marketCapUsd,
                })
              ),
            rule,
            roundTripCostBps
          );
          if (row.triggerMs <= splitMs) train.push(result);
          else test.push(result);
        }
        scenarios.push({
          delayMs,
          roundTripCostBps,
          rule,
          train: summarize(train),
          test: summarize(test),
        });
      }
    }
  }
  const minimumTrain = Math.max(10, Math.floor(paths.length * 0.2));
  const minimumTest = Math.max(5, Math.floor(paths.length * 0.1));
  const viable = scenarios
    .filter(
      (row) =>
        row.train.entries >= minimumTrain &&
        row.test.entries >= minimumTest &&
        row.train.equalBetPnlSol > 0
    )
    .sort(
      (a, b) =>
        b.train.equalBetPnlSol - a.train.equalBetPnlSol ||
        (b.train.profitFactor ?? Infinity) -
          (a.train.profitFactor ?? Infinity)
    );
  const output = {
    generatedAt: new Date().toISOString(),
    walletTemplate: sweep.wallet,
    profile,
    sourceProfileId: projected.sourceProfileId,
    droppedGates: projected.droppedGates,
    inputs: { sweepPath, eventFiles },
    methodology: {
      split: "chronological 70/30 train/test by independent market trigger",
      delaysMs: DELAYS_MS,
      maxDelayPointGapMs: MAX_DELAY_POINT_GAP_MS,
      roundTripCostBps: ROUND_TRIP_COST_BPS,
      exitRules: EXIT_RULES,
      warning:
        "This replay uses Edge stream timestamps and independent exits. It does not assume the source wallet's exit.",
    },
    coverage: {
      parsedRows,
      triggeredMints: paths.length,
      trainMints: paths.filter((row) => row.triggerMs <= splitMs).length,
      testMints: paths.filter((row) => row.triggerMs > splitMs).length,
      scenarios: scenarios.length,
      viableScenarios: viable.length,
    },
    topScenarios: viable.slice(0, 30),
  };
  const dataDir = path.resolve(process.cwd(), "data");
  await mkdir(dataDir, { recursive: true });
  const jsonPath = path.join(dataDir, `${outputTag}.json`);
  const mdPath = path.join(dataDir, `${outputTag}.md`);
  await writeFile(jsonPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  const markdown = [
    "# Wallet-Derived Forward Latency And Exit Replay",
    "",
    `Generated: ${output.generatedAt}`,
    `Template wallet: \`${output.walletTemplate}\``,
    `Profile: \`${profile.id}\``,
    `Source profile: \`${output.sourceProfileId}\``,
    `Dropped unsupported gates: ${output.droppedGates.join(", ") || "none"}`,
    "",
    "## Coverage",
    "",
    `- Parsed stream rows: ${parsedRows}`,
    `- Triggered market mints: ${paths.length}`,
    `- Chronological train/test mints: ${output.coverage.trainMints}/${output.coverage.testMints}`,
    `- Viable train scenarios: ${viable.length}`,
    "",
    "## Top Train-Selected Scenarios",
    "",
    "| Delay | Cost | Exit | Train n | Train PF | Train PnL | Test n | Test PF | Test PnL | Test win | Test stops |",
    "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...viable.slice(0, 30).map(
      (row) =>
        `| ${row.delayMs}ms | ${row.roundTripCostBps}bps | ${row.rule.id} | ${row.train.entries} | ${row.train.profitFactor?.toFixed(2) ?? "-"} | ${row.train.equalBetPnlSol.toFixed(3)} | ${row.test.entries} | ${row.test.profitFactor?.toFixed(2) ?? "-"} | ${row.test.equalBetPnlSol.toFixed(3)} | ${row.test.winRatePct.toFixed(1)}% | ${row.test.stopLosses} |`
    ),
    "",
    "## Decision Rule",
    "",
    "- A scenario is selected using only the chronological train partition.",
    "- The later test result is the decision evidence.",
    "- Zero viable scenarios is a valid rejection of the template under retained market conditions.",
    "",
  ].join("\n");
  await writeFile(mdPath, `${markdown}\n`, "utf8");
  console.log(
    JSON.stringify(
      {
        jsonPath,
        mdPath,
        coverage: output.coverage,
        top: viable.slice(0, 5),
      },
      null,
      2
    )
  );
}

async function main(): Promise<void> {
  const sweepPath = path.resolve(process.argv[2] ?? "");
  const outputTag = process.argv[3] ?? "wallet-causal-forward-replay";
  const eventFiles = process.argv.slice(4).map((file) => path.resolve(file));
  if (!process.argv[2] || !process.argv[3] || !eventFiles.length) {
    throw new Error(
      "usage: tsx src/walletCausalForwardReplay.ts SWEEP.json OUTPUT_TAG STREAM.jsonl [MORE.jsonl...]"
    );
  }
  await runForwardReplay(sweepPath, eventFiles, outputTag);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  });
}
