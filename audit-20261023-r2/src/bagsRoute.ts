import { bagsDirectEnabled, bagsMintSuffixFilter } from "./config.js";
import type { ExecutionPlan } from "./executionPlanner.js";

export function isBagsMint(mint: string): boolean {
  return bagsMintSuffixFilter.length > 0 && mint.endsWith(bagsMintSuffixFilter);
}

export function shouldUseBagsDirectLane(plan: ExecutionPlan): boolean {
  return bagsDirectEnabled && plan.side === "buy" && plan.route === "jupiter-non-pump" && isBagsMint(plan.mint);
}
