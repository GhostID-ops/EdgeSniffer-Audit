import { getPositions } from "./pnl.js";
import { estimatePumpSwapSellQuote } from "./pumpswap/route.js";
import { telegram } from "./telegram.js";

// Local 1-second price feed for the DEX UI. For each OPEN position we sample the
// realizable PumpSwap sell value (what the bag is actually worth right now, read
// from fresh pool reserves — not a laggy third-party API) and keep a ring buffer
// per mint. Only open positions are sampled; closed mints are pruned. This is a
// read-only valuation path and never touches execution.

const SAMPLE_MS = Math.max(500, parseInt(process.env.UI_PRICE_SAMPLE_MS ?? "1000", 10));
const MAX_POINTS = Math.max(60, parseInt(process.env.UI_PRICE_SERIES_POINTS ?? "900", 10)); // ~15 min @ 1s
const STALE_PRUNE_MS = 5 * 60_000;

export type PricePoint = {
  /** epoch ms */
  t: number;
  /** realizable SOL for the full held bag, in lamports */
  valueLamports: number;
  /** SOL per whole token at this sample */
  pricePerToken: number;
};

const series = new Map<string, PricePoint[]>();
const lastSeen = new Map<string, number>();
let started = false;
let sampling = false;

// Telegram price alerts: fire once when a position's unrealized PnL crosses each
// configured threshold (%), re-arming after it falls back below so a re-pump can
// alert again. Set PRICE_ALERT_PCTS="" to disable. Negative values are downside
// alerts. notifyInfo no-ops when Telegram isn't configured.
const ALERT_PCTS = (process.env.PRICE_ALERT_PCTS ?? "50,100,200,500,-50")
  .split(",")
  .map((s) => parseFloat(s.trim()))
  .filter((n) => Number.isFinite(n));
const firedAlerts = new Map<string, Set<number>>();

function maybeAlert(mint: string, symbol: string, costBasisRemainingLamports: number, valueLamports: number): void {
  if (ALERT_PCTS.length === 0 || !(costBasisRemainingLamports > 0)) return;
  const pct = ((valueLamports - costBasisRemainingLamports) / costBasisRemainingLamports) * 100;
  const fired = firedAlerts.get(mint) ?? new Set<number>();
  for (const thr of ALERT_PCTS) {
    const beyond = thr >= 0 ? pct >= thr : pct <= thr;
    if (beyond && !fired.has(thr)) {
      fired.add(thr);
      const dir = thr >= 0 ? "🟢" : "🔴";
      void telegram.notifyInfo(
        `${dir} $${symbol} ${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%`,
        `Bag worth ${(valueLamports / 1e9).toFixed(4)} SOL\n<code>${mint}</code>`
      );
    } else if (!beyond && fired.has(thr)) {
      fired.delete(thr); // re-arm so a later re-cross alerts again
    }
  }
  firedAlerts.set(mint, fired);
}

export function getPriceSeries(mint: string): PricePoint[] {
  return series.get(mint) ?? [];
}

export function getPriceSampleMs(): number {
  return SAMPLE_MS;
}

async function sampleOnce(): Promise<void> {
  if (sampling) return; // never let slow RPC stack up
  sampling = true;
  try {
    const open = getPositions().filter((p) => p.heldRaw > 0);
    const now = Date.now();

    // Drop series for mints we haven't held for a while.
    for (const mint of [...series.keys()]) {
      if (now - (lastSeen.get(mint) ?? 0) > STALE_PRUNE_MS) {
        series.delete(mint);
        lastSeen.delete(mint);
        firedAlerts.delete(mint);
      }
    }

    await Promise.all(
      open.map(async (p) => {
        lastSeen.set(p.mint, now);
        const out = await estimatePumpSwapSellQuote(p.mint, p.heldRaw).catch(() => null);
        if (out == null || !(out > 0)) return; // e.g. pre-graduation token with no PumpSwap pool
        const wholeTokens = p.heldRaw / Math.pow(10, p.decimals || 0);
        const pricePerToken = wholeTokens > 0 ? out / 1e9 / wholeTokens : 0;
        const buf = series.get(p.mint) ?? [];
        buf.push({ t: now, valueLamports: out, pricePerToken });
        if (buf.length > MAX_POINTS) buf.splice(0, buf.length - MAX_POINTS);
        series.set(p.mint, buf);
        maybeAlert(p.mint, p.symbol, p.costBasisRemainingLamports, out);
      })
    );
  } finally {
    sampling = false;
  }
}

export function startPriceSampler(): void {
  if (started) return;
  started = true;
  setInterval(() => {
    void sampleOnce();
  }, SAMPLE_MS);
  console.log(
    `Price sampler: every ${SAMPLE_MS}ms, ${MAX_POINTS} pts/token (local PumpSwap pool quotes for the DEX UI).`
  );
}
