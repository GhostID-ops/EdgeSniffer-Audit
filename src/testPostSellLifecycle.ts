import assert from "node:assert/strict";
import { shouldCancelPostSellRemainder, type PostSellRemainderIntent } from "./postSellLifecycle.js";

const sourceFull: PostSellRemainderIntent = { kind: "source-full", sourceSellVersion: 4 };
const forceFull: PostSellRemainderIntent = { kind: "force-full" };
const state = (side: "buy" | "sell", version: number) => ({
  side,
  version,
  signature: `${side}-${version}`,
  observedAt: version,
});

assert.equal(shouldCancelPostSellRemainder(undefined, sourceFull), false);
assert.equal(shouldCancelPostSellRemainder(state("sell", 4), sourceFull), false);
assert.equal(shouldCancelPostSellRemainder(state("buy", 3), sourceFull), false);
assert.equal(shouldCancelPostSellRemainder(state("buy", 5), sourceFull), true);
assert.equal(shouldCancelPostSellRemainder(state("sell", 6), sourceFull), false);
assert.equal(shouldCancelPostSellRemainder(state("buy", 99), forceFull), false);

console.log("Post-sell lifecycle decision tests passed.");
