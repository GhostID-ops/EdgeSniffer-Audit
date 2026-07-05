import assert from "node:assert/strict";
import { isCreationTransaction } from "./walletLaunchOriginAudit.js";
import type { OriginTransaction } from "./walletHistoricalPreEntryAudit.js";

const base: OriginTransaction = {
  signature: "sig",
  slot: 1,
  timestamp: 1,
  feePayer: "creator",
  type: null,
  source: "PUMP_FUN",
  description: null,
  nativeTransfers: [],
};

assert.equal(
  isCreationTransaction({ ...base, type: "CREATE" }),
  true
);
assert.equal(
  isCreationTransaction({ ...base, type: "CREATE_POOL" }),
  true
);
assert.equal(
  isCreationTransaction({
    ...base,
    description: "Creator created a new token",
  }),
  true
);
assert.equal(
  isCreationTransaction({ ...base, type: "SWAP" }),
  false
);

console.log("walletLaunchOriginAudit tests passed");
