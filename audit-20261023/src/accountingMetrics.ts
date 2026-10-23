export type ReturnSummary = {
  trades: number;
  wins: number;
  losses: number;
  winRatePct: number;
  avgPct: number;
  medianPct: number | null;
  profitFactor: number;
  sharpeProxy: number;
  cumulativeReturnPctPoints: number;
  maxCumulativeDrawdownPctPoints: number;
};

export function avg(values: number[]): number {
  const xs = values.filter(Number.isFinite);
  return xs.reduce((sum, value) => sum + value, 0) / Math.max(1, xs.length);
}

export function median(values: number[]): number | null {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid]! : (xs[mid - 1]! + xs[mid]!) / 2;
}

export function std(values: number[]): number {
  const xs = values.filter(Number.isFinite);
  if (xs.length < 2) return 0;
  const m = avg(xs);
  return Math.sqrt(avg(xs.map((value) => (value - m) ** 2)));
}

/**
 * Drawdown for a sequence of per-trade percentage returns.
 *
 * This is a percentage-point drawdown of a cumulative return curve, not a
 * bankroll drawdown percentage. It can be below -100pp after repeated losses,
 * so reports must label it as "pp" instead of "%".
 */
export function maxCumulativeDrawdownPctPoints(returnsPct: number[]): number {
  let cumulative = 0;
  let peak = 0;
  let drawdown = 0;
  for (const value of returnsPct.filter(Number.isFinite)) {
    cumulative += value;
    peak = Math.max(peak, cumulative);
    drawdown = Math.min(drawdown, cumulative - peak);
  }
  return drawdown;
}

export function summarizeReturns(returnsPct: number[]): ReturnSummary {
  const xs = returnsPct.filter(Number.isFinite);
  const wins = xs.filter((value) => value > 0);
  const losses = xs.filter((value) => value < 0);
  const grossProfit = wins.reduce((sum, value) => sum + value, 0);
  const grossLoss = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  const mean = avg(xs);
  const sigma = std(xs);
  return {
    trades: xs.length,
    wins: wins.length,
    losses: losses.length,
    winRatePct: (wins.length / Math.max(1, xs.length)) * 100,
    avgPct: mean,
    medianPct: median(xs),
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0,
    sharpeProxy: sigma > 0 ? mean / sigma : 0,
    cumulativeReturnPctPoints: xs.reduce((sum, value) => sum + value, 0),
    maxCumulativeDrawdownPctPoints: maxCumulativeDrawdownPctPoints(xs),
  };
}
