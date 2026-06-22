export type HybridExitPlanInput = {
  enabled: boolean;
  tpDone: boolean;
  tpInFlight: boolean;
  tpPct: number;
  tpSellPct: number;
  heldRaw: number;
  pnlPct: number;
};

/** Return the one-time partial-TP token amount, or null when no hybrid action is due. */
export function planHybridPartialSellRaw(input: HybridExitPlanInput): number | null {
  if (!input.enabled || input.tpDone || input.tpInFlight) return null;
  if (input.tpPct <= 0 || input.tpSellPct <= 0) return null;
  if (input.heldRaw <= 1 || input.pnlPct < input.tpPct) return null;

  const sellPct = Math.min(100, input.tpSellPct);
  const targetRaw = Math.floor((input.heldRaw * sellPct) / 100);
  return Math.min(input.heldRaw, Math.max(1, targetRaw));
}

/**
 * Exact "partial TP, remainder on source sell" mode must not leak into ordinary TP/SL/trailing
 * exits. Those exits describe a different strategy and would liquidate the intended runner.
 */
export function waitsForSourceExit(enabled: boolean, sourceExitRemaining: boolean): boolean {
  return enabled && sourceExitRemaining;
}

/** Once the partial TP landed, no more price quotes are needed while waiting for the source sell. */
export function needsHybridPriceCheck(
  enabled: boolean,
  sourceExitRemaining: boolean,
  tpDone: boolean
): boolean {
  return !(waitsForSourceExit(enabled, sourceExitRemaining) && tpDone);
}

/** Any positive tracked-source sell is the source-exit signal for the remaining bot inventory. */
export function shouldCloseHybridRemainderOnSourceSell(input: {
  enabled: boolean;
  sourceExitRemaining: boolean;
  sourceSellRaw: number;
  botHeldRaw: number;
}): boolean {
  return (
    waitsForSourceExit(input.enabled, input.sourceExitRemaining) &&
    input.sourceSellRaw > 0 &&
    input.botHeldRaw > 0
  );
}
