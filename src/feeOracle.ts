import { PublicKey } from "@solana/web3.js";
import {
  readConnection,
  priorityFeeMicroLamports,
  dynamicPriorityFeeEnabled,
  dynamicPriorityFeeRefreshMs,
  dynamicPriorityFeeMaxMicroLamports,
} from "./config.js";
import { PUMP_FUN_PROGRAM_ID } from "./pumpfun/constants.js";

/**
 * Background-refreshed priority-fee oracle.
 *
 * Polls `getRecentPrioritizationFees` for the pump.fun program (the contention surface we actually
 * compete on) and exposes the 75th percentile. The hot path reads `getDynamicPriorityFee()` synchronously
 * — never waits on the RPC. The static `priorityFeeMicroLamports` is a floor, so we never UNDERPAY;
 * dynamic fee only kicks in when network is busier than the floor.
 */

let cachedFee = priorityFeeMicroLamports;
let lastFetchedAt = 0;
let pollerStarted = false;
let pollerHandle: NodeJS.Timeout | null = null;
let lastRefreshWarningAt = 0;
let suppressedRefreshWarnings = 0;
const FEE_ORACLE_WARNING_GAP_MS = 60_000;

function p75(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.floor(sorted.length * 0.75);
  return sorted[Math.min(idx, sorted.length - 1)] ?? 0;
}

async function refreshFee(): Promise<void> {
  try {
    const fees = await readConnection.getRecentPrioritizationFees({
      lockedWritableAccounts: [PUMP_FUN_PROGRAM_ID],
    });
    if (!fees || fees.length === 0) return;
    const samples = fees.map((f) => f.prioritizationFee).filter((v) => v > 0);
    if (samples.length === 0) return;
    const p75Fee = p75(samples);
    const uncapped = Math.max(priorityFeeMicroLamports, p75Fee);
    cachedFee =
      dynamicPriorityFeeMaxMicroLamports != null
        ? Math.min(uncapped, dynamicPriorityFeeMaxMicroLamports)
        : uncapped;
    lastFetchedAt = Date.now();
  } catch (err) {
    // Transient — keep the previous cached fee. Static floor still protects us.
    const msg = err instanceof Error ? err.message : String(err);
    const now = Date.now();
    suppressedRefreshWarnings += 1;
    if (now - lastRefreshWarningAt >= FEE_ORACLE_WARNING_GAP_MS) {
      const suffix =
        suppressedRefreshWarnings > 1 ? ` (${suppressedRefreshWarnings - 1} similar warning(s) suppressed)` : "";
      console.warn(`Fee oracle refresh failed (using cached ${cachedFee} microLamports/CU): ${msg}${suffix}`);
      lastRefreshWarningAt = now;
      suppressedRefreshWarnings = 0;
    }
  }
}

/**
 * Start the background refresh loop. Idempotent — safe to call multiple times. No-op if dynamic
 * fees are disabled in config.
 */
export function startFeeOracle(): void {
  if (pollerStarted || !dynamicPriorityFeeEnabled) return;
  pollerStarted = true;
  // Kick once immediately so the first trade has a fresh number.
  void refreshFee();
  pollerHandle = setInterval(() => {
    void refreshFee();
  }, dynamicPriorityFeeRefreshMs);
}

export function stopFeeOracle(): void {
  if (pollerHandle) clearInterval(pollerHandle);
  pollerHandle = null;
  pollerStarted = false;
}

/** Synchronous accessor for the current priority fee. Always returns at least the static floor. */
export function getDynamicPriorityFee(): number {
  if (!dynamicPriorityFeeEnabled) return priorityFeeMicroLamports;
  return Math.max(cachedFee, priorityFeeMicroLamports);
}

/** Diagnostic snapshot for status displays. */
export function getFeeOracleSnapshot(): {
  enabled: boolean;
  cachedFee: number;
  staticFloor: number;
  lastFetchedAt: number;
  maxFee: number | null;
} {
  return {
    enabled: dynamicPriorityFeeEnabled,
    cachedFee,
    staticFloor: priorityFeeMicroLamports,
    lastFetchedAt,
    maxFee: dynamicPriorityFeeMaxMicroLamports,
  };
}
