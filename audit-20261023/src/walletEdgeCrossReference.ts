import { createReadStream } from "node:fs";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline";

type LedgerRow = {
  mint: string;
  route: "pump" | "bags" | "other";
  entryTs: number;
  entryIso: string;
  firstBuySol: number;
  entryMarketCapUsd: number;
  sourceExitRoiPct: number;
  realizedPnlSol: number;
  holdMinutes: number;
};

type LedgerReport = {
  wallet: string;
  coverage: Record<string, unknown>;
  rows: LedgerRow[];
};

type Context = {
  uniqueWallets60?: number;
  buyCount60?: number;
  sellCount60?: number;
  volumeBuySol60?: number;
  volumeSellSol60?: number;
  netBuySol60?: number;
};

type Observation = {
  mint: string;
  chainMs: number;
  observedMs?: number;
  signature?: string;
  wallet?: string;
  side?: string;
  score?: number;
  decision?: string;
  sourceBuySol?: number;
  marketCapUsd?: number;
  detector?: string;
  triggerProvider?: string;
  context: Context;
};

type Rejection = {
  mint: string;
  chainMs: number;
  gate?: string;
  reason?: string;
  stage?: string;
};

type Profile = {
  name: string;
  minScore: number;
  maxScore: number;
  minMc: number;
  maxMc: number;
  minSource: number;
  maxSource: number;
  maxWallets: number;
  maxBuys: number;
  maxSells: number;
  maxBuyVolume: number;
};

type MatchedCampaign = {
  ledger: LedgerRow;
  outcome: "loss" | "small_win" | "scalp_win" | "runner";
  firstPre?: Observation;
  closestPre?: Observation;
  sameSecond?: Observation;
  firstPost?: Observation;
  closestRecordedRejection?: Rejection;
};

const LOOKBACK_MS = Number(process.env.WALLET_EDGE_LOOKBACK_MS ?? "300000");
const POST_WINDOW_MS = Number(process.env.WALLET_EDGE_POST_WINDOW_MS ?? "60000");
const STRICT_PRE_MS = Number(process.env.WALLET_EDGE_STRICT_PRE_MS ?? "1000");
const FIXED_BET_SOL = Number(process.env.WALLET_EDGE_FIXED_BET_SOL ?? "0.5");

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function timestampMs(value: unknown): number | undefined {
  const numeric = asNumber(value);
  if (numeric != null) return numeric < 10_000_000_000 ? numeric * 1000 : numeric;
  const parsed = typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : undefined;
}

function median(values: number[]): number | null {
  const rows = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!rows.length) return null;
  const mid = Math.floor(rows.length / 2);
  return rows.length % 2 ? rows[mid]! : (rows[mid - 1]! + rows[mid]!) / 2;
}

function average(values: number[]): number | null {
  const rows = values.filter(Number.isFinite);
  return rows.length ? rows.reduce((sum, value) => sum + value, 0) / rows.length : null;
}

function profitFactor(values: number[]): number | null {
  const positive = values.filter((value) => value > 0).reduce((sum, value) => sum + value, 0);
  const negative = Math.abs(values.filter((value) => value < 0).reduce((sum, value) => sum + value, 0));
  return negative > 0 ? positive / negative : positive > 0 ? null : 0;
}

function pct(value: number, total: number): number {
  return (value / Math.max(1, total)) * 100;
}

function fmtPct(value: number | null | undefined): string {
  return value == null ? "-" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtUsd(value: number | null | undefined): string {
  return value == null ? "-" : `$${Math.round(value).toLocaleString("en-US")}`;
}

function outcome(row: LedgerRow): MatchedCampaign["outcome"] {
  if (row.sourceExitRoiPct < 0) return "loss";
  if (row.sourceExitRoiPct >= 50) return "runner";
  if (row.sourceExitRoiPct >= 10) return "scalp_win";
  return "small_win";
}

function contextFrom(value: unknown): Context {
  if (!isRecord(value)) return {};
  return {
    uniqueWallets60: asNumber(value.uniqueWallets60),
    buyCount60: asNumber(value.buyCount60),
    sellCount60: asNumber(value.sellCount60),
    volumeBuySol60: asNumber(value.volumeBuySol60),
    volumeSellSol60: asNumber(value.volumeSellSol60),
    netBuySol60: asNumber(value.netBuySol60),
  };
}

function normalizeObservation(row: Record<string, unknown>): Observation | null {
  const signal = isRecord(row.signal) ? row.signal : row;
  const scored = isRecord(row.scored) ? row.scored : {};
  const mint = asString(signal.mint) ?? asString(row.mint);
  const chainMs =
    timestampMs(signal.timestamp) ??
    timestampMs(signal.createdAt) ??
    timestampMs(row.createdAt) ??
    timestampMs(row.at);
  if (!mint || chainMs == null) return null;
  const context = contextFrom(row.context ?? signal.context);
  return {
    mint,
    chainMs,
    observedMs:
      timestampMs(signal.createdAt) ?? timestampMs(row.createdAt) ?? timestampMs(row.at),
    signature: asString(signal.signature) ?? asString(row.signature),
    wallet: asString(signal.wallet) ?? asString(row.wallet),
    side: asString(signal.side) ?? asString(row.side),
    score: asNumber(row.score) ?? asNumber(signal.score) ?? asNumber(scored.score),
    decision: asString(row.decision) ?? asString(signal.decision),
    sourceBuySol: asNumber(row.sourceBuySol) ?? asNumber(signal.sourceBuySol),
    marketCapUsd:
      asNumber(row.inferredMarketCapUsd) ??
      asNumber(signal.inferredMarketCapUsd) ??
      asNumber(row.entryMarketCapUsd),
    detector: asString(signal.detector) ?? asString(row.detector),
    triggerProvider:
      asString(signal.triggerProvider) ?? asString(row.triggerProvider),
    context,
  };
}

function normalizeRejection(row: Record<string, unknown>): Rejection | null {
  const mint =
    asString(row.mint) ?? (isRecord(row.signal) ? asString(row.signal.mint) : undefined);
  const signal = isRecord(row.signal) ? row.signal : {};
  const chainMs =
    timestampMs(signal.timestamp) ??
    timestampMs(row.at) ??
    timestampMs(row.createdAt);
  if (!mint || chainMs == null) return null;
  return {
    mint,
    chainMs,
    gate: asString(row.gate),
    reason: asString(row.reason),
    stage: asString(row.stage),
  };
}

async function findFiles(root: string, names: Set<string>): Promise<string[]> {
  const output: string[] = [];
  async function walk(directory: string): Promise<void> {
    let entries;
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(target);
      else if (entry.isFile() && names.has(entry.name)) output.push(target);
    }
  }
  await walk(root);
  return output.sort();
}

async function scanJsonl(
  files: string[],
  targetMints: Set<string>,
  onRow: (row: Record<string, unknown>, file: string) => void
): Promise<{ lines: number; parsed: number; minMs: number | null; maxMs: number | null }> {
  let lines = 0;
  let parsed = 0;
  let minMs: number | null = null;
  let maxMs: number | null = null;
  for (const file of files) {
    const reader = createInterface({
      input: createReadStream(file, { encoding: "utf8" }),
      crlfDelay: Infinity,
    });
    for await (const line of reader) {
      lines += 1;
      const rawTimestamp = /"timestamp"\s*:\s*(\d+(?:\.\d+)?)/.exec(line)?.[1];
      const rawCreatedAt = /"(?:createdAt|at)"\s*:\s*"([^"]+)"/.exec(line)?.[1];
      const rowMs = timestampMs(rawTimestamp ?? rawCreatedAt);
      if (rowMs != null) {
        minMs = minMs == null ? rowMs : Math.min(minMs, rowMs);
        maxMs = maxMs == null ? rowMs : Math.max(maxMs, rowMs);
      }
      const mintMatch = /"mint"\s*:\s*"([^"]+)"/.exec(line);
      if (!mintMatch?.[1] || !targetMints.has(mintMatch[1])) continue;
      try {
        const row = JSON.parse(line.replace(/^\uFEFF/, "")) as unknown;
        if (!isRecord(row)) continue;
        parsed += 1;
        onRow(row, file);
      } catch {}
    }
  }
  return { lines, parsed, minMs, maxMs };
}

function parseEnv(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    values[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
  }
  return values;
}

function envNumber(env: Record<string, string>, key: string, fallback: number): number {
  const value = Number(env[key]);
  return Number.isFinite(value) ? value : fallback;
}

function profilesFromEnv(env: Record<string, string>): Profile[] {
  return [
    {
      name: "current-edge-env",
      minScore: envNumber(env, "LAUNCH_MIN_SCORE", 85),
      maxScore: envNumber(env, "LAUNCH_MAX_SCORE", 94),
      minMc: envNumber(env, "LAUNCH_MIN_ENTRY_MC_USD", 2_000),
      maxMc: envNumber(env, "LAUNCH_MAX_ENTRY_MC_USD", 4_000),
      minSource: envNumber(env, "LAUNCH_MIN_SOURCE_BUY_SOL", 0.25),
      maxSource: envNumber(env, "LAUNCH_MAX_SOURCE_BUY_SOL", 1.24),
      maxWallets: envNumber(env, "LAUNCH_MAX_UNIQUE_WALLETS_60", 12),
      maxBuys: envNumber(env, "LAUNCH_MAX_BUY_COUNT_60", 8),
      maxSells: envNumber(env, "LAUNCH_MAX_SELL_COUNT_60", 8),
      maxBuyVolume: envNumber(env, "LAUNCH_MAX_BUY_VOLUME_SOL_60", Infinity),
    },
    {
      name: "runner-aggregate",
      minScore: 85,
      maxScore: 94,
      minMc: 2_000,
      maxMc: 4_000,
      minSource: 0.25,
      maxSource: 1.24,
      maxWallets: 12,
      maxBuys: 8,
      maxSells: 8,
      maxBuyVolume: Infinity,
    },
    {
      name: "wide-scout",
      minScore: 80,
      maxScore: 89,
      minMc: 1_500,
      maxMc: 5_000,
      minSource: 0.2,
      maxSource: 1.5,
      maxWallets: 15,
      maxBuys: 25,
      maxSells: 8,
      maxBuyVolume: Infinity,
    },
  ];
}

function profileGates(observation: Observation, profile: Profile): string[] {
  const gates: string[] = [];
  if (!observation.mint.endsWith("pump")) gates.push("route");
  if (observation.score == null) gates.push("score_missing");
  else if (observation.score < profile.minScore) gates.push("score_low");
  else if (observation.score > profile.maxScore) gates.push("score_high");
  if (observation.marketCapUsd == null) gates.push("entry_mc_missing");
  else if (observation.marketCapUsd < profile.minMc) gates.push("entry_mc_low");
  else if (observation.marketCapUsd >= profile.maxMc) gates.push("entry_mc_high");
  if (observation.sourceBuySol == null) gates.push("source_buy_missing");
  else if (observation.sourceBuySol < profile.minSource) gates.push("source_buy_low");
  else if (observation.sourceBuySol > profile.maxSource) gates.push("source_buy_high");
  const context = observation.context;
  if (context.uniqueWallets60 != null && context.uniqueWallets60 > profile.maxWallets) {
    gates.push("wallet_crowding");
  }
  if (context.buyCount60 != null && context.buyCount60 > profile.maxBuys) {
    gates.push("buy_crowding");
  }
  if (context.sellCount60 != null && context.sellCount60 > profile.maxSells) {
    gates.push("sell_crowding");
  }
  if (context.volumeBuySol60 != null && context.volumeBuySol60 > profile.maxBuyVolume) {
    gates.push("buy_volume");
  }
  return gates;
}

function summarizeRows(rows: LedgerRow[]) {
  const rois = rows.map((row) => row.sourceExitRoiPct);
  return {
    n: rows.length,
    wins: rows.filter((row) => row.sourceExitRoiPct > 0).length,
    winRatePct: pct(rows.filter((row) => row.sourceExitRoiPct > 0).length, rows.length),
    avgRoiPct: average(rois),
    medianRoiPct: median(rois),
    profitFactor: profitFactor(rois),
    equalBetPnlSol: rois.reduce((sum, roi) => sum + FIXED_BET_SOL * (roi / 100), 0),
  };
}

function featureComparison(matches: MatchedCampaign[]) {
  const rows = matches.filter((match) => match.closestPre);
  const groups = {
    winners: rows.filter((row) => row.ledger.sourceExitRoiPct > 0),
    losers: rows.filter((row) => row.ledger.sourceExitRoiPct < 0),
    scalpWins: rows.filter((row) => row.ledger.sourceExitRoiPct >= 10),
    runners: rows.filter((row) => row.ledger.sourceExitRoiPct >= 50),
  };
  const summarize = (items: MatchedCampaign[]) => ({
    n: items.length,
    score: median(items.map((item) => item.closestPre?.score ?? NaN)),
    scannerMcUsd: median(items.map((item) => item.closestPre?.marketCapUsd ?? NaN)),
    sourceBuySol: median(items.map((item) => item.closestPre?.sourceBuySol ?? NaN)),
    uniqueWallets60: median(items.map((item) => item.closestPre?.context.uniqueWallets60 ?? NaN)),
    buyCount60: median(items.map((item) => item.closestPre?.context.buyCount60 ?? NaN)),
    sellCount60: median(items.map((item) => item.closestPre?.context.sellCount60 ?? NaN)),
    buyVolumeSol60: median(items.map((item) => item.closestPre?.context.volumeBuySol60 ?? NaN)),
    leadSeconds: median(items.map((item) => (item.ledger.entryTs * 1000 - item.closestPre!.chainMs) / 1000)),
  });
  return Object.fromEntries(
    Object.entries(groups).map(([name, items]) => [name, summarize(items)])
  );
}

async function main(): Promise<void> {
  const ledgerPath = path.resolve(process.argv[2] ?? "");
  const edgeRoot = path.resolve(process.argv[3] ?? "");
  const outputTag = process.argv[4] ?? "wallet-edge-cross-reference";
  if (!process.argv[2] || !process.argv[3]) {
    throw new Error(
      "usage: tsx src/walletEdgeCrossReference.ts LEDGER.json EDGE_ROOT [outputTag]"
    );
  }
  const ledger = JSON.parse(
    (await readFile(ledgerPath, "utf8")).replace(/^\uFEFF/, "")
  ) as LedgerReport;
  if (!Array.isArray(ledger.rows) || !ledger.rows.length) {
    throw new Error("Ledger report has no campaign rows");
  }
  const targetMints = new Set(ledger.rows.map((row) => row.mint));
  const signalFiles = await findFiles(path.join(edgeRoot, "data"), new Set(["launch-paper-signals.jsonl"]));
  const rejectionFiles = await findFiles(
    path.join(edgeRoot, "data"),
    new Set(["launch-paper-rejections.jsonl"])
  );
  if (!signalFiles.length) throw new Error(`No launch-paper-signals.jsonl found under ${edgeRoot}`);

  const observationsByMint = new Map<string, Observation[]>();
  const rejectionsByMint = new Map<string, Rejection[]>();
  const signalScan = await scanJsonl(signalFiles, targetMints, (row) => {
    const observation = normalizeObservation(row);
    if (!observation || !targetMints.has(observation.mint)) return;
    const rows = observationsByMint.get(observation.mint) ?? [];
    rows.push(observation);
    observationsByMint.set(observation.mint, rows);
  });
  const rejectionScan = await scanJsonl(rejectionFiles, targetMints, (row) => {
    const rejection = normalizeRejection(row);
    if (!rejection || !targetMints.has(rejection.mint)) return;
    const rows = rejectionsByMint.get(rejection.mint) ?? [];
    rows.push(rejection);
    rejectionsByMint.set(rejection.mint, rows);
  });

  for (const rows of observationsByMint.values()) {
    rows.sort((a, b) => a.chainMs - b.chainMs);
  }
  for (const rows of rejectionsByMint.values()) rows.sort((a, b) => a.chainMs - b.chainMs);

  const matches: MatchedCampaign[] = ledger.rows.map((row) => {
    const entryMs = row.entryTs * 1000;
    const observations = observationsByMint.get(row.mint) ?? [];
    const inWindow = observations.filter(
      (observation) =>
        observation.chainMs >= entryMs - LOOKBACK_MS &&
        observation.chainMs <= entryMs + POST_WINDOW_MS
    );
    const strictPre = inWindow.filter(
      (observation) => observation.chainMs <= entryMs - STRICT_PRE_MS
    );
    const sameSecond = inWindow.filter(
      (observation) => Math.abs(observation.chainMs - entryMs) < STRICT_PRE_MS
    );
    const post = inWindow.filter((observation) => observation.chainMs >= entryMs + STRICT_PRE_MS);
    const rejections = (rejectionsByMint.get(row.mint) ?? []).filter(
      (rejection) =>
        rejection.chainMs >= entryMs - LOOKBACK_MS && rejection.chainMs <= entryMs
    );
    return {
      ledger: row,
      outcome: outcome(row),
      firstPre: strictPre[0],
      closestPre: strictPre[strictPre.length - 1],
      sameSecond: sameSecond[0],
      firstPost: post[0],
      closestRecordedRejection: rejections[rejections.length - 1],
    };
  });

  const edgeStart = signalScan.minMs;
  const edgeEnd = signalScan.maxMs;
  const overlapCampaigns =
    edgeStart == null || edgeEnd == null
      ? []
      : matches.filter((match) => {
          const entryMs = match.ledger.entryTs * 1000;
          return entryMs >= edgeStart && entryMs <= edgeEnd;
        });
  const strictMatches = matches.filter((match) => match.closestPre);
  const coverage = {
    ledgerCampaigns: matches.length,
    edgeSignalFiles: signalFiles.length,
    edgeRejectionFiles: rejectionFiles.length,
    signalLinesScanned: signalScan.lines,
    relevantSignalRows: signalScan.parsed,
    rejectionLinesScanned: rejectionScan.lines,
    relevantRejectionRows: rejectionScan.parsed,
    edgeStartIso: edgeStart == null ? null : new Date(edgeStart).toISOString(),
    edgeEndIso: edgeEnd == null ? null : new Date(edgeEnd).toISOString(),
    campaignsInsideEdgeWindow: overlapCampaigns.length,
    strictPreEntryMatches: strictMatches.length,
    sameSecondOnly: matches.filter((match) => !match.closestPre && match.sameSecond).length,
    postEntryOnly: matches.filter(
      (match) => !match.closestPre && !match.sameSecond && match.firstPost
    ).length,
    unseen: matches.filter(
      (match) => !match.closestPre && !match.sameSecond && !match.firstPost
    ).length,
    strictCoverageAllPct: pct(strictMatches.length, matches.length),
    strictCoverageOverlapPct: pct(
      overlapCampaigns.filter((match) => match.closestPre).length,
      overlapCampaigns.length
    ),
  };

  let edgeEnv: Record<string, string> = {};
  try {
    edgeEnv = parseEnv(await readFile(path.join(edgeRoot, ".env"), "utf8"));
  } catch {}
  const profiles = profilesFromEnv(edgeEnv);
  const profileResults = profiles.map((profile) => {
    const evaluated = matches.map((match) => {
      const observations = (observationsByMint.get(match.ledger.mint) ?? []).filter(
        (observation) =>
          observation.chainMs >= match.ledger.entryTs * 1000 - LOOKBACK_MS &&
          observation.chainMs <= match.ledger.entryTs * 1000 - STRICT_PRE_MS
      );
      const passed = observations.find((observation) => profileGates(observation, profile).length === 0);
      const snapshot = passed ?? observations[observations.length - 1];
      return {
        match,
        passed,
        snapshot,
        gates: snapshot ? profileGates(snapshot, profile) : ["not_seen_pre_entry"],
      };
    });
    const passedRows = evaluated.filter((row) => row.passed).map((row) => row.match.ledger);
    const observableWinners = evaluated.filter(
      (row) => row.snapshot && row.match.ledger.sourceExitRoiPct > 0
    );
    const observableLosers = evaluated.filter(
      (row) => row.snapshot && row.match.ledger.sourceExitRoiPct < 0
    );
    const gatesOnWinners = new Map<string, number>();
    for (const row of evaluated.filter(
      (item) => item.snapshot && !item.passed && item.match.ledger.sourceExitRoiPct > 0
    )) {
      for (const gate of row.gates) {
        gatesOnWinners.set(gate, (gatesOnWinners.get(gate) ?? 0) + 1);
      }
    }
    return {
      profile,
      summary: summarizeRows(passedRows),
      strictObservable: evaluated.filter((row) => row.snapshot).length,
      winnerCapturePct: pct(
        passedRows.filter((row) => row.sourceExitRoiPct > 0).length,
        observableWinners.length
      ),
      scalpWinCapturePct: pct(
        passedRows.filter((row) => row.sourceExitRoiPct >= 10).length,
        evaluated.filter(
          (row) => row.snapshot && row.match.ledger.sourceExitRoiPct >= 10
        ).length
      ),
      runnerCapturePct: pct(
        passedRows.filter((row) => row.sourceExitRoiPct >= 50).length,
        evaluated.filter(
          (row) => row.snapshot && row.match.ledger.sourceExitRoiPct >= 50
        ).length
      ),
      loserRejectionPct: pct(
        observableLosers.filter((row) => !row.passed).length,
        observableLosers.length
      ),
      topMissedWinnerGates: [...gatesOnWinners.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 12)
        .map(([gate, count]) => ({ gate, count })),
    };
  });

  const output = {
    generatedAt: new Date().toISOString(),
    wallet: ledger.wallet,
    ledgerPath,
    edgeRoot,
    methodology: {
      lookbackMs: LOOKBACK_MS,
      postWindowMs: POST_WINDOW_MS,
      strictPreEntryMs: STRICT_PRE_MS,
      fixedBetSol: FIXED_BET_SOL,
      caution:
        "Helius wallet entry timestamps are second-level. Strict pre-entry requires at least one full second of lead; same-second observations are reported separately and are not counted as independent early detection.",
    },
    ledgerCoverage: ledger.coverage,
    coverage,
    outcomes: {
      all: summarizeRows(matches.map((match) => match.ledger)),
      strictPreMatched: summarizeRows(strictMatches.map((match) => match.ledger)),
      winners: matches.filter((match) => match.ledger.sourceExitRoiPct > 0).length,
      losses: matches.filter((match) => match.ledger.sourceExitRoiPct < 0).length,
      scalpWins: matches.filter((match) => match.ledger.sourceExitRoiPct >= 10).length,
      runners: matches.filter((match) => match.ledger.sourceExitRoiPct >= 50).length,
    },
    featureComparison: featureComparison(matches),
    profiles: profileResults,
    rows: matches.map((match) => ({
      ...match.ledger,
      outcome: match.outcome,
      firstPreLeadSeconds: match.firstPre
        ? (match.ledger.entryTs * 1000 - match.firstPre.chainMs) / 1000
        : null,
      closestPreLeadSeconds: match.closestPre
        ? (match.ledger.entryTs * 1000 - match.closestPre.chainMs) / 1000
        : null,
      firstPre: match.firstPre ?? null,
      closestPre: match.closestPre ?? null,
      sameSecond: match.sameSecond ?? null,
      firstPost: match.firstPost ?? null,
      recordedRejection: match.closestRecordedRejection ?? null,
      profileResults: Object.fromEntries(
        profiles.map((profile) => {
          const observations = (observationsByMint.get(match.ledger.mint) ?? []).filter(
            (observation) =>
              observation.chainMs >= match.ledger.entryTs * 1000 - LOOKBACK_MS &&
              observation.chainMs <= match.ledger.entryTs * 1000 - STRICT_PRE_MS
          );
          const passed = observations.find(
            (observation) => profileGates(observation, profile).length === 0
          );
          const snapshot = passed ?? observations[observations.length - 1];
          return [
            profile.name,
            {
              passed: Boolean(passed),
              gates: snapshot ? profileGates(snapshot, profile) : ["not_seen_pre_entry"],
              passLeadSeconds: passed
                ? (match.ledger.entryTs * 1000 - passed.chainMs) / 1000
                : null,
            },
          ];
        })
      ),
    })),
  };

  const dataDir = path.resolve(process.cwd(), "data");
  await mkdir(dataDir, { recursive: true });
  const jsonPath = path.join(dataDir, `${outputTag}.json`);
  const mdPath = path.join(dataDir, `${outputTag}.md`);
  await writeFile(jsonPath, JSON.stringify(output, null, 2), "utf8");
  const feature = output.featureComparison as Record<
    string,
    {
      n: number;
      score: number | null;
      scannerMcUsd: number | null;
      sourceBuySol: number | null;
      uniqueWallets60: number | null;
      buyCount60: number | null;
      sellCount60: number | null;
      buyVolumeSol60: number | null;
      leadSeconds: number | null;
    }
  >;
  const markdown = [
    "# Wallet To EdgeSniffer Cross-Reference",
    "",
    `Generated: ${output.generatedAt}`,
    `Wallet: \`${ledger.wallet}\``,
    "",
    "## Coverage",
    "",
    `- Ledger campaigns: ${coverage.ledgerCampaigns}`,
    `- Edge data window: ${coverage.edgeStartIso ?? "-"} to ${coverage.edgeEndIso ?? "-"}`,
    `- Campaigns inside retained Edge window: ${coverage.campaignsInsideEdgeWindow}`,
    `- Strict pre-entry matches: ${coverage.strictPreEntryMatches}`,
    `- Strict coverage, all history: ${coverage.strictCoverageAllPct.toFixed(1)}%`,
    `- Strict coverage, overlapping history: ${coverage.strictCoverageOverlapPct.toFixed(1)}%`,
    `- Same-second only: ${coverage.sameSecondOnly}`,
    `- Post-entry only: ${coverage.postEntryOnly}`,
    `- Unseen: ${coverage.unseen}`,
    "",
    "Strict pre-entry requires at least one full second of lead. Same-second signals are not credited as independent early detection.",
    "",
    "## Wallet Outcomes",
    "",
    `- Closed campaigns: ${output.outcomes.all.n}`,
    `- Win rate: ${output.outcomes.all.winRatePct.toFixed(1)}%`,
    `- Median ROI: ${fmtPct(output.outcomes.all.medianRoiPct)}`,
    `- Equal-size PF: ${output.outcomes.all.profitFactor?.toFixed(2) ?? "-"}`,
    `- Scalp wins (>=10%): ${output.outcomes.scalpWins}`,
    `- Runners (>=50%): ${output.outcomes.runners}`,
    "",
    "## Pre-Entry Indicator Comparison",
    "",
    "| Group | n | Score | Scanner MC | Source buy | Wallets60 | Buys60 | Sells60 | BuyVol60 | Lead |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...["winners", "losers", "scalpWins", "runners"].map((name) => {
      const row = feature[name]!;
      return `| ${name} | ${row.n} | ${row.score?.toFixed(1) ?? "-"} | ${fmtUsd(row.scannerMcUsd)} | ${row.sourceBuySol?.toFixed(3) ?? "-"} | ${row.uniqueWallets60?.toFixed(1) ?? "-"} | ${row.buyCount60?.toFixed(1) ?? "-"} | ${row.sellCount60?.toFixed(1) ?? "-"} | ${row.buyVolumeSol60?.toFixed(2) ?? "-"} | ${row.leadSeconds?.toFixed(1) ?? "-"}s |`;
    }),
    "",
    "## Profile Replay",
    "",
    "| Profile | Seen pre | Passed | Win | Median ROI | PF | PnL | Winner capture | >=10% capture | Runner capture | Loser rejection |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...profileResults.map(
      (result) =>
        `| ${result.profile.name} | ${result.strictObservable} | ${result.summary.n} | ${result.summary.winRatePct.toFixed(1)}% | ${fmtPct(result.summary.medianRoiPct)} | ${result.summary.profitFactor?.toFixed(2) ?? "-"} | ${result.summary.equalBetPnlSol.toFixed(3)} SOL | ${result.winnerCapturePct.toFixed(1)}% | ${result.scalpWinCapturePct.toFixed(1)}% | ${result.runnerCapturePct.toFixed(1)}% | ${result.loserRejectionPct.toFixed(1)}% |`
    ),
    "",
    "## Missed Winner Gates",
    "",
    ...profileResults.flatMap((result) => [
      `### ${result.profile.name}`,
      "",
      ...(result.topMissedWinnerGates.length
        ? result.topMissedWinnerGates.map((row) => `- ${row.gate}: ${row.count}`)
        : ["- None in matched sample"]),
      "",
    ]),
    "## Limits",
    "",
    "- This measures selection among wallet-bought campaigns; it does not yet measure market-wide false positives.",
    "- Missing retained EdgeSniffer history reduces coverage and must not be interpreted as a failed detector.",
    "- Profile results are historical/in-sample diagnostics, not permission for live trading.",
    "",
  ].join("\n");
  await writeFile(mdPath, `${markdown}\n`, "utf8");
  console.log(
    JSON.stringify(
      {
        jsonPath,
        mdPath,
        coverage,
        profileSummary: profileResults.map((row) => ({
          profile: row.profile.name,
          passed: row.summary.n,
          medianRoiPct: row.summary.medianRoiPct,
          profitFactor: row.summary.profitFactor,
          winnerCapturePct: row.winnerCapturePct,
          loserRejectionPct: row.loserRejectionPct,
        })),
      },
      null,
      2
    )
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
