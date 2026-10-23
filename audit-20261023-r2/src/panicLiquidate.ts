/**
 * Emergency full-liquidation orchestrator.
 *
 * Why this exists: stopping the bot and running "sell all positions" only ever touched *tracked*
 * positions. Real wallet balances that were no longer tracked positions (a token bought twice where
 * one leg was never recorded, a position closed locally as a "ghost", dust left after a partial fill)
 * stayed in the wallet forever because nothing rescanned the actual on-chain SPL/Token-2022 balances.
 *
 * This module is a pure, dependency-injected orchestrator so it can be unit-tested without booting the
 * bot, RPC, or a wallet. `botService.panicLiquidateAll()` wires the real implementations into it.
 *
 * It never calls Jupiter. Selling is delegated to injected callbacks that use the existing local
 * Pump.fun/PumpSwap/Raydium direct routes (Rust first, TypeScript local fallback when the Rust sell
 * executor is unavailable). When every local route is unavailable AND Jupiter is disabled, the sell
 * callback throws; we classify that as `noLocalRoute` and surface it in the final report instead of
 * silently leaving a hidden balance behind.
 */

export type PanicPositionRef = {
  mint: string;
  symbol?: string;
  heldRaw: number;
};

export type PanicWalletBalance = {
  mint: string;
  amountRaw: number;
};

/** What happened to one tracked position during phase 1. */
export type TrackedOutcome = {
  mint: string;
  symbol?: string;
  heldRaw: number;
  ok: boolean;
  reason?: string;
};

/** A single wallet-balance sell attempt during a sweep pass. */
export type WalletOutcome = {
  mint: string;
  amountRaw: number;
  pass: number;
  ok: boolean;
  skipped?: boolean;
  reason?: string;
  noLocalRoute?: boolean;
};

/** A mint still holding a nonzero balance after every pass — the authoritative "leftover" list. */
export type RemainingMint = {
  mint: string;
  amountRaw: number;
  lastReason?: string;
  noLocalRoute: boolean;
};

export type PanicLiquidateReport = {
  simulation: boolean;
  jupiterFallbackEnabled: boolean;
  passes: number;
  trackedSold: TrackedOutcome[];
  trackedFailed: TrackedOutcome[];
  walletSold: WalletOutcome[];
  walletFailed: WalletOutcome[];
  /** Authoritative: mints still showing a nonzero balance after all passes. Empty = wallet is clean. */
  remaining: RemainingMint[];
};

export type PanicLiquidateDeps = {
  /** Stop new entries immediately. Called first, before anything is sold. */
  pauseBuys: () => void;
  /** Request the watcher/loop stop. Called right after pausing buys. */
  stopBot: () => void;
  isSimulationMode: () => boolean;
  /** Whether the Jupiter fallback is enabled in config — reported, never used to route here. */
  jupiterFallbackEnabled: boolean;
  /** Snapshot of currently-tracked open positions (heldRaw > 0). */
  getTrackedPositions: () => PanicPositionRef[];
  /** Sell one tracked position fully. Throws on failure. */
  sellTrackedPosition: (position: PanicPositionRef) => Promise<void>;
  /** Read live on-chain SPL/Token-2022 balances of our wallet. Live mode only. */
  listWalletBalances: () => Promise<PanicWalletBalance[]>;
  /** Sell a raw wallet balance via local direct routes. Throws on failure (incl. no-local-route). */
  sellWalletBalance: (mint: string, amountRaw: number) => Promise<void>;
  /** wSOL/USDC and other mints that must never be swept. */
  isSkippableMint: (mint: string) => boolean;
  /** A fast buy is still awaiting confirmation for this mint — do not sell it yet. */
  isFastBuyPending: (mint: string) => boolean;
  log?: (msg: string) => void;
  /** Delay between sweep passes so pending sell confirmations can settle. Default no-op. */
  sleep?: (ms: number) => Promise<void>;
};

export type PanicLiquidateOptions = {
  /** Max wallet-balance sweep passes (bounded retry). Default 3. */
  maxWalletSweepPasses?: number;
  /** Delay between sweep passes in ms. Default 1500. */
  retryDelayMs?: number;
};

/**
 * True when a sell failure means "no local Pump.fun/PumpSwap/Raydium route exists and Jupiter is
 * disabled" — i.e. the token cannot be sold locally, not a transient error. This is exactly the
 * message `mirrorSwap` throws when every direct route is unavailable and JUPITER_FALLBACK_ENABLED
 * is false.
 */
export function isNoLocalRouteError(reason: string | undefined): boolean {
  if (!reason) return false;
  return /local direct execution unavailable|JUPITER_FALLBACK_ENABLED\s*=\s*false|no (?:pump\.?fun|pumpswap|local) route|pool (?:not found|unavailable)|NO_ROUTES_FOUND/i.test(
    reason
  );
}

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * Run the full emergency liquidation:
 *   1. pause buys, stop the bot (prevents new entries while we drain)
 *   2. sell tracked positions first
 *   3. rescan real wallet balances and sell untracked balances/dust
 *   4. retry remaining balances up to a bounded number of passes
 *   5. return a report of sold / failed / remaining mints
 *
 * Never routes through Jupiter. The wallet sweep is the safety net that catches balances the tracked
 * sell-all can't see.
 */
export async function runPanicLiquidate(
  deps: PanicLiquidateDeps,
  options: PanicLiquidateOptions = {}
): Promise<PanicLiquidateReport> {
  const log = deps.log ?? (() => {});
  const sleep = deps.sleep ?? (async () => {});
  const maxPasses = Math.max(1, Math.floor(options.maxWalletSweepPasses ?? 3));
  const retryDelayMs = Math.max(0, options.retryDelayMs ?? 1500);
  const simulation = deps.isSimulationMode();

  const report: PanicLiquidateReport = {
    simulation,
    jupiterFallbackEnabled: deps.jupiterFallbackEnabled,
    passes: 0,
    trackedSold: [],
    trackedFailed: [],
    walletSold: [],
    walletFailed: [],
    remaining: [],
  };

  // Step 1: stop the bleeding BEFORE selling anything, so no new entry can race the drain.
  deps.pauseBuys();
  deps.stopBot();
  log("PANIC LIQUIDATE: buys paused, bot stop requested. Draining positions and wallet balances...");

  // Step 2: sell tracked positions first.
  const tracked = deps.getTrackedPositions().filter((p) => p.heldRaw > 0);
  if (tracked.length > 0) {
    log(`PANIC LIQUIDATE: selling ${tracked.length} tracked position(s)...`);
  }
  for (const position of tracked) {
    try {
      await deps.sellTrackedPosition(position);
      report.trackedSold.push({
        mint: position.mint,
        symbol: position.symbol,
        heldRaw: position.heldRaw,
        ok: true,
      });
    } catch (err) {
      const reason = errMsg(err);
      report.trackedFailed.push({
        mint: position.mint,
        symbol: position.symbol,
        heldRaw: position.heldRaw,
        ok: false,
        reason,
      });
      log(`PANIC LIQUIDATE: tracked sell failed for ${position.mint.slice(0, 8)}...: ${reason}`);
    }
  }

  // Wallet-balance sweeping is live-only: simulation has no real SPL balances to drain.
  if (simulation) {
    log("PANIC LIQUIDATE: simulation mode — skipping live wallet-balance sweep.");
    return report;
  }

  // Step 3 + 4: rescan real wallet balances and sweep untracked balances/dust, with bounded retries.
  const lastReasonByMint = new Map<string, string>();
  const lastNoRouteByMint = new Map<string, boolean>();

  const sweepable = (b: PanicWalletBalance): boolean =>
    b.amountRaw > 0 && !deps.isSkippableMint(b.mint);

  for (let pass = 1; pass <= maxPasses; pass++) {
    let balances: PanicWalletBalance[];
    try {
      balances = (await deps.listWalletBalances()).filter(sweepable);
    } catch (err) {
      const reason = errMsg(err);
      log(`PANIC LIQUIDATE: wallet balance rescan failed on pass ${pass}: ${reason}`);
      // Can't enumerate the wallet — retry on the next pass rather than declaring it clean.
      if (pass < maxPasses) await sleep(retryDelayMs);
      continue;
    }

    if (balances.length === 0) {
      log(`PANIC LIQUIDATE: no untracked wallet balances remaining after pass ${pass}.`);
      break;
    }

    report.passes = pass;
    log(`PANIC LIQUIDATE: pass ${pass}/${maxPasses} — sweeping ${balances.length} wallet balance(s)...`);

    for (const balance of balances) {
      if (deps.isFastBuyPending(balance.mint)) {
        lastReasonByMint.set(balance.mint, "fast buy confirmation still pending");
        lastNoRouteByMint.set(balance.mint, false);
        report.walletSold.push({
          mint: balance.mint,
          amountRaw: balance.amountRaw,
          pass,
          ok: false,
          skipped: true,
          reason: "fast buy confirmation still pending",
        });
        continue;
      }
      try {
        await deps.sellWalletBalance(balance.mint, balance.amountRaw);
        report.walletSold.push({
          mint: balance.mint,
          amountRaw: balance.amountRaw,
          pass,
          ok: true,
        });
        lastReasonByMint.delete(balance.mint);
        lastNoRouteByMint.delete(balance.mint);
      } catch (err) {
        const reason = errMsg(err);
        const noLocalRoute = isNoLocalRouteError(reason);
        lastReasonByMint.set(balance.mint, reason);
        lastNoRouteByMint.set(balance.mint, noLocalRoute);
        report.walletFailed.push({
          mint: balance.mint,
          amountRaw: balance.amountRaw,
          pass,
          ok: false,
          reason,
          noLocalRoute,
        });
        log(
          `PANIC LIQUIDATE: wallet sell failed for ${balance.mint.slice(0, 8)}...: ${reason}` +
            (noLocalRoute ? " (no local route; Jupiter disabled)" : "")
        );
      }
    }

    if (pass < maxPasses) await sleep(retryDelayMs);
  }

  // Step 5: authoritative final scan. Anything nonzero here is a genuine leftover the user must see.
  try {
    const finalBalances = (await deps.listWalletBalances()).filter(sweepable);
    report.remaining = finalBalances.map((b) => ({
      mint: b.mint,
      amountRaw: b.amountRaw,
      lastReason: lastReasonByMint.get(b.mint),
      noLocalRoute: lastNoRouteByMint.get(b.mint) ?? false,
    }));
  } catch (err) {
    log(`PANIC LIQUIDATE: final wallet rescan failed: ${errMsg(err)}`);
    // Fall back to whatever we last recorded as failing so we never claim "clean" on a failed read.
    report.remaining = [...lastReasonByMint.entries()].map(([mint, reason]) => ({
      mint,
      amountRaw: 0,
      lastReason: reason,
      noLocalRoute: lastNoRouteByMint.get(mint) ?? false,
    }));
  }

  return report;
}

/** Human-readable one-block summary of a liquidation report, for logs / Telegram / API responses. */
export function formatPanicLiquidateReport(report: PanicLiquidateReport): string {
  const lines: string[] = [];
  lines.push(
    report.simulation
      ? "PANIC LIQUIDATE (SIMULATION) — tracked positions only"
      : "PANIC LIQUIDATE (LIVE) complete"
  );
  lines.push(
    `Tracked positions sold: ${report.trackedSold.length}` +
      (report.trackedFailed.length ? `, failed: ${report.trackedFailed.length}` : "")
  );
  if (!report.simulation) {
    const walletOk = report.walletSold.filter((w) => w.ok).length;
    lines.push(`Wallet balances sold: ${walletOk} (in ${report.passes} pass(es))`);
    if (report.remaining.length === 0) {
      lines.push("Final leftover tokens: none — wallet is clean.");
    } else {
      lines.push(`Final leftover tokens: ${report.remaining.length}`);
      for (const r of report.remaining) {
        const why = r.noLocalRoute
          ? "no local Pump.fun/PumpSwap route (Jupiter disabled)"
          : r.lastReason ?? "still holding after sweep";
        lines.push(`  • ${r.mint} — ${why}`);
      }
    }
  }
  return lines.join("\n");
}
