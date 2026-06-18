import assert from "node:assert/strict";
import { isPostBroadcastBuyUncertainty, shouldHoldBuyOnceAfterFailure } from "./buyOncePolicy.js";

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

console.log("Buy-once policy tests passed");
