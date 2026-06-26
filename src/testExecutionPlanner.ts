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

const legacyNonSuffix: DetectedSwap = {
  ...base,
  sourceIxAccounts: Array.from({ length: 16 }, (_, i) => `legacy-account-${i}`),
};
assert.equal(planExecution(legacyNonSuffix).route, "pumpfun-bonding-curve");
assert.equal(planExecution({ ...legacyNonSuffix, sourceIxAccounts: undefined }).route, "jupiter-non-pump");
assert.equal(
  planExecution({
    ...base,
    sourceIxAccounts: undefined,
    sourcePumpSwapAccounts: Array.from({ length: 23 }, (_, i) => `pool-account-${i}`),
  }).route,
  "pumpswap-graduated"
);

assert.equal(
  planExecution({
    ...base,
    sourceIxAccounts: undefined,
    sourceRaydiumCpmmAccounts: Array.from({ length: 13 }, (_, i) => ({
      pubkey: `raydium-account-${i}`,
      isSigner: i === 0,
      isWritable: [0, 3, 4, 5, 6, 7, 12].includes(i),
    })),
  }).route,
  "raydium-cpmm"
);
console.log("Execution planner tests passed");


