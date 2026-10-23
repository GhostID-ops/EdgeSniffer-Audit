import { LAMPORTS_PER_SOL } from "@solana/web3.js";
import {
  dailyLossLimitLamports,
  consecutiveLossLimit,
  circuitBreakerPauseMs,
} from "./config.js";

/**
 * Two-layer circuit breaker:
 *   1. Session loss limit: when cumulative realized PnL since session start drops below
 *      -DAILY_LOSS_LIMIT_SOL, block new buys for the rest of the session.
 *   2. Consecutive-loss streak: when N losing trades fire in a row within a short window,
 *      pause new buys for CIRCUIT_BREAKER_PAUSE_MS.
 *
 * Both are advisory — they only block buys, never sells. Existing positions can always exit.
 */

let sessionRealizedPnlLamports = 0;
let sessionStartedAt = Date.now();
let sessionLossKilled = false;

const recentResults: ("win" | "loss")[] = [];
let pausedUntilTs = 0;
let pausedReason: string | null = null;

/** Reset all breaker state. Call on bot start / resetTradingState. */
export function resetCircuitBreaker(): void {
  sessionRealizedPnlLamports = 0;
  sessionStartedAt = Date.now();
  sessionLossKilled = false;
  recentResults.length = 0;
  pausedUntilTs = 0;
  pausedReason = null;
}

/**
 * Record a closed trade's outcome. Updates both session PnL and the loss-streak buffer.
 * `realizedPnlLamports` is the trade's PnL (positive for win, negative for loss).
 */
export function recordTradeOutcome(realizedPnlLamports: number): void {
  sessionRealizedPnlLamports += realizedPnlLamports;

  const result: "win" | "loss" = realizedPnlLamports >= 0 ? "win" : "loss";
  recentResults.push(result);
  if (recentResults.length > 32) recentResults.shift();

  // Check session loss limit.
  if (
    dailyLossLimitLamports != null &&
    !sessionLossKilled &&
    sessionRealizedPnlLamports <= -dailyLossLimitLamports
  ) {
    sessionLossKilled = true;
    console.log(
      `⛔ Session loss limit hit: ${(sessionRealizedPnlLamports / LAMPORTS_PER_SOL).toFixed(4)} SOL <= -${(dailyLossLimitLamports / LAMPORTS_PER_SOL).toFixed(4)} SOL. New buys blocked for the rest of the session.`
    );
  }

  // Check consecutive-loss streak.
  if (consecutiveLossLimit > 0 && result === "loss") {
    const tail = recentResults.slice(-consecutiveLossLimit);
    if (tail.length === consecutiveLossLimit && tail.every((r) => r === "loss")) {
      pausedUntilTs = Date.now() + circuitBreakerPauseMs;
      pausedReason = `${consecutiveLossLimit} losses in a row`;
      console.log(
        `⏸️  Circuit breaker tripped: ${pausedReason}. New buys paused for ${(circuitBreakerPauseMs / 1000).toFixed(0)}s.`
      );
    }
  }
}

/**
 * Check whether new buys should be blocked. Returns a string reason if blocked, null otherwise.
 * Called at the top of processSwap for buy-side trades.
 */
export function shouldBlockNewBuys(): string | null {
  if (sessionLossKilled) {
    return `session loss limit hit (cumulative ${(sessionRealizedPnlLamports / LAMPORTS_PER_SOL).toFixed(4)} SOL)`;
  }
  if (pausedUntilTs > 0) {
    const now = Date.now();
    if (now < pausedUntilTs) {
      const remainingSec = Math.ceil((pausedUntilTs - now) / 1000);
      return `circuit breaker paused (${pausedReason}, ${remainingSec}s remaining)`;
    }
    // Expired — clear and resume.
    pausedUntilTs = 0;
    pausedReason = null;
    console.log(`▶️  Circuit breaker pause expired. Resuming new buys.`);
  }
  return null;
}

export function getCircuitBreakerSnapshot(): {
  sessionStartedAt: number;
  sessionRealizedPnlLamports: number;
  sessionLossKilled: boolean;
  recentResults: readonly ("win" | "loss")[];
  pausedUntilTs: number;
  pausedReason: string | null;
} {
  return {
    sessionStartedAt,
    sessionRealizedPnlLamports,
    sessionLossKilled,
    recentResults: [...recentResults],
    pausedUntilTs,
    pausedReason,
  };
}
