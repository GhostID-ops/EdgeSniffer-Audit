import fs from "node:fs";
import path from "node:path";

const input = "data/cya-48h-mc50-analysis-pumpfun-pumpswap.json";
const outputJson = "data/cya-profit-ladder-sim.json";
const outputMd = "data/cya-profit-ladder-sim.md";

const data = JSON.parse(fs.readFileSync(input, "utf8"));
const rows = [...(data.rows ?? [])]
  .sort((a, b) => new Date(b.entryIso).getTime() - new Date(a.entryIso).getTime())
  .slice(0, 200);

const ladder = [
  { trigger: 30, sell: 50 },
  { trigger: 50, sell: 20 },
  { trigger: 75, sell: 15 },
];

function hit(row, trigger) {
  const key = `tp${trigger}`;
  if (typeof row.hit?.[key] === "boolean") return row.hit[key];
  return Number(row.mfePct ?? -Infinity) >= trigger;
}

function terminalExit(row) {
  if (Number.isFinite(row.trailingExit?.roiPct)) return row.trailingExit.roiPct;
  if (Number.isFinite(row.strategyReturns?.trailingCustom)) return row.strategyReturns.trailingCustom;
  return Number(row.sourceExitPct ?? 0);
}

function mirrorSource(row) {
  return Number(row.strategyReturns?.mirrorCyaFirstSell ?? row.sourceExitPct ?? 0);
}

function hybridSource(row) {
  return hit(row, 30) ? 0.5 * 30 + 0.5 * mirrorSource(row) : mirrorSource(row);
}

function hybridTrailing(row) {
  return hit(row, 30) ? 0.5 * 30 + 0.5 * terminalExit(row) : terminalExit(row);
}

function hybridLadder(row) {
  let remaining = 100;
  let weighted = 0;
  for (const step of ladder) {
    if (!hit(row, step.trigger)) continue;
    const sell = Math.min(remaining, step.sell);
    weighted += sell * step.trigger;
    remaining -= sell;
  }
  weighted += remaining * terminalExit(row);
  return weighted / 100;
}

function summarize(name, values) {
  const xs = values.filter(Number.isFinite);
  const wins = xs.filter((x) => x > 0);
  const losses = xs.filter((x) => x < 0);
  const grossWin = wins.reduce((s, x) => s + x, 0);
  const grossLoss = Math.abs(losses.reduce((s, x) => s + x, 0));
  const sorted = [...xs].sort((a, b) => a - b);
  const q = (p) => sorted.length ? sorted[Math.max(0, Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p)))] : null;
  return {
    name,
    trades: xs.length,
    winRatePct: xs.length ? (wins.length / xs.length) * 100 : 0,
    avgPct: xs.reduce((s, x) => s + x, 0) / Math.max(1, xs.length),
    medianPct: q(0.5),
    p25Pct: q(0.25),
    p75Pct: q(0.75),
    totalPct: xs.reduce((s, x) => s + x, 0),
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
  };
}

const strategies = [
  summarize("Mirror CyA first sell", rows.map(mirrorSource)),
  summarize("Hybrid 50%@30 then CyA sell", rows.map(hybridSource)),
  summarize("Hybrid 50%@30 then trailing", rows.map(hybridTrailing)),
  summarize("Hybrid + ladder 30:50,50:20,75:15 then trailing", rows.map(hybridLadder)),
];

const perTrade = rows.map((row) => ({
  mint: row.mint,
  symbol: row.symbol,
  entryIso: row.entryIso,
  sourceBuySol: row.sourceBuySol,
  mfePct: row.mfePct,
  sourceExitPct: row.sourceExitPct,
  trailingExitPct: terminalExit(row),
  mirrorSourcePct: mirrorSource(row),
  hybridSourcePct: hybridSource(row),
  hybridTrailingPct: hybridTrailing(row),
  hybridLadderPct: hybridLadder(row),
}));

const out = {
  generatedAt: new Date().toISOString(),
  sourceFile: input,
  requestedRows: 200,
  measuredRows: rows.length,
  caveat: "Only cached detailed path rows can be simulated exactly; broader CyA quant files retain aggregates only.",
  ladder,
  strategies,
  perTrade,
};
fs.writeFileSync(outputJson, JSON.stringify(out, null, 2));

function fmtPct(x) { return Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${x.toFixed(2)}%` : "-"; }
function fmtPf(x) { return x === Infinity ? "inf" : Number.isFinite(x) ? x.toFixed(2) : "-"; }
const lines = [
  "# CyA Profit Ladder Simulation",
  "",
  `Generated: ${out.generatedAt}`,
  `Source: \`${input}\``,
  `Requested: last cached 200 detailed trades`,
  `Measured detailed rows available: ${rows.length}`,
  "",
  "Caveat: the broader cached CyA quant files have 268+ measured entries, but only aggregate strategy stats. This simulation uses every cached row with enough path detail for ladder timing.",
  "",
  "Ladder tested: `30:50,50:20,75:15` then trailing remainder.",
  "",
  "| Strategy | Trades | Win Rate | Avg ROI | Median ROI | P25 | P75 | Profit Factor | Total ROI Pts |",
  "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
  ...strategies.map((s) => `| ${s.name} | ${s.trades} | ${fmtPct(s.winRatePct)} | ${fmtPct(s.avgPct)} | ${fmtPct(s.medianPct)} | ${fmtPct(s.p25Pct)} | ${fmtPct(s.p75Pct)} | ${fmtPf(s.profitFactor)} | ${fmtPct(s.totalPct)} |`),
  "",
  "## Top Delta Examples",
  "",
  "| Token | Entry | MFE | Hybrid+Trailing | Ladder | Delta |",
  "|---|---|---:|---:|---:|---:|",
  ...perTrade
    .map((r) => ({ ...r, delta: r.hybridLadderPct - r.hybridTrailingPct }))
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, 20)
    .map((r) => `| ${r.symbol || r.mint.slice(0, 6)} | ${r.entryIso} | ${fmtPct(r.mfePct)} | ${fmtPct(r.hybridTrailingPct)} | ${fmtPct(r.hybridLadderPct)} | ${fmtPct(r.delta)} |`),
  "",
];
fs.writeFileSync(outputMd, lines.join("\n"));
console.log(JSON.stringify({ outputJson, outputMd, measuredRows: rows.length, strategies }, null, 2));
