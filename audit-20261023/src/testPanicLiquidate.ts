import assert from "node:assert/strict";
import {
  runPanicLiquidate,
  isNoLocalRouteError,
  type PanicLiquidateDeps,
  type PanicWalletBalance,
} from "./panicLiquidate.js";

// The exact error mirror.ts throws when every local direct route is unavailable and Jupiter is off.
const NO_LOCAL_ROUTE_ERR =
  "sell local direct execution unavailable and JUPITER_FALLBACK_ENABLED=false; not using Jupiter fallback.";

/** Build a mock wallet whose balances clear as sellWalletBalance succeeds. */
function makeWallet(initial: PanicWalletBalance[]) {
  const byMint = new Map(initial.map((b) => [b.mint, b.amountRaw]));
  return {
    list: async (): Promise<PanicWalletBalance[]> =>
      [...byMint.entries()].map(([mint, amountRaw]) => ({ mint, amountRaw })),
    clear: (mint: string) => byMint.delete(mint),
    set: (mint: string, amountRaw: number) => byMint.set(mint, amountRaw),
  };
}

// ---------------------------------------------------------------------------
// Scenario A: tracked positions sold, then untracked wallet balances swept, clean finish.
// Also asserts ordering (buys paused + stop before any sell; tracked before wallet) — requirement 5 & 2.
// ---------------------------------------------------------------------------
{
  const events: string[] = [];
  let buysPaused = false;
  const wallet = makeWallet([
    { mint: "UUUU1", amountRaw: 1000 },
    { mint: "UUUU2", amountRaw: 5 },
  ]);

  const deps: PanicLiquidateDeps = {
    pauseBuys: () => {
      buysPaused = true;
      events.push("pauseBuys");
    },
    stopBot: () => events.push("stopBot"),
    isSimulationMode: () => false,
    jupiterFallbackEnabled: false,
    getTrackedPositions: () => [
      { mint: "TTTT1", symbol: "T1", heldRaw: 100 },
      { mint: "TTTT2", symbol: "T2", heldRaw: 200 },
    ],
    sellTrackedPosition: async (p) => {
      assert.equal(buysPaused, true, "buys must be paused before any tracked sell");
      events.push(`sellTracked:${p.mint}`);
    },
    listWalletBalances: wallet.list,
    sellWalletBalance: async (mint) => {
      assert.equal(buysPaused, true, "buys must be paused before any wallet sell");
      events.push(`sellWallet:${mint}`);
      wallet.clear(mint);
    },
    isSkippableMint: () => false,
    isFastBuyPending: () => false,
  };

  const report = await runPanicLiquidate(deps, { maxWalletSweepPasses: 3, retryDelayMs: 0 });

  assert.deepEqual(
    report.trackedSold.map((t) => t.mint),
    ["TTTT1", "TTTT2"],
    "both tracked positions sold"
  );
  assert.equal(report.trackedFailed.length, 0);
  assert.deepEqual(
    report.walletSold.filter((w) => w.ok).map((w) => w.mint).sort(),
    ["UUUU1", "UUUU2"],
    "both untracked wallet balances sold"
  );
  assert.deepEqual(report.remaining, [], "wallet is clean at the end");

  // Ordering: pause + stop happen first, tracked sells before wallet sells.
  assert.equal(events[0], "pauseBuys", "buys paused first");
  assert.equal(events[1], "stopBot", "bot stopped second");
  const firstWallet = events.findIndex((e) => e.startsWith("sellWallet:"));
  const lastTracked = events.map((e) => e.startsWith("sellTracked:")).lastIndexOf(true);
  assert.ok(firstWallet > lastTracked, "all tracked sells happen before any wallet sweep sell");
  console.log("Scenario A passed: tracked-then-wallet liquidation, ordering, buys paused.");
}

// ---------------------------------------------------------------------------
// Scenario B: a token with no local route fails every pass and lands in the final remaining report,
// flagged noLocalRoute. Jupiter is never consulted (no jupiter dep exists). Retries are bounded.
// Requirements 3 & 4 (+ bounded retry).
// ---------------------------------------------------------------------------
{
  const wallet = makeWallet([
    { mint: "GOODU", amountRaw: 42 },
    { mint: "DEADU", amountRaw: 7 }, // no local route — cannot sell locally, Jupiter disabled
  ]);
  let deadSellAttempts = 0;

  const deps: PanicLiquidateDeps = {
    pauseBuys: () => {},
    stopBot: () => {},
    isSimulationMode: () => false,
    jupiterFallbackEnabled: false,
    getTrackedPositions: () => [],
    sellTrackedPosition: async () => {
      throw new Error("no tracked positions in this scenario");
    },
    listWalletBalances: wallet.list,
    sellWalletBalance: async (mint) => {
      if (mint === "DEADU") {
        deadSellAttempts++;
        throw new Error(NO_LOCAL_ROUTE_ERR);
      }
      wallet.clear(mint);
    },
    isSkippableMint: () => false,
    isFastBuyPending: () => false,
  };

  const report = await runPanicLiquidate(deps, { maxWalletSweepPasses: 3, retryDelayMs: 0 });

  assert.ok(
    report.walletSold.some((w) => w.mint === "GOODU" && w.ok),
    "the sellable token was sold"
  );
  assert.equal(report.remaining.length, 1, "exactly one leftover token");
  const left = report.remaining[0];
  assert.equal(left.mint, "DEADU");
  assert.equal(left.noLocalRoute, true, "leftover is flagged as having no local route");
  assert.ok(isNoLocalRouteError(left.lastReason), "leftover reason classifies as no-local-route");
  assert.equal(deadSellAttempts, 3, "the no-route token was retried a bounded number of times (3)");
  assert.equal(report.jupiterFallbackEnabled, false, "report records Jupiter stayed disabled");
  console.log("Scenario B passed: no-local-route token surfaced in remaining, bounded retries, no Jupiter.");
}

// ---------------------------------------------------------------------------
// Scenario C: simulation mode sells tracked positions but never touches the live wallet.
// ---------------------------------------------------------------------------
{
  let listCalls = 0;
  const deps: PanicLiquidateDeps = {
    pauseBuys: () => {},
    stopBot: () => {},
    isSimulationMode: () => true,
    jupiterFallbackEnabled: false,
    getTrackedPositions: () => [{ mint: "SIMT1", symbol: "S1", heldRaw: 10 }],
    sellTrackedPosition: async () => {},
    listWalletBalances: async () => {
      listCalls++;
      return [];
    },
    sellWalletBalance: async () => {
      throw new Error("must not sell wallet balances in simulation");
    },
    isSkippableMint: () => false,
    isFastBuyPending: () => false,
  };

  const report = await runPanicLiquidate(deps, { maxWalletSweepPasses: 3, retryDelayMs: 0 });
  assert.equal(report.simulation, true);
  assert.deepEqual(report.trackedSold.map((t) => t.mint), ["SIMT1"]);
  assert.equal(listCalls, 0, "simulation must not scan the live wallet");
  assert.deepEqual(report.remaining, []);
  console.log("Scenario C passed: simulation liquidation skips the live wallet sweep.");
}

// ---------------------------------------------------------------------------
// Scenario D: wSOL/USDC are never swept; a fast-buy-pending mint is skipped (not sold) and reported.
// ---------------------------------------------------------------------------
{
  const wallet = makeWallet([
    { mint: "So11111111111111111111111111111111111111112", amountRaw: 999 }, // wSOL, skippable
    { mint: "PENDINGU", amountRaw: 3 }, // fast buy still confirming
    { mint: "PLAINU", amountRaw: 8 },
  ]);
  const sold: string[] = [];

  const deps: PanicLiquidateDeps = {
    pauseBuys: () => {},
    stopBot: () => {},
    isSimulationMode: () => false,
    jupiterFallbackEnabled: false,
    getTrackedPositions: () => [],
    sellTrackedPosition: async () => {},
    listWalletBalances: wallet.list,
    sellWalletBalance: async (mint) => {
      sold.push(mint);
      wallet.clear(mint);
    },
    isSkippableMint: (mint) => mint === "So11111111111111111111111111111111111111112",
    isFastBuyPending: (mint) => mint === "PENDINGU",
  };

  const report = await runPanicLiquidate(deps, { maxWalletSweepPasses: 2, retryDelayMs: 0 });

  assert.deepEqual(sold, ["PLAINU"], "only the plain untracked balance is sold");
  assert.ok(
    !sold.includes("So11111111111111111111111111111111111111112"),
    "wSOL is never swept"
  );
  assert.ok(!sold.includes("PENDINGU"), "a fast-buy-pending mint is not sold");
  const pending = report.remaining.find((r) => r.mint === "PENDINGU");
  assert.ok(pending, "the skipped fast-buy-pending mint shows up as a leftover");
  assert.equal(pending!.noLocalRoute, false);
  assert.ok(!report.remaining.some((r) => r.mint === "So11111111111111111111111111111111111111112"));
  console.log("Scenario D passed: wSOL skipped, fast-buy-pending skipped and reported.");
}

// ---------------------------------------------------------------------------
// isNoLocalRouteError classifier unit checks.
// ---------------------------------------------------------------------------
{
  assert.equal(isNoLocalRouteError(NO_LOCAL_ROUTE_ERR), true);
  assert.equal(isNoLocalRouteError("buy local direct execution unavailable and JUPITER_FALLBACK_ENABLED=false; not using Jupiter fallback."), true);
  assert.equal(isNoLocalRouteError("PumpSwap pool not found"), true);
  assert.equal(isNoLocalRouteError("NO_ROUTES_FOUND"), true);
  assert.equal(isNoLocalRouteError("blockhash expired"), false, "transient errors are not no-route");
  assert.equal(isNoLocalRouteError(undefined), false);
  console.log("Classifier checks passed.");
}

console.log("All panic-liquidate tests passed.");
