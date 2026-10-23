import assert from "node:assert/strict";
import {
  calculateProportionalSellRaw,
  clearSourcePositions,
  recordSourceBuyRaw,
  recordSourceSellRaw,
} from "./sourcePositions.js";

clearSourcePositions();

recordSourceBuyRaw("wallet-a", "mint-a", 1_000);
let sell = calculateProportionalSellRaw({
  wallet: "wallet-a",
  mint: "mint-a",
  sourceSellAmountRaw: 250,
  ourHeldRaw: 10_000,
});
assert.equal(sell.amountRaw, 2_500);
assert.equal(sell.fallback, false);

recordSourceSellRaw("wallet-a", "mint-a", 250);
sell = calculateProportionalSellRaw({
  wallet: "wallet-a",
  mint: "mint-a",
  sourceSellAmountRaw: 750,
  ourHeldRaw: 7_500,
});
assert.equal(sell.amountRaw, 7_500);
assert.equal(sell.fallback, false);

sell = calculateProportionalSellRaw({
  wallet: "wallet-b",
  mint: "mint-a",
  sourceSellAmountRaw: 250,
  ourHeldRaw: 10_000,
});
assert.equal(sell.amountRaw, 0);
assert.equal(sell.fallback, true);

clearSourcePositions();

recordSourceBuyRaw("wallet-a", "mint-a", 1_000);
sell = calculateProportionalSellRaw({
  wallet: "wallet-a",
  mint: "mint-a",
  sourceSellAmountRaw: 10,
  ourHeldRaw: 10_000,
  minPct: 25,
  maxPct: 50,
});
assert.equal(sell.amountRaw, 2_500);

sell = calculateProportionalSellRaw({
  wallet: "wallet-a",
  mint: "mint-a",
  sourceSellAmountRaw: 900,
  ourHeldRaw: 10_000,
  minPct: 25,
  maxPct: 50,
});
assert.equal(sell.amountRaw, 5_000);

console.log("Source position proportional sell tests passed");
