import {
  prebuyFilterFailOpen,
  prebuyMinLiquidityUsd,
  prebuyMinMomentumPct,
  prebuyMinVolumeUsd,
  prebuyMoralisTimeoutMs,
  prebuyMoralisWindowMs,
} from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const MORALIS_API_KEY = process.env.MORALIS_API_KEY ?? "";
const MORALIS_BASE = "https://solana-gateway.moralis.io";

type Candle = {
  ts: number;
  open: number;
  close: number;
  volume: number;
};

export type PrebuyFilterResult = {
  allowed: boolean;
  reason: string;
  volumeUsd?: number;
  momentumPct?: number | null;
  liquidityUsd?: number | null;
};

function short(mint: string): string {
  return `${mint.slice(0, 8)}...`;
}

function pct(from: number, to: number): number | null {
  if (!Number.isFinite(from) || !Number.isFinite(to) || from <= 0) return null;
  return ((to - from) / from) * 100;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`prebuy filter timeout after ${ms}ms`)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      }
    );
  });
}

async function fetchMoralisCandles(pairAddress: string, fromTs: number, toTs: number): Promise<Candle[]> {
  if (!MORALIS_API_KEY) throw new Error("MORALIS_API_KEY is not set");
  const params = new URLSearchParams({
    fromDate: String(fromTs),
    toDate: String(toTs),
    timeframe: "1min",
    currency: "usd",
    limit: "10",
  });
  const response = await fetch(`${MORALIS_BASE}/token/mainnet/pairs/${pairAddress}/ohlcv?${params}`, {
    headers: { "X-Api-Key": MORALIS_API_KEY },
    signal: AbortSignal.timeout(Math.max(250, prebuyMoralisTimeoutMs - 100)),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Moralis ${response.status}: ${text.slice(0, 120)}`);
  }
  const json = (await response.json()) as {
    result?: Array<{ timestamp?: string; open?: number; close?: number; volume?: number }>;
  };
  return (json.result ?? [])
    .map((item) => ({
      ts: item.timestamp ? Math.floor(new Date(item.timestamp).getTime() / 1000) : NaN,
      open: item.open ?? NaN,
      close: item.close ?? NaN,
      volume: item.volume ?? 0,
    }))
    .filter((c) => Number.isFinite(c.ts) && Number.isFinite(c.open) && Number.isFinite(c.close))
    .sort((a, b) => a.ts - b.ts);
}

async function checkPrebuyMoralisFilterInner(mint: string): Promise<PrebuyFilterResult> {
  const pair = await getTokenPairInfo(mint);
  if (!pair?.pairAddress) {
    return {
      allowed: prebuyFilterFailOpen,
      reason: `prebuy Moralis filter: no pair for ${short(mint)} (${prebuyFilterFailOpen ? "fail-open" : "fail-closed"})`,
    };
  }

  const nowTs = Math.floor(Date.now() / 1000);
  const fromTs = nowTs - Math.ceil(prebuyMoralisWindowMs / 1000);
  const candles = await fetchMoralisCandles(pair.pairAddress, fromTs, nowTs);
  const volumeUsd = candles.reduce((sum, candle) => sum + candle.volume, 0);
  const momentumPct = candles.length > 0 ? pct(candles[0]!.open, candles[candles.length - 1]!.close) : null;
  const liquidityUsd = pair.liquidityUsd;

  if (prebuyMinLiquidityUsd != null && (liquidityUsd ?? 0) < prebuyMinLiquidityUsd) {
    return {
      allowed: false,
      reason: `prebuy Moralis filter: liquidity $${Math.round(liquidityUsd ?? 0)} < $${prebuyMinLiquidityUsd}`,
      volumeUsd,
      momentumPct,
      liquidityUsd,
    };
  }
  if (volumeUsd < prebuyMinVolumeUsd) {
    return {
      allowed: false,
      reason: `prebuy Moralis filter: ${Math.round(prebuyMoralisWindowMs / 60_000)}m volume $${Math.round(volumeUsd)} < $${prebuyMinVolumeUsd}`,
      volumeUsd,
      momentumPct,
      liquidityUsd,
    };
  }
  if (prebuyMinMomentumPct != null && (momentumPct ?? -Infinity) < prebuyMinMomentumPct) {
    return {
      allowed: false,
      reason: `prebuy Moralis filter: momentum ${(momentumPct ?? 0).toFixed(1)}% < ${prebuyMinMomentumPct}%`,
      volumeUsd,
      momentumPct,
      liquidityUsd,
    };
  }

  return {
    allowed: true,
    reason:
      `prebuy Moralis filter passed: vol${Math.round(prebuyMoralisWindowMs / 60_000)}m=$${Math.round(volumeUsd)}` +
      `${momentumPct == null ? "" : ` momentum=${momentumPct.toFixed(1)}%`}` +
      `${liquidityUsd == null ? "" : ` liq=$${Math.round(liquidityUsd)}`}`,
    volumeUsd,
    momentumPct,
    liquidityUsd,
  };
}

export async function checkPrebuyMoralisFilter(mint: string): Promise<PrebuyFilterResult> {
  try {
    return await withTimeout(checkPrebuyMoralisFilterInner(mint), prebuyMoralisTimeoutMs);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return {
      allowed: prebuyFilterFailOpen,
      reason: `prebuy Moralis filter error for ${short(mint)}: ${msg} (${prebuyFilterFailOpen ? "fail-open" : "fail-closed"})`,
    };
  }
}
