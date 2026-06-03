import { WRAPPED_SOL_MINT, USDC_MINT } from "./tokenRegistry.js";
import type { DetectedSwap } from "./types.js";

export type PlannedRoute =
  | "pumpfun-bonding-curve"
  | "pumpfun-v2-token2022"
  | "pumpswap-graduated"
  | "jupiter-non-pump"
  | "unsafe-unknown";

export type ExecutionPlan = {
  side: "buy" | "sell" | "swap";
  route: PlannedRoute;
  mint: string;
  reason: string;
};

export function planExecution(swap: DetectedSwap): ExecutionPlan {
  const isBuy =
    (swap.inputMint === WRAPPED_SOL_MINT || swap.inputMint === USDC_MINT) &&
    swap.outputMint !== WRAPPED_SOL_MINT &&
    swap.outputMint !== USDC_MINT;
  const isSell = swap.outputMint === WRAPPED_SOL_MINT || swap.outputMint === USDC_MINT;
  const side = isBuy ? "buy" : isSell ? "sell" : "swap";
  const mint = isSell ? swap.inputMint : swap.outputMint;

  if (side === "swap") {
    return { side, mint, route: "unsafe-unknown", reason: "token-to-token swap" };
  }

  if (!mint.endsWith("pump")) {
    return { side, mint, route: "jupiter-non-pump", reason: "non-pump mint" };
  }

  if (swap.sourcePumpSwapAccounts?.length) {
    return {
      side,
      mint,
      route: "pumpswap-graduated",
      reason: "source payload includes PumpSwap accounts",
    };
  }

  if (swap.sourceIxAccounts?.length && swap.sourceIxAccounts.length >= 27) {
    return {
      side,
      mint,
      route: "pumpfun-v2-token2022",
      reason: "source pump.fun v2 account layout",
    };
  }

  return {
    side,
    mint,
    route: "pumpfun-bonding-curve",
    reason: "pump mint without PumpSwap hints",
  };
}

