import assert from "node:assert/strict";
import {
  attachWalkForwardIdentity,
  profilePasses,
  simulateExit,
  summarizeWeighted,
  type CausalFeatureRow,
  type CausalProfile,
} from "./walletCausalResearch.js";

const base = {
  key: "mint:1",
  mint: "mint",
  entryTs: 1,
  outcome: "scalp_win" as const,
  roiPct: 20,
  priorBuys: 5,
  priorWallets: 4,
  priorBuyVolumeSol: 11,
  medianIndexLead: 180,
  priorSameSlotEvents: 5,
  creationKnown: true,
  creationSameSlot: true,
  creator: "creator",
  predecessor: "payer",
};
const walked = attachWalkForwardIdentity([
  base,
  { ...base, key: "mint:2", entryTs: 2, roiPct: -5, outcome: "loss" },
  { ...base, key: "mint:3", entryTs: 3 },
]);
assert.equal(walked[0]?.creatorPriorAppearances, 0);
assert.equal(walked[1]?.creatorPriorAppearances, 1);
assert.equal(walked[2]?.creatorPriorAppearances, 2);
assert.equal(walked[2]?.creatorPriorWinRatePct, 50);

const profile: CausalProfile = {
  id: "test",
  minBuys: 5,
  minWallets: 4,
  minBuyVolumeSol: 10,
  minIndexLead: 150,
  creation: "same_slot",
  identity: "none",
};
assert.equal(profilePasses(walked[0]!, profile), true);
assert.equal(
  profilePasses({ ...walked[0]!, priorWallets: 3 }, profile),
  false
);

const universe: CausalFeatureRow[] = [
  walked[0]!,
  { ...walked[1]!, outcome: "loss", roiPct: -5 },
];
const metrics = summarizeWeighted(
  universe,
  universe,
  { loss: 100, small_win: 0, scalp_win: 50, runner: 0 },
  0.5
);
assert.equal(metrics.rows, 2);
assert.ok(Math.abs(metrics.winRatePct - 100 / 3) < 1e-9);
assert.ok((metrics.profitFactor ?? 0) > 1);

const exit = simulateExit(
  { timestamp: 100, marketCapUsd: 100 },
  [
    { timestamp: 101, marketCapUsd: 108 },
    { timestamp: 102, marketCapUsd: 121 },
  ],
  {
    id: "tp20",
    takeProfitPct: 20,
    stopLossPct: -10,
    maxHoldSeconds: 10,
  },
  100
);
assert.equal(exit.reason, "take_profit");
assert.ok(exit.roiPct > 19 && exit.roiPct < 21);

console.log("walletCausalResearch tests passed");
