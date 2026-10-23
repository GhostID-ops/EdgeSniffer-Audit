import assert from "node:assert/strict";
import {
  applyConfirmedTransactionState,
  applyContextualInventorySnapshot,
  authoritativeBalanceRaw,
  cappedQueuedSellRaw,
  emptyConfirmedInventoryLedger,
  shouldExpandQueuedSellAfterFailure,
} from "./confirmedInventory.js";

let ledger = emptyConfirmedInventoryLedger();
ledger = applyConfirmedTransactionState(ledger, {
  slot: 100,
  balanceRaw: 500,
  signature: "buy-100",
});
assert.equal(authoritativeBalanceRaw(ledger), 500);

ledger = applyConfirmedTransactionState(ledger, {
  slot: 99,
  balanceRaw: 999,
  signature: "late-old-sell",
});
assert.equal(authoritativeBalanceRaw(ledger), 500, "older callbacks must not overwrite inventory");

ledger = applyConfirmedTransactionState(ledger, {
  slot: 100,
  balanceRaw: 0,
  signature: "same-slot-sell",
});
assert.equal(authoritativeBalanceRaw(ledger), null, "same-slot ordering must be refreshed");
assert.equal(ledger.refreshFloorSlot, 100);

const staleSnapshot = applyContextualInventorySnapshot(ledger, {
  slot: 99,
  balanceRaw: 700,
  signature: "snapshot-99",
});
assert.equal(authoritativeBalanceRaw(staleSnapshot), null);

ledger = applyContextualInventorySnapshot(ledger, {
  slot: 101,
  balanceRaw: 250,
  signature: "snapshot-101",
});
assert.equal(authoritativeBalanceRaw(ledger), 250);

ledger = applyConfirmedTransactionState(ledger, {
  slot: 101,
  balanceRaw: 999,
  signature: "late-same-slot-callback",
});
assert.equal(
  authoritativeBalanceRaw(ledger),
  250,
  "an end-of-slot snapshot must outrank a late callback from the same slot"
);

ledger = applyConfirmedTransactionState(ledger, {
  slot: 102,
  balanceRaw: 0,
  signature: "sell-102",
});
assert.equal(authoritativeBalanceRaw(ledger), 0);

assert.equal(cappedQueuedSellRaw(1_500, 1_000), 1_000, "later reentry must remain held");
assert.equal(cappedQueuedSellRaw(700, 1_000), 700, "never sell more than confirmed inventory");
assert.equal(cappedQueuedSellRaw(700, 0), 0);
assert.equal(shouldExpandQueuedSellAfterFailure(4, undefined), true);
assert.equal(shouldExpandQueuedSellAfterFailure(4, { version: 4, side: "buy" }), true);
assert.equal(shouldExpandQueuedSellAfterFailure(4, { version: 5, side: "buy" }), false);
assert.equal(shouldExpandQueuedSellAfterFailure(4, { version: 5, side: "sell" }), true);

console.log("Confirmed inventory ledger tests passed.");
