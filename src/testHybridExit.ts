import assert from "node:assert/strict";
import {
  needsHybridPriceCheck,
  planHybridPartialSellRaw,
  shouldCloseHybridRemainderOnSourceSell,
  waitsForSourceExit,
} from "./hybridExit.js";

const base = {
  enabled: true,
  tpDone: false,
  tpInFlight: false,
  tpPct: 30,
  tpSellPct: 50,
  heldRaw: 1_000,
};

assert.equal(planHybridPartialSellRaw({ ...base, pnlPct: 29.99 }), null);
assert.equal(planHybridPartialSellRaw({ ...base, pnlPct: 30 }), 500);
assert.equal(planHybridPartialSellRaw({ ...base, heldRaw: 999, pnlPct: 40 }), 499);
assert.equal(planHybridPartialSellRaw({ ...base, tpDone: true, pnlPct: 40 }), null);
assert.equal(planHybridPartialSellRaw({ ...base, tpInFlight: true, pnlPct: 40 }), null);
assert.equal(planHybridPartialSellRaw({ ...base, enabled: false, pnlPct: 40 }), null);
assert.equal(planHybridPartialSellRaw({ ...base, tpSellPct: 100, pnlPct: 40 }), 1_000);
assert.equal(planHybridPartialSellRaw({ ...base, tpSellPct: 150, pnlPct: 40 }), 1_000);

assert.equal(waitsForSourceExit(true, true), true);
assert.equal(waitsForSourceExit(true, false), false);
assert.equal(needsHybridPriceCheck(true, true, false), true);
assert.equal(needsHybridPriceCheck(true, true, true), false);
assert.equal(needsHybridPriceCheck(true, false, true), true);
assert.equal(
  shouldCloseHybridRemainderOnSourceSell({
    enabled: true,
    sourceExitRemaining: true,
    sourceSellRaw: 10,
    botHeldRaw: 500,
  }),
  true
);
assert.equal(
  shouldCloseHybridRemainderOnSourceSell({
    enabled: true,
    sourceExitRemaining: true,
    sourceSellRaw: 0,
    botHeldRaw: 500,
  }),
  false
);
assert.equal(
  shouldCloseHybridRemainderOnSourceSell({
    enabled: true,
    sourceExitRemaining: false,
    sourceSellRaw: 10,
    botHeldRaw: 500,
  }),
  false
);

console.log("Hybrid exit planner tests passed.");
