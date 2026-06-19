import assert from "node:assert/strict";
import { isBagsMint, shouldUseBagsDirectLane } from "./bagsRoute.js";
import type { ExecutionPlan } from "./executionPlanner.js";

const bagsPlan: ExecutionPlan = {
  side: "buy",
  mint: "ExampleMintBAGS",
  route: "jupiter-non-pump",
  reason: "non-pump mint",
};

assert.equal(isBagsMint("ExampleMintBAGS"), process.env.BAGS_MINT_SUFFIX_FILTER !== "");
assert.equal(shouldUseBagsDirectLane(bagsPlan), process.env.BAGS_DIRECT_ENABLED === "true");
assert.equal(
  shouldUseBagsDirectLane({ ...bagsPlan, mint: "ExampleMintpump", route: "pumpfun-bonding-curve" }),
  false
);
assert.equal(shouldUseBagsDirectLane({ ...bagsPlan, side: "sell" }), false);

console.log("BAGS route tests passed");
