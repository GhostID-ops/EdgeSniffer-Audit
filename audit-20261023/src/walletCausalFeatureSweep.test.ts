import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { runCausalSweep } from "./walletCausalFeatureSweep.js";

const root = await mkdtemp(path.join(os.tmpdir(), "wallet-causal-sweep-"));
const previousCwd = process.cwd();

try {
  const edgeRoot = path.join(root, "edge");
  await mkdir(path.join(root, "data"), { recursive: true });
  await mkdir(path.join(edgeRoot, "data"), { recursive: true });
  const wallet = "wallet";
  const startTs = 1_800_000_000;
  const ledgerRows = Array.from({ length: 40 }, (_, index) => {
    const won = index % 2 === 1;
    return {
      mint: `wallet-mint-${index}`,
      entryTs: startTs + index,
      sourceExitRoiPct: won ? (index % 4 === 1 ? 20 : 60) : -5,
    };
  });
  const slotRows = ledgerRows.map((ledger, index) => {
    const won = ledger.sourceExitRoiPct > 0;
    return {
      ledger,
      provenPriorBuys: won ? 6 : 3,
      provenPriorWallets: won ? 5 : 3,
      provenPriorBuyVolumeSol: won ? 15 : 6,
      priorSameSlotEvents: won ? 6 : 3,
      medianSameSlotIndexLead: won ? 200 : 50,
    };
  });
  const originRows = ledgerRows.map((ledger, index) => {
    const won = ledger.sourceExitRoiPct > 0;
    return {
      ledger,
      creationSignature: `creation-${index}`,
      creationRelation: won ? "prior_same_slot" : "prior_slot",
      creator: `creator-${index % 3}`,
      immediatePredecessorFeePayer: `payer-${index % 4}`,
    };
  });
  const ledgerPath = path.join(root, "ledger.json");
  const slotPath = path.join(root, "slot.json");
  const originPath = path.join(root, "origin.json");
  await writeFile(
    ledgerPath,
    JSON.stringify({ wallet, rows: ledgerRows }),
    "utf8"
  );
  await writeFile(
    slotPath,
    JSON.stringify({ wallet, rows: slotRows }),
    "utf8"
  );
  await writeFile(
    originPath,
    JSON.stringify({ wallet, rows: originRows }),
    "utf8"
  );
  const controlLines: string[] = [];
  for (let index = 0; index < 120; index++) {
    const timestamp = startTs + (index % 40);
    const mint = `control-${index}`;
    controlLines.push(
      JSON.stringify({
        timestamp,
        mint,
        inferredMarketCapUsd: 1_000,
        context: {
          buyCount60: 6,
          uniqueWallets60: 5,
          volumeBuySol60: 15,
        },
      })
    );
    controlLines.push(
      JSON.stringify({
        timestamp: timestamp + 10,
        mint,
        inferredMarketCapUsd: index % 2 ? 1_200 : 1_050,
        context: {
          buyCount60: 7,
          uniqueWallets60: 6,
          volumeBuySol60: 16,
        },
      })
    );
  }
  await writeFile(
    path.join(edgeRoot, "data", "launch-paper-signals.jsonl"),
    `${controlLines.join("\n")}\n`,
    "utf8"
  );
  process.chdir(root);
  await runCausalSweep(
    ledgerPath,
    slotPath,
    originPath,
    edgeRoot,
    "fixture-sweep"
  );
  const output = JSON.parse(
    await readFile(path.join(root, "data", "fixture-sweep.json"), "utf8")
  ) as {
    coverage: {
      provenPriorRows: number;
      marketControls: number;
      marketControlsWithPath: number;
    };
    controlValidity: { eligible: boolean };
    topProfiles: unknown[];
    controlComparableProfiles: unknown[];
    marketControlSummary: Array<{
      negativeControls: number;
      triggeredPathWins: number;
    }>;
  };
  assert.equal(output.coverage.provenPriorRows, 40);
  assert.equal(output.coverage.marketControls, 120);
  assert.equal(output.coverage.marketControlsWithPath, 120);
  assert.equal(output.controlValidity.eligible, true);
  assert.ok(output.topProfiles.length > 0);
  assert.ok(output.controlComparableProfiles.length > 0);
  assert.ok(output.marketControlSummary[0]!.negativeControls > 0);
  assert.ok(output.marketControlSummary[0]!.triggeredPathWins > 0);
  console.log("walletCausalFeatureSweep integration test passed");
} finally {
  process.chdir(previousCwd);
  await rm(root, { recursive: true, force: true });
}
