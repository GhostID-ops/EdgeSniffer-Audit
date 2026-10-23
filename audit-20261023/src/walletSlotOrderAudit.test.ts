import assert from "node:assert/strict";
import {
  classifySlotEvent,
  extractBlockSignatures,
} from "./walletSlotOrderAudit.js";
import type { FlowEvent } from "./walletHistoricalPreEntryAudit.js";

assert.deepEqual(
  extractBlockSignatures({ signatures: ["a", "entry", "b"] }),
  ["a", "entry", "b"]
);
assert.deepEqual(
  extractBlockSignatures({
    transactions: [
      { transaction: { signatures: ["a"] } },
      { signatures: ["entry"] },
    ],
  }),
  ["a", "entry"]
);

const event: FlowEvent = {
  signature: "a",
  slot: 10,
  timestamp: 100,
  side: "buy",
  wallet: "wallet",
  sol: 0.5,
  tokens: 100,
  marketCapUsd: 2_500,
};
assert.deepEqual(
  classifySlotEvent(event, 10, "entry", ["a", "entry", "b"]),
  {
    relation: "prior_same_slot",
    transactionIndex: 0,
    indexLead: 1,
  }
);
assert.deepEqual(
  classifySlotEvent(
    { ...event, signature: "b" },
    10,
    "entry",
    ["a", "entry", "b"]
  ),
  {
    relation: "after_same_slot",
    transactionIndex: 2,
    indexLead: -1,
  }
);
assert.equal(
  classifySlotEvent({ ...event, slot: 9 }, 10, "entry", []).relation,
  "prior_slot"
);
assert.equal(
  classifySlotEvent({ ...event, slot: 11 }, 10, "entry", []).relation,
  "after_slot"
);
assert.equal(
  classifySlotEvent({ ...event, slot: undefined }, 10, "entry", []).relation,
  "unknown_slot"
);

console.log("walletSlotOrderAudit tests passed");
