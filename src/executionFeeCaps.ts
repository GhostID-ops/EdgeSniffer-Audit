import { computeUnitLimit, maxBuyExecutionFeeLamports, maxBuyExecutionFeePctOfBuy } from "./config.js";
import { getDynamicPriorityFee } from "./feeOracle.js";
import { getHeliusTipLamportsForRoute, getJitoTipLamportsForRoute } from "./tipBuilder.js";

export type ExecutionFeeParams = {
  priorityFeeMicroLamports: number;
  jitoTipLamports: number;
  heliusTipLamports: number;
  capped: boolean;
  estimatedMaxLamports: number;
  capLamports: number | null;
};

export function getExecutionFeeParamsForRoute(route?: string, buyLamports?: number | bigint | null): ExecutionFeeParams {
  let priorityFeeMicroLamports = getDynamicPriorityFeeForRouteCompat(route);
  let jitoTipLamports = getJitoTipLamportsForRoute(route);
  let heliusTipLamports = getHeliusTipLamportsForRoute(route);
  const capLamports = isBuyRoute(route) ? effectiveBuyExecutionFeeCapLamports(buyLamports) : null;
  const uncappedEstimate = estimateMaxLamports(priorityFeeMicroLamports, jitoTipLamports, heliusTipLamports);

  if (capLamports != null && capLamports >= 0 && uncappedEstimate > capLamports) {
    const scale = capLamports / Math.max(1, uncappedEstimate);
    priorityFeeMicroLamports = Math.floor(priorityFeeMicroLamports * scale);
    jitoTipLamports = Math.floor(jitoTipLamports * scale);
    heliusTipLamports = Math.floor(heliusTipLamports * scale);

    while (
      estimateMaxLamports(priorityFeeMicroLamports, jitoTipLamports, heliusTipLamports) > capLamports &&
      priorityFeeMicroLamports > 0
    ) {
      priorityFeeMicroLamports -= 1;
    }
  }

  return {
    priorityFeeMicroLamports,
    jitoTipLamports,
    heliusTipLamports,
    capped: capLamports != null && uncappedEstimate > capLamports,
    estimatedMaxLamports: estimateMaxLamports(priorityFeeMicroLamports, jitoTipLamports, heliusTipLamports),
    capLamports,
  };
}

export function effectiveBuyExecutionFeeCapLamports(buyLamports?: number | bigint | null): number | null {
  const caps: number[] = [];
  if (maxBuyExecutionFeeLamports != null) caps.push(maxBuyExecutionFeeLamports);
  const amount =
    typeof buyLamports === "bigint"
      ? Number(buyLamports)
      : typeof buyLamports === "number" && Number.isFinite(buyLamports)
        ? buyLamports
        : null;
  if (amount != null && amount > 0 && maxBuyExecutionFeePctOfBuy != null) {
    caps.push(Math.floor((amount * maxBuyExecutionFeePctOfBuy) / 100));
  }
  if (caps.length === 0) return null;
  return Math.max(0, Math.min(...caps));
}

function isBuyRoute(route?: string): boolean {
  return (route ?? "").toLowerCase().includes("buy");
}

function getDynamicPriorityFeeForRouteCompat(route?: string): number {
  const base = getDynamicPriorityFee();
  return Math.max(0, Math.round(base * routeMultiplier(route, process.env.EXECUTION_PRIORITY_FEE_MULTIPLIERS)));
}

function routeMultiplier(route: string | undefined, raw: string | undefined): number {
  if (!raw) return 1;
  const normalizedRoute = (route ?? "").toLowerCase();
  const pairs = raw
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => {
      const [key, value] = entry.split(":");
      return { key: (key ?? "").trim().toLowerCase(), value: Number((value ?? "").trim()) };
    })
    .filter((entry) => entry.key && Number.isFinite(entry.value) && entry.value >= 0);

  const exact = pairs.find((entry) => entry.key === normalizedRoute);
  if (exact) return exact.value;

  if (isBuyRoute(normalizedRoute)) {
    const buy = pairs.find((entry) => entry.key === "buy");
    if (buy) return buy.value;
  }

  if (normalizedRoute.includes("sell")) {
    const sell = pairs.find((entry) => entry.key === "sell");
    if (sell) return sell.value;
  }

  return 1;
}

function estimateMaxLamports(priorityFeeMicroLamports: number, jitoTipLamports: number, heliusTipLamports: number): number {
  const units = computeUnitLimit > 0 ? Math.min(computeUnitLimit, 1_400_000) : 1_400_000;
  const priorityLamports = Math.ceil((Math.max(0, priorityFeeMicroLamports) * units) / 1_000_000);
  return Math.max(0, jitoTipLamports) + Math.max(0, heliusTipLamports) + priorityLamports;
}
