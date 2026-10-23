import {
  localPumpfunMarketCapFilterEnabled,
  localPumpfunSolPriceAutoEnabled,
  localPumpfunSolPriceRefreshMs,
  localPumpfunSolPriceTimeoutMs,
  localPumpfunSolPriceUrl,
  localPumpfunSolPriceUsd,
  prebuyInferredMcFilterEnabled,
  prebuyMaxMarketCapUsd,
  prebuyMinMarketCapUsd,
  updateLocalPumpfunSolPriceUsd,
} from "./config.js";

const SOL_MINT = "So11111111111111111111111111111111111111112";
let started = false;
let lastLoggedPrice: number | null = null;

function asPositiveNumber(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : typeof value === "string" ? parseFloat(value) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function extractSolUsdPrice(value: unknown, depth = 0): number | null {
  if (depth > 6) return null;
  const direct = asPositiveNumber(value);
  if (direct != null) return direct;

  if (Array.isArray(value)) {
    for (const item of value) {
      const found = extractSolUsdPrice(item, depth + 1);
      if (found != null) return found;
    }
    return null;
  }

  if (!isRecord(value)) return null;

  for (const key of ["usd", "usdPrice", "priceUsd", "price", "value"]) {
    const found = asPositiveNumber(value[key]);
    if (found != null) return found;
  }

  for (const key of ["solana", "SOL", "SoL", SOL_MINT, "data", "result"]) {
    if (key in value) {
      const found = extractSolUsdPrice(value[key], depth + 1);
      if (found != null) return found;
    }
  }

  for (const nested of Object.values(value)) {
    if (!isRecord(nested) && !Array.isArray(nested)) continue;
    const found = extractSolUsdPrice(nested, depth + 1);
    if (found != null) return found;
  }

  return null;
}

async function fetchSolUsdPrice(): Promise<number> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), localPumpfunSolPriceTimeoutMs);
  try {
    const response = await fetch(localPumpfunSolPriceUrl, {
      signal: controller.signal,
      headers: { accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const body: unknown = await response.json();
    const price = extractSolUsdPrice(body);
    if (price == null) {
      throw new Error("response did not contain a SOL/USD price");
    }
    return price;
  } finally {
    clearTimeout(timeout);
  }
}

async function refreshSolPrice(): Promise<void> {
  try {
    const price = await fetchSolUsdPrice();
    if (!updateLocalPumpfunSolPriceUsd(price)) return;
    const shouldLog = lastLoggedPrice == null || Math.abs(price - lastLoggedPrice) >= 0.25;
    if (shouldLog) {
      console.log(`SOL price refreshed: $${price.toFixed(2)} for local Pump.fun MC filter`);
      lastLoggedPrice = price;
    }
  } catch (err) {
    const fallback =
      localPumpfunSolPriceUsd != null
        ? `keeping fallback/cached $${localPumpfunSolPriceUsd.toFixed(2)}`
        : "no fallback price available";
    console.warn(
      `SOL price refresh failed (${err instanceof Error ? err.message : String(err)}); ${fallback}.`
    );
  }
}

export function startSolPriceUpdater(): void {
  if (started || !localPumpfunSolPriceAutoEnabled) return;
  const needsUsdPrice = prebuyMinMarketCapUsd != null || prebuyMaxMarketCapUsd != null;
  if ((!localPumpfunMarketCapFilterEnabled && !prebuyInferredMcFilterEnabled) || !needsUsdPrice) return;
  started = true;

  const fallback =
    localPumpfunSolPriceUsd != null ? `$${localPumpfunSolPriceUsd.toFixed(2)} fallback` : "no fallback";
  console.log(
    `SOL price auto-refresh enabled for local/inferred MC filter (${fallback}, every ${Math.round(
      localPumpfunSolPriceRefreshMs / 1000
    )}s).`
  );

  void refreshSolPrice();
  const interval = setInterval(() => void refreshSolPrice(), localPumpfunSolPriceRefreshMs);
  interval.unref?.();
}
