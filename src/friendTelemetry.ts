import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { executionTelemetryPath } from "./config.js";

type Row = {
  tsMs?: number;
  ts?: string;
  kind?: string;
  route?: string;
  side?: string;
  detector?: string;
  mint?: string;
  elapsedMs?: number;
  success?: boolean;
  errorClass?: string;
  details?: Record<string, unknown>;
};

export type TelemetrySummary = {
  generatedAt: number;
  available: boolean;
  totals: { events: number; success: number; failure: number; successRate: number };
  latency: {
    decodeMsBest: number | null; decodeMsP50: number | null;
    buildMsBest: number | null; buildMsP50: number | null;
    broadcastMsBest: number | null; broadcastMsP50: number | null;
    quoteMsP50: number | null;
    detectMsBest: number | null; detectMsP50: number | null;
    confirmMsP50: number | null;
    inProcessBestMs: number | null;
  };
  routes: Array<{ name: string; count: number; pct: number }>;
  detectors: Array<{ name: string; count: number }>;
  failureClasses: Array<{ name: string; count: number }>;
  recent: Array<{ tsMs: number | null; kind: string; route: string; side: string; elapsedMs: number | null; success: boolean }>;
};

let cache: { at: number; data: TelemetrySummary } | null = null;
const CACHE_MS = 4000;

function numOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function detailNum(row: Row, field: string): number | null {
  return numOrNull(row.details?.[field]);
}

function quantile(values: number[], q: number): number | null {
  const xs = values.filter((v) => Number.isFinite(v));
  if (xs.length === 0) return null;
  const sorted = [...xs].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * q)));
  return sorted[idx]!;
}

function best(values: number[]): number | null {
  const xs = values.filter((v) => Number.isFinite(v));
  return xs.length ? Math.min(...xs) : null;
}

function countBy(rows: Row[], get: (r: Row) => string | undefined): Array<[string, number]> {
  const m = new Map<string, number>();
  for (const r of rows) {
    const k = get(r);
    if (typeof k === "string" && k.length > 0) m.set(k, (m.get(k) ?? 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function emptySummary(): TelemetrySummary {
  return {
    generatedAt: Date.now(),
    available: false,
    totals: { events: 0, success: 0, failure: 0, successRate: 0 },
    latency: {
      decodeMsBest: null, decodeMsP50: null, buildMsBest: null, buildMsP50: null,
      broadcastMsBest: null, broadcastMsP50: null, quoteMsP50: null,
      detectMsBest: null, detectMsP50: null, confirmMsP50: null, inProcessBestMs: null,
    },
    routes: [], detectors: [], failureClasses: [], recent: [],
  };
}

// ---------------- Top trades (PnL cards) ----------------

const TRADES_PATH = resolve(process.cwd(), "data", "trades.jsonl");

type TradeRow = {
  ts?: number | string;
  isoTs?: string;
  side?: string;
  inputMint?: string;
  outputMint?: string;
  inputSymbol?: string;
  outputSymbol?: string;
  sourceWallet?: string;
  realizedPnlLamports?: number;
  realizedPnlPct?: number;
  exitReason?: string;
  marketCapUsd?: number;
  mode?: string;
};

// Friendly names for known source wallets (the traders we copy).
const WALLET_NAMES: Record<string, string> = {
  omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq: "Omego",
  CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o: "CyA",
  "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9": "4vw54",
  "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk": "4BdK",
  "5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF": "5qn",
};

function walletName(addr: string | undefined): string {
  if (!addr) return "";
  if (WALLET_NAMES[addr]) return WALLET_NAMES[addr];
  return addr.length > 8 ? addr.slice(0, 4) + "…" + addr.slice(-3) : addr;
}

export type TopTrade = {
  symbol: string;
  mint: string;
  pnlSol: number;
  pnlPct: number;
  exitLabel: string;
  peakPct: number | null;
  marketCapUsd: number | null;
  tsMs: number | null;
  mode: string;
  source: string;
};

let tradesCache: { at: number; data: { available: boolean; count: number; trades: TopTrade[] } } | null = null;

function tradeTs(r: TradeRow): number | null {
  if (typeof r.ts === "number" && Number.isFinite(r.ts)) return r.ts;
  const iso = r.isoTs ?? (typeof r.ts === "string" ? r.ts : undefined);
  if (iso) { const p = Date.parse(iso); return Number.isFinite(p) ? p : null; }
  return null;
}

function exitLabel(reason: string | undefined): string {
  if (!reason) return "Closed";
  const r = reason.trim();
  if (/trailing/i.test(r)) return "Trailing stop";
  if (/take profit|hybrid/i.test(r)) return "Take profit";
  if (/ladder/i.test(r)) return "Laddered exit";
  if (/source/i.test(r)) return "Followed source exit";
  if (/stop loss/i.test(r)) return "Stop loss";
  // fallback: text before first parenthesis
  const head = r.split("(")[0]!.trim();
  return head.length > 0 && head.length <= 40 ? head : "Closed";
}

function parsePeak(reason: string | undefined): number | null {
  if (!reason) return null;
  const m = reason.match(/peak\s+([\d.]+)\s*%/i);
  return m ? Number(m[1]) : null;
}

export function getTopTrades(limit = 9): { available: boolean; count: number; trades: TopTrade[] } {
  if (tradesCache && Date.now() - tradesCache.at < CACHE_MS) return tradesCache.data;
  if (!existsSync(TRADES_PATH)) {
    const data = { available: false, count: 0, trades: [] as TopTrade[] };
    tradesCache = { at: Date.now(), data };
    return data;
  }
  let rows: TradeRow[] = [];
  try {
    const raw = readFileSync(TRADES_PATH, "utf8").trim();
    rows = raw ? raw.split("\n").map((l) => { try { return JSON.parse(l) as TradeRow; } catch { return null; } }).filter((r): r is TradeRow => r != null) : [];
  } catch {
    const data = { available: false, count: 0, trades: [] as TopTrade[] };
    tradesCache = { at: Date.now(), data };
    return data;
  }

  // Map each token mint -> the source wallet whose buy we copied (sells log the exit, not the source).
  const buyByMint = new Map<string, string>();
  for (const r of rows) {
    if (r.side === "buy" && r.outputMint && r.sourceWallet && !buyByMint.has(r.outputMint)) {
      buyByMint.set(r.outputMint, r.sourceWallet);
    }
  }

  const winners = rows.filter(
    (r) => r.side === "sell" && typeof r.realizedPnlLamports === "number" && r.realizedPnlLamports > 0
  );

  const trades: TopTrade[] = winners
    .map((r) => ({
      symbol: (r.inputSymbol && r.inputSymbol !== "SOL" ? r.inputSymbol : "") ||
        (r.inputMint ? r.inputMint.slice(0, 4).toUpperCase() : "TOKEN"),
      mint: r.inputMint ?? "",
      pnlSol: (r.realizedPnlLamports ?? 0) / 1e9,
      pnlPct: r.realizedPnlPct ?? 0,
      exitLabel: exitLabel(r.exitReason),
      peakPct: parsePeak(r.exitReason),
      marketCapUsd: typeof r.marketCapUsd === "number" && r.marketCapUsd > 0 ? r.marketCapUsd : null,
      tsMs: tradeTs(r),
      mode: r.mode === "live" ? "live" : "sim",
      source: walletName(r.inputMint ? buyByMint.get(r.inputMint) : undefined),
    }))
    .sort((a, b) => b.pnlPct - a.pnlPct)
    .slice(0, Math.max(1, limit));

  const data = { available: trades.length > 0, count: winners.length, trades };
  tradesCache = { at: Date.now(), data };
  return data;
}

export function getExecutionTelemetrySummary(): TelemetrySummary {
  if (cache && Date.now() - cache.at < CACHE_MS) return cache.data;
  if (!existsSync(executionTelemetryPath)) {
    const data = emptySummary();
    cache = { at: Date.now(), data };
    return data;
  }

  let rows: Row[] = [];
  try {
    const raw = readFileSync(executionTelemetryPath, "utf8").trim();
    rows = raw
      ? raw.split("\n").map((l) => { try { return JSON.parse(l) as Row; } catch { return null; } }).filter((r): r is Row => r != null)
      : [];
  } catch {
    const data = emptySummary();
    cache = { at: Date.now(), data };
    return data;
  }

  const events = rows.length;
  const failure = rows.filter((r) => r.success === false).length;
  const success = events - failure;

  const detectRows = rows.filter((r) => r.kind === "source-detected");
  const decodeVals = detectRows.map((r) => detailNum(r, "receivedToDetectedMs")).filter((n): n is number => n != null);
  const detectVals = detectRows.map((r) => detailNum(r, "providerToProcessMs") ?? numOrNull(r.elapsedMs)).filter((n): n is number => n != null);
  const buildVals = rows.filter((r) => r.kind === "stage:build").map((r) => numOrNull(r.elapsedMs)).filter((n): n is number => n != null);
  const broadcastVals = rows.filter((r) => r.kind === "broadcast").map((r) => numOrNull(r.elapsedMs)).filter((n): n is number => n != null);
  const quoteVals = rows.filter((r) => r.kind === "stage:quote").map((r) => numOrNull(r.elapsedMs)).filter((n): n is number => n != null);
  const confirmVals = rows.filter((r) => r.kind === "stage:confirm").map((r) => numOrNull(r.elapsedMs)).filter((n): n is number => n != null);

  const buildBest = best(buildVals);
  const broadcastBest = best(broadcastVals);
  const decodeBest = best(decodeVals);
  const inProcessBest =
    decodeBest != null || buildBest != null || broadcastBest != null
      ? (decodeBest ?? 0) + (buildBest ?? 0) + (broadcastBest ?? 0)
      : null;

  const routeCounts = countBy(rows, (r) => r.route);
  const routes = routeCounts.slice(0, 6).map(([name, count]) => ({ name, count, pct: events ? count / events : 0 }));
  const detectors = countBy(detectRows, (r) => r.detector).map(([name, count]) => ({ name, count }));
  const failureClasses = countBy(rows.filter((r) => r.success === false), (r) => r.errorClass ?? "unknown").map(([name, count]) => ({ name, count }));

  const recentKinds = new Set(["broadcast", "stage:confirm", "source-detected", "mirror-failure"]);
  const recent = rows
    .filter((r) => typeof r.kind === "string" && recentKinds.has(r.kind))
    .slice(-14)
    .reverse()
    .map((r) => ({
      tsMs: numOrNull(r.tsMs) ?? (r.ts ? Date.parse(r.ts) || null : null),
      kind: r.kind ?? "",
      route: r.route ?? "",
      side: r.side ?? "",
      elapsedMs: numOrNull(r.elapsedMs),
      success: r.success !== false,
    }));

  const data: TelemetrySummary = {
    generatedAt: Date.now(),
    available: events > 0,
    totals: { events, success, failure, successRate: events ? success / events : 0 },
    latency: {
      decodeMsBest: decodeBest, decodeMsP50: quantile(decodeVals, 0.5),
      buildMsBest: buildBest, buildMsP50: quantile(buildVals, 0.5),
      broadcastMsBest: broadcastBest, broadcastMsP50: quantile(broadcastVals, 0.5),
      quoteMsP50: quantile(quoteVals, 0.5),
      detectMsBest: best(detectVals), detectMsP50: quantile(detectVals, 0.5),
      confirmMsP50: quantile(confirmVals, 0.5),
      inProcessBestMs: inProcessBest,
    },
    routes, detectors, failureClasses, recent,
  };
  cache = { at: Date.now(), data };
  return data;
}
