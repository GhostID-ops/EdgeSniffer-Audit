import { promises as fs } from "node:fs";
import path from "node:path";
import { dataPath } from "./runtimePaths.js";
import {
  simulationMode,
  stopLossPct,
  takeProfitPct,
  hybridExitEnabled,
  hybridExitTpPct,
  hybridExitTpSellPct,
  hybridExitSourceExitRemaining,
  minHoldBeforeStopLossMs,
  mirrorSlippageBps,
  maxEntryPriceImpactPct,
} from "./config.js";

const TRADE_LOG_PATH = dataPath("trades.jsonl");

let directoryReady = false;
let warnedOnFailure = false;

async function ensureDirectory(): Promise<void> {
  if (directoryReady) return;
  await fs.mkdir(path.dirname(TRADE_LOG_PATH), { recursive: true });
  directoryReady = true;
}

export type TradeLogEntry = {
  ts: number;
  isoTs: string;
  mode: "simulation" | "live";
  side: "buy" | "sell" | "swap";
  /** Mirror tx signature (sim-prefixed in simulation mode). */
  mirrorSignature?: string;
  /** Source wallet's tx signature that triggered this mirror. */
  sourceSignature: string;
  sourceWallet: string;
  inputMint: string;
  outputMint: string;
  inputAmountRaw: number;
  outputAmountRaw: number;
  inputSymbol?: string;
  outputSymbol?: string;
  priceImpactPct?: number | null;
  marketCapUsd?: number | null;
  /** Incremental realized PnL in lamports for this sell fill. Buys have null. */
  realizedPnlLamports?: number | null;
  /** Incremental realized PnL % for this sell fill. Buys have null. */
  realizedPnlPct?: number | null;
  /** Cumulative realized PnL for this token position after this sell. */
  positionRealizedPnlLamports?: number | null;
  /** Risk-exit reason, if any (e.g. "Stop loss hit (-25%)"). */
  exitReason?: string | null;
  /** Snapshot of key config at trade time — so we can correlate behaviour with settings later. */
  config: {
    stopLossPct: number | null;
    takeProfitPct: number | null;
    hybridExitEnabled: boolean;
    hybridExitTpPct: number;
    hybridExitTpSellPct: number;
    hybridExitSourceExitRemaining: boolean;
    minHoldBeforeStopLossMs: number;
    mirrorSlippageBps: number;
    maxEntryPriceImpactPct: number | null;
  };
  /** Free-form note (e.g. "manual sell", "risk exit"). */
  note?: string;
  /** Set if the trade failed. */
  error?: string;
};

/**
 * Append one trade entry to the JSONL log on disk. Never throws — disk failures are warned once
 * and then suppressed so the hot path is never blocked.
 */
export async function appendTradeLog(entry: Omit<TradeLogEntry, "isoTs" | "mode" | "config">): Promise<void> {
  const full: TradeLogEntry = {
    ...entry,
    isoTs: new Date(entry.ts).toISOString(),
    mode: simulationMode ? "simulation" : "live",
    config: {
      stopLossPct,
      takeProfitPct,
      hybridExitEnabled,
      hybridExitTpPct,
      hybridExitTpSellPct,
      hybridExitSourceExitRemaining,
      minHoldBeforeStopLossMs,
      mirrorSlippageBps,
      maxEntryPriceImpactPct,
    },
  };
  try {
    await ensureDirectory();
    await fs.appendFile(TRADE_LOG_PATH, JSON.stringify(full) + "\n", "utf8");
  } catch (err) {
    if (!warnedOnFailure) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`⚠️  Trade log write failed (suppressed): ${msg} — path=${TRADE_LOG_PATH}`);
      warnedOnFailure = true;
    }
  }
}

/** Convenience: fire-and-forget so callers don't have to await. */
export function logTradeAsync(entry: Omit<TradeLogEntry, "isoTs" | "mode" | "config">): void {
  void appendTradeLog(entry);
}

export async function readRecentTradeLogs(options: { since?: number; limit?: number } = {}): Promise<TradeLogEntry[]> {
  try {
    await ensureDirectory();
    const text = await fs.readFile(TRADE_LOG_PATH, "utf8").catch((err: unknown) => {
      const code = typeof err === "object" && err && "code" in err ? String((err as { code?: unknown }).code) : "";
      if (code === "ENOENT") return "";
      throw err;
    });
    const since = Number.isFinite(options.since) ? Number(options.since) : 0;
    const limit = Math.max(1, Math.min(200, Math.floor(options.limit ?? 50)));
    const rows: TradeLogEntry[] = [];
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const row = JSON.parse(line) as TradeLogEntry;
        if (typeof row.ts === "number" && row.ts > since) rows.push(row);
      } catch {
        // Ignore partial/corrupt lines so the UI endpoint stays best-effort.
      }
    }
    return rows.slice(-limit);
  } catch (err) {
    if (!warnedOnFailure) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Trade log read failed (suppressed): ${msg} - path=${TRADE_LOG_PATH}`);
      warnedOnFailure = true;
    }
    return [];
  }
}

