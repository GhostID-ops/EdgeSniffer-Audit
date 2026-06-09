import { strict as assert } from "node:assert";
import { decideRetryAction } from "./executionRetry.js";

function assertDecision(
  msg: string,
  expected: { action: string; shouldQuarantine: boolean; category?: string; program?: string }
): void {
  const actual = decideRetryAction(msg);
  assert.equal(actual.action, expected.action, msg);
  assert.equal(actual.shouldQuarantine, expected.shouldQuarantine, msg);
  if (expected.category) assert.equal(actual.category, expected.category, msg);
  if (expected.program) assert.equal(actual.program, expected.program, msg);
}

assertDecision(
  "Program pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA failed: custom program error: 0x1787",
  {
    action: "rebuild-pool-state",
    shouldQuarantine: false,
    category: "liquidity-overflow",
    program: "pump_amm",
  }
);

assertDecision("PumpSwap preflight failed: {\"InstructionError\":[6,\"PrivilegeEscalation\"]}", {
  action: "rebuild-derived-accounts",
  shouldQuarantine: false,
  category: "account-privilege",
});

assertDecision("Transaction not found in status polling after 10000ms; suspected dropped transaction", {
  action: "fresh-blockhash-rebroadcast",
  shouldQuarantine: false,
  category: "dropped-or-expired",
});

assertDecision("Jupiter quote failed: 400 {\"errorCode\":\"NO_ROUTES_FOUND\"}", {
  action: "skip",
  shouldQuarantine: true,
  category: "no-route",
});

console.log("Execution retry tests passed");
