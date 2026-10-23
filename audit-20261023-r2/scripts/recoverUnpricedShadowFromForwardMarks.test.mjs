import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

test("recovers only a matching opening and leaves mismatched marks unresolved", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shadow-recovery-"));
  const shadow = path.join(dir, "shadow.jsonl");
  const forward = path.join(dir, "forward.jsonl");
  const report = path.join(dir, "report.json");
  const exits = {
    takeProfitPct: 90, stopLossPct: -20, trailingStartPct: 25, trailingGivebackPct: 18,
    maxHoldMs: 120_000, noUpdateExitMs: 120_000, maxExitMarkAgeMs: 15_000,
    paperBetSol: 0.5, entryCostBps: 200, exitCostBps: 200, fixedRoundTripCostSol: 0.00002,
  };
  const rows = [
    { event: "shadow_open", tradeId: "a", profile: "p", mint: "mint-a", openedAtMs: 1_000_000, entryMarketCapUsd: 4_000, exits },
    { event: "shadow_close", tradeId: "a", mint: "mint-a", priced: false, reason: "unpriced_stale_exit", at: new Date(1_120_000).toISOString() },
    { event: "shadow_open", tradeId: "b", profile: "p", mint: "mint-b", openedAtMs: 2_000_000, entryMarketCapUsd: 4_000, exits },
    { event: "shadow_close", tradeId: "b", mint: "mint-b", priced: false, reason: "unpriced_stale_exit", at: new Date(2_120_000).toISOString() },
  ];
  fs.writeFileSync(shadow, `${rows.map(JSON.stringify).join("\n")}\n`);
  const marks = [
    { status: "ok", mint: "mint-a", openedAtMs: 1_000_000, entryMarketCapUsd: 4_000, requestedAtMs: 1_010_000, curveRoiPct: 95, curvePnlSol: 0.475 },
    { status: "ok", mint: "mint-b", openedAtMs: 2_010_000, entryMarketCapUsd: 8_000, requestedAtMs: 2_010_000, curveRoiPct: 95, curvePnlSol: 0.475 },
  ];
  fs.writeFileSync(forward, `${marks.map(JSON.stringify).join("\n")}\n`);

  execFileSync(process.execPath, [
    path.resolve("scripts/recoverUnpricedShadowFromForwardMarks.mjs"),
    `--shadow=${shadow}`, `--forward=${forward}`, `--out=${report}`,
  ]);
  const result = JSON.parse(fs.readFileSync(report, "utf8"));
  assert.equal(result.summary.recovered, 1);
  assert.equal(result.recoveredTrades[0].reason, "take_profit");
  assert.equal(result.unresolvedTrades[0].reason, "forward_marks_belong_to_different_opening");
  fs.rmSync(dir, { recursive: true, force: true });
});
