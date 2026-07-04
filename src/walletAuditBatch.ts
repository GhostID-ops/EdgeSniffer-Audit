import "dotenv/config";
import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { runWalletQuantAudit } from "./walletQuantAudit.js";

type Lane = "scalper" | "runner";
type WalletConfig = {
  label: string;
  address: string;
  lane: Lane;
  enabled?: boolean;
  maxTxs?: number;
  entryLimit?: number;
};
type BatchConfig = {
  defaults?: { maxTxs?: number; entryLimit?: number; continueOnError?: boolean };
  wallets: WalletConfig[];
};
type WalletState = WalletConfig & {
  status: "pending" | "running" | "completed" | "failed";
  startedAt?: string;
  finishedAt?: string;
  output?: string;
  error?: string;
};
type BatchState = {
  startedAt: string;
  updatedAt: string;
  finishedAt?: string;
  configPath: string;
  runDir: string;
  wallets: WalletState[];
};
type CampaignRow = {
  entryTs: number;
  realizedPnlSol: number;
  realizedRoiPct: number | null;
  holdSeconds: number;
  closed: boolean;
};
type QuantReport = {
  wallet: string;
  sample: { oldestTxIso: string; newestTxIso: string };
  campaignRows: CampaignRow[];
};

function stamp(): string {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function safeLabel(label: string): string {
  const value = label.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  if (!value) throw new Error(`Invalid wallet label: ${label}`);
  return value;
}

function median(values: number[]): number | null {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid]! : (xs[mid - 1]! + xs[mid]!) / 2;
}

function summarize(rows: CampaignRow[]) {
  const closed = rows.filter((row) => row.closed && row.realizedRoiPct != null);
  const rois = closed.map((row) => row.realizedRoiPct!);
  const wins = rois.filter((roi) => roi > 0);
  const losses = rois.filter((roi) => roi < 0);
  const grossWin = wins.reduce((sum, roi) => sum + roi, 0);
  const grossLoss = Math.abs(losses.reduce((sum, roi) => sum + roi, 0));
  const pnl = closed.map((row) => row.realizedPnlSol).sort((a, b) => b - a);
  const totalPnlSol = pnl.reduce((sum, value) => sum + value, 0);
  return {
    trades: closed.length,
    totalPnlSol,
    winRatePct: (wins.length / Math.max(1, closed.length)) * 100,
    avgRoiPct: rois.reduce((sum, value) => sum + value, 0) / Math.max(1, rois.length),
    medianRoiPct: median(rois),
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? null : 0,
    worstRoiPct: rois.length ? Math.min(...rois) : null,
    medianHoldSeconds: median(closed.map((row) => row.holdSeconds)),
    top5PnlSharePct: totalPnlSol > 0 ? (pnl.slice(0, 5).reduce((sum, value) => sum + value, 0) / totalPnlSol) * 100 : null,
  };
}

function validateConfig(value: unknown): BatchConfig {
  if (!value || typeof value !== "object" || !Array.isArray((value as BatchConfig).wallets)) {
    throw new Error("Wallet audit config must contain a wallets array");
  }
  const config = value as BatchConfig;
  const labels = new Set<string>();
  for (const wallet of config.wallets) {
    if (!wallet.label || !wallet.address || !["scalper", "runner"].includes(wallet.lane)) {
      throw new Error("Every wallet needs label, address, and lane=scalper|runner");
    }
    if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(wallet.address)) {
      throw new Error(`Invalid Solana wallet address for ${wallet.label}`);
    }
    const label = safeLabel(wallet.label);
    if (labels.has(label)) throw new Error(`Duplicate wallet label: ${label}`);
    labels.add(label);
  }
  return config;
}

async function writeJsonAtomic(file: string, value: unknown): Promise<void> {
  const temp = `${file}.tmp`;
  await writeFile(temp, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temp, file);
}

async function writeCohortSummary(runDir: string, state: BatchState): Promise<void> {
  const completed = state.wallets.filter((wallet) => wallet.status === "completed" && wallet.output);
  const reports = await Promise.all(completed.map(async (wallet) => ({
    wallet,
    report: JSON.parse(await readFile(wallet.output!, "utf8")) as QuantReport,
  })));
  if (!reports.length) return;
  const overlapStartMs = Math.max(...reports.map(({ report }) => Date.parse(report.sample.oldestTxIso)));
  const overlapEndMs = Math.min(...reports.map(({ report }) => Date.parse(report.sample.newestTxIso)));
  const hasOverlap = overlapEndMs > overlapStartMs;
  const wallets = reports.map(({ wallet, report }) => {
    const rows = hasOverlap
      ? report.campaignRows.filter((row) => row.entryTs * 1000 >= overlapStartMs && row.entryTs * 1000 <= overlapEndMs)
      : report.campaignRows;
    return { label: wallet.label, address: wallet.address, lane: wallet.lane, ...summarize(rows) };
  });
  const lanes = (["scalper", "runner"] as const).map((lane) => {
    const rows = reports.flatMap(({ wallet, report }) => {
      if (wallet.lane !== lane) return [];
      return hasOverlap
        ? report.campaignRows.filter((row) => row.entryTs * 1000 >= overlapStartMs && row.entryTs * 1000 <= overlapEndMs)
        : report.campaignRows;
    });
    return { lane, wallets: reports.filter(({ wallet }) => wallet.lane === lane).length, ...summarize(rows) };
  });
  const summary = {
    generatedAt: new Date().toISOString(),
    comparisonWindow: hasOverlap
      ? { start: new Date(overlapStartMs).toISOString(), end: new Date(overlapEndMs).toISOString(), exactOverlap: true }
      : { start: null, end: null, exactOverlap: false },
    wallets,
    lanes,
    interpretationRules: [
      "Prefer profit factor, median ROI, worst ROI, and top-five concentration over win rate alone.",
      "Raw SOL PnL is not capital-normalized; ROI statistics are the fairer cross-wallet comparison.",
      "Latency replay is a separate report and requires millisecond local stream events.",
    ],
  };
  await writeJsonAtomic(path.join(runDir, "cohort-summary.json"), summary);
  const lines = [
    "# Wallet Cohort Summary",
    "",
    `Generated: ${summary.generatedAt}`,
    `Common window: ${summary.comparisonWindow.start ?? "unavailable"} to ${summary.comparisonWindow.end ?? "unavailable"}`,
    "",
    "| Wallet | Lane | Trades | PnL SOL | Win | Median ROI | PF | Worst ROI | Median Hold | Top-5 Share |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...wallets.map((row) => `| ${row.label} | ${row.lane} | ${row.trades} | ${row.totalPnlSol.toFixed(3)} | ${row.winRatePct.toFixed(1)}% | ${row.medianRoiPct?.toFixed(2) ?? "-"}% | ${row.profitFactor?.toFixed(2) ?? "-"} | ${row.worstRoiPct?.toFixed(2) ?? "-"}% | ${row.medianHoldSeconds?.toFixed(0) ?? "-"}s | ${row.top5PnlSharePct?.toFixed(1) ?? "-"}% |`),
    "",
    "## Lane Comparison",
    "",
    ...lanes.map((row) => `- ${row.lane}: wallets=${row.wallets} trades=${row.trades} win=${row.winRatePct.toFixed(1)}% medianROI=${row.medianRoiPct?.toFixed(2) ?? "-"}% PF=${row.profitFactor?.toFixed(2) ?? "-"} worst=${row.worstRoiPct?.toFixed(2) ?? "-"}% top5=${row.top5PnlSharePct?.toFixed(1) ?? "-"}%`),
    "",
    "Latency and impact are intentionally excluded here until millisecond local stream data is joined.",
  ];
  await writeFile(path.join(runDir, "cohort-summary.md"), `${lines.join("\n")}\n`, "utf8");
}

async function main(): Promise<void> {
  const configPath = path.resolve(process.argv[2] ?? "data/wallet-audit-wallets.json");
  const config = validateConfig(JSON.parse(await readFile(configPath, "utf8")));
  const runDir = path.resolve(process.argv[3] ?? path.join("data", "wallet-audits", `run-${stamp()}`));
  const stateFile = path.join(runDir, "state.json");
  const eventFile = path.join(runDir, "events.jsonl");
  await mkdir(runDir, { recursive: true });
  let resumed = false;
  let state: BatchState;
  try {
    state = JSON.parse(await readFile(stateFile, "utf8")) as BatchState;
    state.wallets.forEach((wallet) => {
      if (wallet.status === "running") wallet.status = "pending";
    });
    resumed = true;
  } catch {
    state = {
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      configPath,
      runDir,
      wallets: config.wallets.filter((wallet) => wallet.enabled !== false)
        .map((wallet) => ({ ...wallet, label: safeLabel(wallet.label), status: "pending" })),
    };
  }
  const log = async (event: string, detail: Record<string, unknown> = {}) => {
    const row = { ts: new Date().toISOString(), event, ...detail };
    await appendFile(eventFile, `${JSON.stringify(row)}\n`, "utf8");
    console.log(JSON.stringify(row));
  };
  const save = async () => {
    state.updatedAt = new Date().toISOString();
    await writeJsonAtomic(stateFile, state);
  };
  await save();
  await log(resumed ? "batch_resumed" : "batch_started", { wallets: state.wallets.length, runDir });

  for (const wallet of state.wallets) {
    if (wallet.status === "completed") continue;
    const maxTxs = wallet.maxTxs ?? config.defaults?.maxTxs ?? 10_000;
    const entryLimit = wallet.entryLimit ?? config.defaults?.entryLimit ?? 500;
    const output = path.join(runDir, `${wallet.label}.quant.json`);
    wallet.status = "running";
    wallet.startedAt = new Date().toISOString();
    wallet.output = output;
    await save();
    await log("wallet_started", { label: wallet.label, address: wallet.address, lane: wallet.lane, maxTxs, entryLimit });
    try {
      const report = await runWalletQuantAudit(wallet.address, maxTxs, entryLimit);
      await writeJsonAtomic(output, report);
      wallet.status = "completed";
      wallet.finishedAt = new Date().toISOString();
      await log("wallet_completed", { label: wallet.label, address: wallet.address, sample: report.sample, walletPerformance: report.walletPerformance });
    } catch (error) {
      wallet.status = "failed";
      wallet.finishedAt = new Date().toISOString();
      wallet.error = error instanceof Error ? error.message : String(error);
      await log("wallet_failed", { label: wallet.label, address: wallet.address, error: wallet.error });
      if (config.defaults?.continueOnError === false) {
        await save();
        throw error;
      }
    }
    await save();
  }
  state.finishedAt = new Date().toISOString();
  await save();
  await writeCohortSummary(runDir, state);
  await log("batch_finished", {
    completed: state.wallets.filter((wallet) => wallet.status === "completed").length,
    failed: state.wallets.filter((wallet) => wallet.status === "failed").length,
  });
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});