import assert from "node:assert/strict";
import { isPostBroadcastBuyUncertainty, shouldBlockBuyOnceReentry, shouldHoldBuyOnceAfterFailure } from "./buyOncePolicy.js";

assert.equal(isPostBroadcastBuyUncertainty("PumpSwap confirmation failed after Rust broadcast: Transaction failed"), true);
assert.equal(isPostBroadcastBuyUncertainty("PumpSwap send/confirm failed: x | signature=abc"), true);
assert.equal(isPostBroadcastBuyUncertainty("PumpSwap account plan unavailable"), false);

assert.equal(
  shouldHoldBuyOnceAfterFailure({
    buyOncePerTokenEnabled: true,
    reason: "PumpSwap confirmation failed after Rust broadcast: Transaction failed",
  }),
  true
);
assert.equal(
  shouldHoldBuyOnceAfterFailure({
    buyOncePerTokenEnabled: false,
    reason: "PumpSwap confirmation failed after Rust broadcast: Transaction failed",
  }),
  false
);
assert.equal(
  shouldHoldBuyOnceAfterFailure({
    buyOncePerTokenEnabled: true,
    reason: 'Pump.fun direct buy failed: Transaction failed: {"InstructionError":[3,{"Custom":6042}]}',
  }),
  false
);


assert.equal(
  shouldBlockBuyOnceReentry({
    buyOncePerTokenEnabled: true,
    allowRebuyAfterFlat: false,
    attemptedThisSession: true,
    boughtThisSession: true,
    heldRaw: 0,
  }),
  true
);
assert.equal(
  shouldBlockBuyOnceReentry({
    buyOncePerTokenEnabled: true,
    allowRebuyAfterFlat: true,
    attemptedThisSession: true,
    boughtThisSession: true,
    heldRaw: 0,
  }),
  false
);
assert.equal(
  shouldBlockBuyOnceReentry({
    buyOncePerTokenEnabled: true,
    allowRebuyAfterFlat: true,
    attemptedThisSession: true,
    boughtThisSession: true,
    heldRaw: 42,
  }),
  true
);
console.log("Buy-once policy tests passed");
