import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { isPumpSwapPrewarmCandidateSwap } from "./pumpswapPrewarm.js";
import { USDC_MINT, WRAPPED_SOL_MINT } from "./tokenRegistry.js";
import type { DetectedSwap } from "./types.js";

const baseSwap: DetectedSwap = {
  signature: "source-signature",
  wallet: "EBLANtX7A7aKnakoB9qbqBqLV8y6z1ts8rw8a2RVrHJs",
  inputMint: WRAPPED_SOL_MINT,
  outputMint: "EW1WDYMW111111111111111111111111111111pump",
  amountRaw: 1_000_000_000,
  isNativeInput: true,
  detector: "test",
};

function testPrewarmCandidateFilter(): void {
  assert.equal(isPumpSwapPrewarmCandidateSwap(baseSwap), true, "SOL -> pump mint buy is a prewarm candidate");
  assert.equal(
    isPumpSwapPrewarmCandidateSwap({ ...baseSwap, inputMint: USDC_MINT, isNativeInput: false }),
    true,
    "USDC -> pump mint buy is a prewarm candidate"
  );
  assert.equal(
    isPumpSwapPrewarmCandidateSwap({ ...baseSwap, inputMint: baseSwap.outputMint, outputMint: WRAPPED_SOL_MINT }),
    false,
    "sell-like pump -> SOL swap must not prewarm"
  );
  assert.equal(
    isPumpSwapPrewarmCandidateSwap({ ...baseSwap, outputMint: "NotAPumpMint111111111111111111111111111111" }),
    false,
    "non-pump output mint must not prewarm"
  );
  assert.equal(
    isPumpSwapPrewarmCandidateSwap({ ...baseSwap, inputMint: "SomeOtherQuote111111111111111111111111111111" }),
    false,
    "unsupported quote mint must not prewarm"
  );
}

function testPrewarmAuditScriptReadsRpcCounters(): void {
  const dir = mkdtempSync(path.join(tmpdir(), "finalbot-prewarm-test-"));
  const telemetry = path.join(dir, "execution-telemetry.jsonl");
  const now = Date.now();
  writeFileSync(
    telemetry,
    [
      {
        tsMs: now,
        kind: "prewarm",
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: baseSwap.outputMint,
        sourceSignature: baseSwap.signature,
        wallet: baseSwap.wallet,
        elapsedMs: 25,
        success: true,
        details: { accountPlan: "canonical-pool-state", poolResolved: true },
      },
      {
        tsMs: now + 1,
        kind: "stage:build",
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: baseSwap.outputMint,
        sourceSignature: "tracked-buy",
        elapsedMs: 18,
        success: true,
        details: {
          accountPlan: "canonical-pool-state",
          planCacheHit: true,
          poolCacheHit: true,
          prewarmFresh: true,
          usedSourceRatioQuote: true,
          skippedLiveReserveRead: true,
          quoteRpcReadsInBuild: 0,
          rpcReadsInBuild: 0,
        },
      },
    ]
      .map((row) => JSON.stringify(row))
      .join("\n") + "\n",
    "utf8"
  );

  try {
    const out = execFileSync(
      process.execPath,
      ["scripts/auditPumpSwapPrewarm.mjs", `--telemetry=${telemetry}`, "--since=all", "--limit=5"],
      { encoding: "utf8" }
    );
    assert.match(out, /Prewarm events: 1/);
    assert.match(out, /Fresh-prewarmed builds: 1\/1 \(100\.0%\)/);
    assert.match(out, /Source-ratio fast quote: 1\/1 \(100\.0%\)/);
    assert.match(out, /RPC reads in build: p50=0 p90=0 \(quote-read zero=1\/1\)/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function run(): void {
  testPrewarmCandidateFilter();
  testPrewarmAuditScriptReadsRpcCounters();
  console.log("PumpSwap prewarm tests passed");
}

run();
