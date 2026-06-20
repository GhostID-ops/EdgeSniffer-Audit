import fs from "node:fs";
import path from "node:path";

const DEFAULT_INPUT = "data/path-wallet-audit-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq.json";
const DEFAULT_OUT_JSON = "data/omego-env-strategy-sim.json";
const DEFAULT_OUT_MD = "data/omego-env-strategy-sim.md";

function arg(name, fallback = "") {
  const prefix = `--${name}=`;
  const found = process.argv.find((value) => value.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

function pctMove(from, to) {
  return ((to - from) / from) * 100;
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtSol(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)} SOL`;
}

function fmtUsd(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `$${Math.round(value).toLocaleString()}`;
}

function short(value) {
  if (!value) return "-";
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function avg(values) {
  const xs = values.filter(Number.isFinite);
  return xs.reduce((sum, value) => sum + value, 0) / Math.max(1, xs.length);
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function quantile(values, q) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  return xs[Math.min(xs.length - 1, Math.max(0, Math.floor((xs.length - 1) * q)))];
}

function maxDrawdown(values) {
  let equity = 0;
  let peak = 0;
  let dd = 0;
  for (const value of values) {
    equity += value;
    peak = Math.max(peak, equity);
    dd = Math.min(dd, equity - peak);
  }
  return dd;
}

function parseSteps(raw) {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [peak, floor] = part.split(":").map(Number);
      return Number.isFinite(peak) && Number.isFinite(floor) ? { peak, floor } : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.peak - b.peak);
}

function simulateExit(row, strategy) {
  const candles = Array.isArray(row.candles) ? row.candles : [];
  const entry = candles[0]?.open;
  if (!entry || candles.length === 0) {
    return {
      exitPct: Number.isFinite(row.sourceExitPct) ? row.sourceExitPct : 0,
      exitTs: row.entryTs,
      reason: "source/no-candles",
    };
  }

  const stopLossPct = strategy.stopLossPct ?? null;
  const steps = strategy.trailingSteps ?? [];
  let activeFloor = null;
  let bestHigh = 0;

  for (const candle of candles) {
    const lowPct = pctMove(entry, candle.low);
    const highPct = pctMove(entry, candle.high);
    bestHigh = Math.max(bestHigh, highPct);

    for (const step of steps) {
      if (bestHigh >= step.peak) activeFloor = activeFloor == null ? step.floor : Math.max(activeFloor, step.floor);
    }

    if (stopLossPct != null && lowPct <= -stopLossPct) {
      return { exitPct: -stopLossPct, exitTs: candle.ts, reason: `SL${stopLossPct}` };
    }

    if (activeFloor != null && lowPct <= activeFloor) {
      return { exitPct: activeFloor, exitTs: candle.ts, reason: `trail-floor-${activeFloor}` };
    }

    if (strategy.takeProfitPct != null && highPct >= strategy.takeProfitPct) {
      return { exitPct: strategy.takeProfitPct, exitTs: candle.ts, reason: `TP${strategy.takeProfitPct}` };
    }
  }

  if (strategy.mirrorSourceExit && Number.isFinite(row.sourceExitPct)) {
    return {
      exitPct: row.sourceExitPct,
      exitTs: candles.at(-1)?.ts ?? row.entryTs,
      reason: "source-exit",
    };
  }

  return {
    exitPct: pctMove(entry, candles.at(-1).close),
    exitTs: candles.at(-1).ts,
    reason: "horizon-close",
  };
}

function passesFilter(row, scenario) {
  const mc = row.entryMarketCapUsdApprox;
  if (scenario.minMc != null && (!Number.isFinite(mc) || mc < scenario.minMc)) return false;
  if (scenario.maxMc != null && Number.isFinite(mc) && mc > scenario.maxMc) return false;
  if (scenario.maxEarlierBuys != null && (row.lead?.earlierBuys ?? Infinity) > scenario.maxEarlierBuys) return false;
  if (scenario.minEarlierBuys != null && (row.lead?.earlierBuys ?? -Infinity) < scenario.minEarlierBuys) return false;
  return true;
}

function simulateScenario(rows, scenario) {
  const sorted = rows
    .filter((row) => Number.isFinite(row.entryTs) && Number.isFinite(row.sourceBuySol))
    .sort((a, b) => a.entryTs - b.entryTs);

  const open = [];
  const trades = [];
  const skipped = { filter: 0, openCap: 0, exposureCap: 0, tooSmall: 0 };
  let maxOpen = 0;
  let maxExposure = 0;
  let totalDeployed = 0;

  for (const row of sorted) {
    for (let i = open.length - 1; i >= 0; i--) {
      if (open[i].exitTs <= row.entryTs) open.splice(i, 1);
    }

    if (!passesFilter(row, scenario)) {
      skipped.filter++;
      continue;
    }

    const sourceSized = row.sourceBuySol * (scenario.copyPct / 100);
    const sizeSol = Math.min(sourceSized, scenario.maxSolPerToken ?? sourceSized);
    if (!(sizeSol > 0)) {
      skipped.tooSmall++;
      continue;
    }

    const openExposure = open.reduce((sum, trade) => sum + trade.sizeSol, 0);
    if (scenario.maxOpenPositions != null && open.length >= scenario.maxOpenPositions) {
      skipped.openCap++;
      continue;
    }
    if (scenario.maxSessionDeployedSol != null && openExposure + sizeSol > scenario.maxSessionDeployedSol) {
      skipped.exposureCap++;
      continue;
    }

    const exit = simulateExit(row, scenario);
    const pnlSol = sizeSol * (exit.exitPct / 100);
    const trade = {
      mint: row.mint,
      symbol: row.symbol,
      entryTs: row.entryTs,
      exitTs: exit.exitTs,
      sizeSol,
      sourceBuySol: row.sourceBuySol,
      entryMarketCapUsdApprox: row.entryMarketCapUsdApprox,
      exitPct: exit.exitPct,
      pnlSol,
      reason: exit.reason,
      mfePct: row.mfePct,
      maePct: row.maePct,
      earlierBuys: row.lead?.earlierBuys ?? null,
    };
    trades.push(trade);
    open.push(trade);
    totalDeployed += sizeSol;
    maxOpen = Math.max(maxOpen, open.length);
    maxExposure = Math.max(maxExposure, open.reduce((sum, item) => sum + item.sizeSol, 0));
  }

  const pnls = trades.map((trade) => trade.pnlSol);
  const rois = trades.map((trade) => trade.exitPct);
  const wins = trades.filter((trade) => trade.pnlSol > 0);
  const losses = trades.filter((trade) => trade.pnlSol < 0);
  const grossWin = wins.reduce((sum, trade) => sum + trade.pnlSol, 0);
  const grossLoss = Math.abs(losses.reduce((sum, trade) => sum + trade.pnlSol, 0));

  return {
    name: scenario.name,
    env: scenario,
    sampleRows: sorted.length,
    trades: trades.length,
    skipped,
    totalDeployedSol: totalDeployed,
    totalPnlSol: pnls.reduce((sum, pnl) => sum + pnl, 0),
    avgPnlSol: avg(pnls),
    medianPnlSol: median(pnls),
    avgRoiPct: avg(rois),
    medianRoiPct: median(rois),
    winRatePct: trades.length > 0 ? (wins.length / trades.length) * 100 : 0,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
    maxDrawdownSol: maxDrawdown(pnls),
    maxOpenPositionsSeen: maxOpen,
    maxExposureSol: maxExposure,
    p10RoiPct: quantile(rois, 0.1),
    p90RoiPct: quantile(rois, 0.9),
    exitReasons: Object.fromEntries(
      [...trades.reduce((map, trade) => map.set(trade.reason, (map.get(trade.reason) ?? 0) + 1), new Map()).entries()]
        .sort((a, b) => b[1] - a[1])
    ),
    worst: [...trades].sort((a, b) => a.pnlSol - b.pnlSol).slice(0, 8),
    best: [...trades].sort((a, b) => b.pnlSol - a.pnlSol).slice(0, 8),
  };
}

const inputPath = arg("input", DEFAULT_INPUT);
const outJson = arg("out-json", DEFAULT_OUT_JSON);
const outMd = arg("out-md", DEFAULT_OUT_MD);
const data = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const rows = data.rows ?? [];

const recommendedSteps = parseSteps("20:5,35:15,50:25,75:45,100:70,150:110,250:180");
const tightSteps = parseSteps("15:3,25:8,35:15,50:25,75:45,100:70");
const moonSteps = parseSteps("30:5,50:20,75:35,100:60,150:100,250:180");

const scenarios = [
  {
    name: "Recommended 15% MC12-50k cap3 exp2.5",
    copyPct: 15,
    minMc: 12_000,
    maxMc: 50_000,
    maxOpenPositions: 3,
    maxSessionDeployedSol: 2.5,
    maxSolPerToken: 0.75,
    stopLossPct: 25,
    trailingSteps: recommendedSteps,
    mirrorSourceExit: true,
  },
  {
    name: "Loose 15% MC8-50k cap3 exp2.5",
    copyPct: 15,
    minMc: 8_000,
    maxMc: 50_000,
    maxOpenPositions: 3,
    maxSessionDeployedSol: 2.5,
    maxSolPerToken: 0.75,
    stopLossPct: 25,
    trailingSteps: recommendedSteps,
    mirrorSourceExit: true,
  },
  {
    name: "Tight 15% MC20-50k cap3 exp2.5",
    copyPct: 15,
    minMc: 20_000,
    maxMc: 50_000,
    maxOpenPositions: 3,
    maxSessionDeployedSol: 2.5,
    maxSolPerToken: 0.75,
    stopLossPct: 25,
    trailingSteps: recommendedSteps,
    mirrorSourceExit: true,
  },
  {
    name: "More exposure 20% MC12-50k cap5 exp5",
    copyPct: 20,
    minMc: 12_000,
    maxMc: 50_000,
    maxOpenPositions: 5,
    maxSessionDeployedSol: 5,
    maxSolPerToken: 1,
    stopLossPct: 25,
    trailingSteps: recommendedSteps,
    mirrorSourceExit: true,
  },
  {
    name: "Conservative 10% MC12-50k cap3 exp1.5",
    copyPct: 10,
    minMc: 12_000,
    maxMc: 50_000,
    maxOpenPositions: 3,
    maxSessionDeployedSol: 1.5,
    maxSolPerToken: 0.5,
    stopLossPct: 25,
    trailingSteps: tightSteps,
    mirrorSourceExit: true,
  },
  {
    name: "All MC 15% cap3 exp2.5",
    copyPct: 15,
    minMc: null,
    maxMc: 50_000,
    maxOpenPositions: 3,
    maxSessionDeployedSol: 2.5,
    maxSolPerToken: 0.75,
    stopLossPct: 25,
    trailingSteps: recommendedSteps,
    mirrorSourceExit: true,
  },
  {
    name: "Early buyers <=5 15% cap3 exp2.5",
    copyPct: 15,
    minMc: null,
    maxMc: 50_000,
    maxEarlierBuys: 5,
    maxOpenPositions: 3,
    maxSessionDeployedSol: 2.5,
    maxSolPerToken: 0.75,
    stopLossPct: 25,
    trailingSteps: moonSteps,
    mirrorSourceExit: true,
  },
];

const results = scenarios.map((scenario) => simulateScenario(rows, scenario));
const output = {
  generatedAt: new Date().toISOString(),
  inputPath,
  sample: data.sample,
  caveats: [
    "Uses cached candle-enriched path rows only.",
    "Source sell timing is not present in the path rows; mirror-source exits are applied at the candle horizon if no trailing/stop exit triggers first.",
    "Open-position and exposure caps are therefore most reliable for bot-managed stop/trailing exits, not source-exit timing.",
    "Fees/tips/slippage are not deducted.",
  ],
  results,
};

fs.mkdirSync(path.dirname(outJson), { recursive: true });
fs.writeFileSync(outJson, JSON.stringify(output, null, 2), "utf8");

const rowsMd = results
  .map((r) => (
    `| ${r.name} | ${r.trades} | ${r.skipped.filter} | ${r.skipped.openCap}/${r.skipped.exposureCap} | ${fmtSol(r.totalDeployedSol)} | ${fmtSol(r.totalPnlSol)} | ${fmtPct(r.totalDeployedSol > 0 ? (r.totalPnlSol / r.totalDeployedSol) * 100 : null)} | ${fmtPct(r.winRatePct)} | ${fmtPct(r.medianRoiPct)} | ${Number.isFinite(r.profitFactor) ? r.profitFactor.toFixed(2) : "inf"} | ${fmtSol(r.maxDrawdownSol)} | ${fmtSol(r.maxExposureSol)} |`
  ))
  .join("\n");

const best = results
  .map((r) => {
    const reasons = Object.entries(r.exitReasons).slice(0, 4).map(([k, v]) => `${k}:${v}`).join(", ");
    return `### ${r.name}

- Trades: ${r.trades}; skipped filter/open/exposure: ${r.skipped.filter}/${r.skipped.openCap}/${r.skipped.exposureCap}
- Total PnL: ${fmtSol(r.totalPnlSol)} on ${fmtSol(r.totalDeployedSol)} deployed (${fmtPct(r.totalDeployedSol > 0 ? (r.totalPnlSol / r.totalDeployedSol) * 100 : null)})
- Win rate / median ROI / PF: ${fmtPct(r.winRatePct)} / ${fmtPct(r.medianRoiPct)} / ${Number.isFinite(r.profitFactor) ? r.profitFactor.toFixed(2) : "inf"}
- Max exposure / max DD: ${fmtSol(r.maxExposureSol)} / ${fmtSol(r.maxDrawdownSol)}
- Exit reasons: ${reasons || "-"}
- Best token: ${r.best[0] ? `${r.best[0].symbol} ${fmtSol(r.best[0].pnlSol)} at ${fmtUsd(r.best[0].entryMarketCapUsdApprox)}` : "-"}
- Worst token: ${r.worst[0] ? `${r.worst[0].symbol} ${fmtSol(r.worst[0].pnlSol)} at ${fmtUsd(r.worst[0].entryMarketCapUsdApprox)}` : "-"}
`;
  })
  .join("\n");

const md = `# Omego Env Strategy Simulation

Generated: ${output.generatedAt}

Input: \`${inputPath}\`

## Caveats

${output.caveats.map((c) => `- ${c}`).join("\n")}

## Summary

| Scenario | Trades | Filter Skips | Open/Exposure Skips | Deployed | PnL | PnL / Deployed | Win % | Median ROI | PF | Max DD | Max Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
${rowsMd}

## Details

${best}

## Read

- Use this to compare buckets and caps, not as a perfect replay of live exits.
- The stronger live test candidates are the scenarios with positive PnL/deployed, low drawdown, and enough trades to matter.
- Rerun this against the weekly path audit output when the VPS background crawl finishes.
`;

fs.writeFileSync(outMd, md, "utf8");

console.log(md);
console.log(`Wrote ${outJson}`);
console.log(`Wrote ${outMd}`);
