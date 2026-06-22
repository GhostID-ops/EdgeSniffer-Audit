import assert from "node:assert/strict";
import { planExecution } from "./executionPlanner.js";
import { USDC_MINT, WRAPPED_SOL_MINT } from "./tokenRegistry.js";
import type { DetectedSwap } from "./types.js";

const wallet = "omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq";
const base: DetectedSwap = {
  signature: "source-signature",
  wallet,
  inputMint: USDC_MINT,
  outputMint: "D8Rk9rugKv3ibUJxBsbsj9U35DqMW34B2DzG6ASr76pS",
  amountRaw: 10_000_000,
  isNativeInput: false,
  sourceIxAccounts: Array.from({ length: 27 }, (_, i) => `account-${i}`),
};

assert.equal(planExecution(base).route, "pumpfun-v2-token2022");
assert.equal(
  planExecution({ ...base, inputMint: WRAPPED_SOL_MINT, isNativeInput: true }).route,
  "pumpfun-v2-token2022"
);
assert.equal(
  planExecution({ ...base, sourceIxAccounts: undefined }).route,
  "jupiter-non-pump"
);
assert.equal(
  planExecution({
    ...base,
    sourceIxAccounts: undefined,
    sourcePumpSwapAccounts: Array.from({ length: 23 }, (_, i) => `pool-account-${i}`),
  }).route,
  "pumpswap-graduated"
);

console.log("Execution planner tests passed");
