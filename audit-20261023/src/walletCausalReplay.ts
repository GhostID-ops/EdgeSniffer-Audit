import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { extractFlowEvent, type FlowEvent } from "./walletHistoricalPreEntryAudit.js";
import {
  campaignKey,
  median,
  profilePasses,
  simulateExit,
  type CausalFeatureRow,
  type CausalProfile,
  type ExitResult,
  type PathPoint,
} from "./walletCausalResearch.js";

type HistoricalRow = {
  ledger: {
    mint: string;
    entryTs: number;
    entrySignature?: string;
    entryMarketCapUsd: number;
    entrySolUsd: number;
  };
};

type HistoricalReport = {
  methodology?: { maxMintTransactions?: number };
  rows: HistoricalRow[];
};

type SweepReport = {
  wallet: string;
  topProfiles: Array<{
    profile: CausalProfile;
    selectedKeys: string[];
  }>;
  rows: CausalFeatureRow[];
};

type SlotRow = {
  ledger: { mint: string; entryTs: number };
  orderedSameSecondFlow?: Array<
    FlowEvent & {
      relation?: string;
      transactionIndex?: number | null;
    }
  >;
};

type SlotReport = { rows: SlotRow[] };
type CacheReport = { txs?: unknown[] };

const DELAY_EVENTS = [0, 1, 3, 5, 10];
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

async function readJson<T>(file: string): Promise<T> {
  return JSON.parse((await readFile(file, "utf8")).replace(/^\uFEFF/, "")) as T;
}

async function findCache(
  cacheDir: string,
  mint: string,
  entryTs: number,
  preferredMax: number | undefined
): Promise<string | null> {
  const prefix = `${mint}-${entryTs}-`;
  let files: string[];
  try {
    files = (await readdir(cacheDir)).filter(
      (file) => file.startsWith(prefix) && file.endsWith(".json")
    );
  } catch {
    return null;
  }
  files.sort((a, b) => {
    const limit = (file: string) =>
      Number(file.slice(prefix.length).replace(/\.json$/, ""));
    const preferredA = limit(a) === preferredMax ? 1 : 0;
    const preferredB = limit(b) === preferredMax ? 1 : 0;
    return preferredB - preferredA || limit(b) - limit(a);
  });
  return files[0] ? path.join(cacheDir, files[0]) : null;
}

function summarize(results: ExitResult[]) {
  const wins = results.filter((row) => row.roiPct > 0);
  const grossWin = wins.reduce((sum, row) => sum + row.roiPct, 0);
  const grossLoss = Math.abs(
    results
      .filter((row) => row.roiPct < 0)
      .reduce((sum, row) => sum + row.roiPct, 0)
  );
  return {
    entries: results.length,
    winRatePct: (wins.length / Math.max(1, results.length)) * 100,
    medianRoiPct: median(results.map((row) => row.roiPct)),
    avgRoiPct:
      results.reduce((sum, row) => sum + row.roiPct, 0) /
      Math.max(1, results.length),
    profitFactor:
      grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? null : 0,
    equalBetPnlSol:
      results.reduce((sum, row) => sum + 0.5 * (row.roiPct / 100), 0),
    stopLosses: results.filter((row) => row.reason === "stop_loss").length,
    takeProfits: results.filter((row) => row.reason === "take_profit").length,
  };
}

function dedupePoints(points: PathPoint[]): PathPoint[] {
  const seen = new Set<string>();
  return points
    .filter((point) => {
      const key = point.signature ?? `${point.timestamp}:${point.marketCapUsd}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return point.marketCapUsd > 0;
    })
    .sort(
      (a, b) =>
        a.timestamp - b.timestamp ||
        (a.transactionIndex ?? Number.MAX_SAFE_INTEGER) -
          (b.transactionIndex ?? Number.MAX_SAFE_INTEGER)
    );
}

export async function runCausalReplay(
  sweepPath: string,
  historyPath: string,
  slotPath: string,
  cacheDir: string,
  outputTag: string
): Promise<void> {
  const [sweep, history, slot] = await Promise.all([
    readJson<SweepReport>(sweepPath),
    readJson<HistoricalReport>(historyPath),
    readJson<SlotReport>(slotPath),
  ]);
  const profile = sweep.topProfiles[0]?.profile;
  if (!profile) throw new Error("Causal sweep has no viable top profile");
  const features = new Map(sweep.rows.map((row) => [row.key, row]));
  const slotRows = new Map(
    slot.rows.map((row) => [
      campaignKey(row.ledger.mint, row.ledger.entryTs),
      row,
    ])
  );
  const paths: Array<{
    key: string;
    entry: PathPoint;
    points: PathPoint[];
  }> = [];
  for (const row of history.rows) {
    const key = campaignKey(row.ledger.mint, row.ledger.entryTs);
    const feature = features.get(key);
    if (!feature || !profilePasses(feature, profile)) continue;
    const cachePath = await findCache(
      cacheDir,
      row.ledger.mint,
      row.ledger.entryTs,
      history.methodology?.maxMintTransactions
    );
    if (!cachePath) continue;
    const cache = await readJson<CacheReport>(cachePath);
    const decoded = (cache.txs ?? [])
      .map((tx) =>
        extractFlowEvent(
          tx as Parameters<typeof extractFlowEvent>[0],
          row.ledger.mint,
          row.ledger.entrySolUsd
        )
      )
      .filter((event): event is FlowEvent => Boolean(event))
      .filter(
        (event) =>
          event.marketCapUsd != null &&
          event.marketCapUsd > 0 &&
          event.signature !== row.ledger.entrySignature &&
          event.timestamp >= row.ledger.entryTs &&
          event.timestamp <= row.ledger.entryTs + 120
      )
      .map(
        (event): PathPoint => ({
          timestamp: event.timestamp,
          marketCapUsd: event.marketCapUsd!,
          signature: event.signature,
        })
      );
    const orderedSameSlot = (
      slotRows.get(key)?.orderedSameSecondFlow ?? []
    )
      .filter(
        (event) =>
          event.relation === "after_same_slot" &&
          event.marketCapUsd != null &&
          event.marketCapUsd > 0
      )
      .map(
        (event): PathPoint => ({
          timestamp: event.timestamp,
          marketCapUsd: event.marketCapUsd!,
          signature: event.signature,
          transactionIndex: event.transactionIndex,
        })
      );
    paths.push({
      key,
      entry: {
        timestamp: row.ledger.entryTs,
        marketCapUsd: row.ledger.entryMarketCapUsd,
      } satisfies PathPoint,
      points: dedupePoints([...orderedSameSlot, ...decoded]),
    });
  }
  const splitTs =
    sweep.rows[Math.max(0, Math.floor(sweep.rows.length * 0.7) - 1)]?.entryTs ??
    Infinity;
  const scenarios = [];
  for (const delayEvents of DELAY_EVENTS) {
    for (const roundTripCostBps of ROUND_TRIP_COST_BPS) {
      for (const rule of EXIT_RULES) {
        const train: ExitResult[] = [];
        const test: ExitResult[] = [];
        for (const pathRow of paths) {
          const delayedEntry =
            delayEvents === 0
              ? pathRow.entry
              : pathRow.points[delayEvents - 1];
          if (!delayedEntry) continue;
          const result = simulateExit(
            delayedEntry,
            pathRow.points.filter(
              (point) =>
                point.timestamp > delayedEntry.timestamp ||
                (point.transactionIndex ?? -1) >
                  (delayedEntry.transactionIndex ?? -1)
            ),
            rule,
            roundTripCostBps
          );
          const feature = features.get(pathRow.key);
          if ((feature?.entryTs ?? Infinity) <= splitTs) train.push(result);
          else test.push(result);
        }
        scenarios.push({
          delayEvents,
          roundTripCostBps,
          rule,
          train: summarize(train),
          test: summarize(test),
        });
      }
    }
  }
  const viable = scenarios
    .filter(
      (row) =>
        row.train.entries >= 5 &&
        row.test.entries >= 3 &&
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
    wallet: sweep.wallet,
    profile,
    methodology: {
      delayEvents: DELAY_EVENTS,
      roundTripCostBps: ROUND_TRIP_COST_BPS,
      exitRules: EXIT_RULES,
      warning:
        "Historical Helius timestamps are second-resolution. Delay is measured in subsequent decoded transactions, not milliseconds.",
      forwardRequirement:
        "Use audit:wallet-latency with millisecond Edge stream logs before paper approval.",
    },
    coverage: {
      selectedProfileRows: sweep.rows.filter((row) =>
        profilePasses(row, profile)
      ).length,
      pathRows: paths.length,
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
    "# Wallet Causal Latency And Exit Replay",
    "",
    `Generated: ${output.generatedAt}`,
    `Wallet: \`${output.wallet}\``,
    `Profile: \`${profile.id}\``,
    "",
    "## Coverage",
    "",
    `- Profile-selected rows: ${output.coverage.selectedProfileRows}`,
    `- Rows with post-entry paths: ${paths.length}`,
    `- Scenarios: ${scenarios.length}`,
    `- Viable train scenarios: ${viable.length}`,
    "",
    "Historical delay is counted in subsequent decoded transactions. It is not labeled in milliseconds because Helius timestamps are second-resolution.",
    "",
    "## Top Train-Selected Scenarios",
    "",
    "| Delay events | Cost | Exit | Train n | Train PF | Train PnL | Test n | Test PF | Test PnL | Test win | Test stops |",
    "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...viable.slice(0, 30).map(
      (row) =>
        `| ${row.delayEvents} | ${row.roundTripCostBps}bps | ${row.rule.id} | ${row.train.entries} | ${row.train.profitFactor?.toFixed(2) ?? "-"} | ${row.train.equalBetPnlSol.toFixed(3)} | ${row.test.entries} | ${row.test.profitFactor?.toFixed(2) ?? "-"} | ${row.test.equalBetPnlSol.toFixed(3)} | ${row.test.winRatePct.toFixed(1)}% | ${row.test.stopLosses} |`
    ),
    "",
    "## Limits",
    "",
    "- The feature profile was selected from source-wallet labels and remains research-only.",
    "- Market-control validity comes from the causal sweep report.",
    "- Sparse decoded paths can miss intratransaction highs and lows.",
    "- Run the forward millisecond replay against Edge stream logs before paper-opening this lane.",
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
  const historyPath = path.resolve(process.argv[3] ?? "");
  const slotPath = path.resolve(process.argv[4] ?? "");
  const cacheDir = path.resolve(
    process.argv[5] ?? path.join(process.cwd(), "data", "wallet-pre-entry-cache")
  );
  const outputTag = process.argv[6] ?? "wallet-causal-replay";
  if (!process.argv[2] || !process.argv[3] || !process.argv[4]) {
    throw new Error(
      "usage: tsx src/walletCausalReplay.ts SWEEP.json HISTORY.json SLOT.json [CACHE_DIR] [outputTag]"
    );
  }
  await runCausalReplay(
    sweepPath,
    historyPath,
    slotPath,
    cacheDir,
    outputTag
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  });
}
