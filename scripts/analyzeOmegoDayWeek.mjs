import fs from "node:fs";
import path from "node:path";

const WALLET = "omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const TIME_ZONE = process.env.OMEGO_ANALYSIS_TZ ?? "America/Toronto";

const DEFAULT_INPUTS = [
  "data/path-audit-cache/wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-6000.json",
  "data/wallet-quant-audit-cache/wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-5000.json",
  "data/wallet-ledger-cache/wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-10000.json",
];

const OUT_JSON = "data/omego-day-weekend-analysis.json";
const OUT_MD = "data/omego-day-weekend-analysis.md";

function sol(lamports) {
  return lamports / 1_000_000_000;
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtSol(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

function fmtNum(value, digits = 1) {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toFixed(digits);
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function avg(values) {
  const xs = values.filter(Number.isFinite);
  return xs.length ? xs.reduce((sum, value) => sum + value, 0) / xs.length : null;
}

function pct(count, total) {
  return total > 0 ? (count / total) * 100 : 0;
}

function profitFactor(values) {
  const wins = values.filter((value) => value > 0).reduce((sum, value) => sum + value, 0);
  const losses = Math.abs(values.filter((value) => value < 0).reduce((sum, value) => sum + value, 0));
  if (losses === 0) return wins > 0 ? Infinity : 0;
  return wins / losses;
}

function localParts(ts) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: TIME_ZONE,
      weekday: "short",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
      .formatToParts(new Date(ts * 1000))
      .map((part) => [part.type, part.value])
  );
  const date = `${parts.year}-${parts.month}-${parts.day}`;
  return {
    date,
    weekday: parts.weekday,
    isWeekend: parts.weekday === "Sat" || parts.weekday === "Sun",
  };
}

function extractSwap(tx) {
  let nativeNet = 0;
  const tokenNet = new Map();
  const decimalsByMint = new Map();

  for (const account of tx.accountData ?? []) {
    if (account.account === WALLET) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== WALLET) continue;
      const mint = change.mint;
      if (!mint || mint === WRAPPED_SOL_MINT) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
      decimalsByMint.set(mint, change.rawTokenAmount?.decimals ?? 0);
    }
  }

  let mint = null;
  let tokenRaw = 0;
  for (const [candidate, raw] of tokenNet) {
    if (Math.abs(raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = raw;
    }
  }

  if (!mint || tokenRaw === 0 || nativeNet === 0) return null;
  if (nativeNet < 0 && tokenRaw > 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "buy",
      mint,
      solLamports: Math.abs(nativeNet),
      tokenRaw: Math.abs(tokenRaw),
      decimals: decimalsByMint.get(mint) ?? 0,
    };
  }
  if (nativeNet > 0 && tokenRaw < 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "sell",
      mint,
      solLamports: nativeNet,
      tokenRaw: Math.abs(tokenRaw),
      decimals: decimalsByMint.get(mint) ?? 0,
    };
  }
  return null;
}

function emptyCampaign(mint, firstBuy) {
  return {
    mint,
    firstBuy,
    firstSell: null,
    lastTs: firstBuy.timestamp,
    buys: [],
    sells: [],
    spentLamports: 0,
    boughtRaw: 0,
    receivedLamports: 0,
    soldRaw: 0,
    decimals: firstBuy.decimals,
  };
}

function buildCampaigns(swaps) {
  const openByMint = new Map();
  const campaigns = [];
  const sorted = [...swaps].sort((a, b) => a.timestamp - b.timestamp || a.signature.localeCompare(b.signature));

  for (const swap of sorted) {
    let campaign = openByMint.get(swap.mint);
    if (!campaign) {
      if (swap.side !== "buy") continue;
      campaign = emptyCampaign(swap.mint, swap);
      openByMint.set(swap.mint, campaign);
      campaigns.push(campaign);
    }

    campaign.lastTs = Math.max(campaign.lastTs, swap.timestamp);
    if (swap.side === "buy") {
      campaign.buys.push(swap);
      campaign.spentLamports += swap.solLamports;
      campaign.boughtRaw += swap.tokenRaw;
      continue;
    }

    campaign.sells.push(swap);
    campaign.firstSell ??= swap;
    campaign.receivedLamports += swap.solLamports;
    campaign.soldRaw += swap.tokenRaw;

    if (campaign.boughtRaw > 0 && campaign.soldRaw >= campaign.boughtRaw * 0.995) {
      openByMint.delete(swap.mint);
    }
  }

  return campaigns;
}

function campaignRealized(campaign) {
  if (
    campaign.spentLamports <= 0 ||
    campaign.boughtRaw <= 0 ||
    campaign.receivedLamports <= 0 ||
    campaign.soldRaw <= 0
  ) {
    return null;
  }
  const soldRaw = Math.min(campaign.soldRaw, campaign.boughtRaw);
  const costLamports = (campaign.spentLamports / campaign.boughtRaw) * soldRaw;
  const pnlLamports = campaign.receivedLamports - costLamports;
  return {
    costSol: sol(costLamports),
    proceedsSol: sol(campaign.receivedLamports),
    pnlSol: sol(pnlLamports),
    roiPct: costLamports > 0 ? (pnlLamports / costLamports) * 100 : null,
    closed: campaign.soldRaw >= campaign.boughtRaw * 0.995,
    holdMinutes: campaign.firstSell ? (campaign.firstSell.timestamp - campaign.firstBuy.timestamp) / 60 : null,
  };
}

function summarizeCampaigns(name, campaigns) {
  const realized = campaigns.map((campaign) => campaignRealized(campaign)).filter(Boolean);
  const roi = realized.map((row) => row.roiPct).filter(Number.isFinite);
  const pnl = realized.map((row) => row.pnlSol).filter(Number.isFinite);
  const spent = campaigns.map((campaign) => sol(campaign.spentLamports)).filter(Number.isFinite);
  const buyCounts = campaigns.map((campaign) => campaign.buys.length).filter(Number.isFinite);
  const holds = realized.map((row) => row.holdMinutes).filter(Number.isFinite);
  const dcaCampaigns = campaigns.filter((campaign) => campaign.buys.length > 1).length;
  const uniqueMints = new Set(campaigns.map((campaign) => campaign.mint)).size;
  return {
    name,
    roundTrips: campaigns.length,
    uniqueMints,
    realizedCampaigns: realized.length,
    closedCampaigns: realized.filter((row) => row.closed).length,
    buys: campaigns.reduce((sum, campaign) => sum + campaign.buys.length, 0),
    sells: campaigns.reduce((sum, campaign) => sum + campaign.sells.length, 0),
    sourceSpentSol: campaigns.reduce((sum, campaign) => sum + sol(campaign.spentLamports), 0),
    realizedPnlSol: pnl.reduce((sum, value) => sum + value, 0),
    winRatePct: pct(roi.filter((value) => value > 0).length, roi.length),
    avgRoiPct: avg(roi),
    medianRoiPct: median(roi),
    profitFactor: profitFactor(roi),
    medianBuySol: median(spent),
    medianBuysPerCampaign: median(buyCounts),
    dcaCampaignPct: pct(dcaCampaigns, campaigns.length),
    medianHoldMinutes: median(holds),
  };
}

function table(rows) {
  const header = [
    "Bucket",
    "Round trips",
    "Mints",
    "Realized",
    "Buys",
    "Sells",
    "Spent SOL",
    "PnL SOL",
    "Win %",
    "Avg ROI",
    "Med ROI",
    "PF",
    "DCA %",
    "Med hold",
  ];
  const lines = [
    `| ${header.join(" | ")} |`,
    `| ${header.map(() => "---").join(" | ")} |`,
  ];
  for (const row of rows) {
    lines.push(
      `| ${row.name} | ${row.roundTrips} | ${row.uniqueMints} | ${row.realizedCampaigns} | ${row.buys} | ${row.sells} | ${fmtSol(row.sourceSpentSol)} | ${fmtSol(row.realizedPnlSol)} | ${fmtPct(row.winRatePct)} | ${fmtPct(row.avgRoiPct)} | ${fmtPct(row.medianRoiPct)} | ${row.profitFactor === Infinity ? "inf" : fmtNum(row.profitFactor, 2)} | ${fmtPct(row.dcaCampaignPct)} | ${row.medianHoldMinutes == null ? "-" : `${fmtNum(row.medianHoldMinutes, 1)}m`} |`
    );
  }
  return lines.join("\n");
}

function loadTransactions(files) {
  const bySig = new Map();
  const sources = [];
  for (const file of files) {
    if (!fs.existsSync(file)) continue;
    const rows = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!Array.isArray(rows)) continue;
    sources.push({ file, rows: rows.length });
    for (const tx of rows) {
      if (!tx?.signature) continue;
      bySig.set(tx.signature, tx);
    }
  }
  return { transactions: [...bySig.values()], sources };
}

function main() {
  const inputFiles = process.argv.slice(2);
  const files = inputFiles.length ? inputFiles : DEFAULT_INPUTS;
  const { transactions, sources } = loadTransactions(files);
  const swaps = transactions
    .map(extractSwap)
    .filter((swap) => swap && swap.timestamp > 0)
    .sort((a, b) => a.timestamp - b.timestamp);
  const campaigns = buildCampaigns(swaps);

  const byDate = new Map();
  const byWeekday = new Map();
  for (const campaign of campaigns) {
    const parts = localParts(campaign.firstBuy.timestamp);
    const dayKey = `${parts.date} ${parts.weekday}`;
    if (!byDate.has(dayKey)) byDate.set(dayKey, []);
    byDate.get(dayKey).push(campaign);
    if (!byWeekday.has(parts.weekday)) byWeekday.set(parts.weekday, []);
    byWeekday.get(parts.weekday).push(campaign);
  }

  const weekend = campaigns.filter((campaign) => localParts(campaign.firstBuy.timestamp).isWeekend);
  const weekday = campaigns.filter((campaign) => !localParts(campaign.firstBuy.timestamp).isWeekend);
  const dayRows = [...byDate.entries()]
    .map(([name, xs]) => summarizeCampaigns(name, xs))
    .sort((a, b) => a.name.localeCompare(b.name));
  const weekdayOrder = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const weekdayRows = weekdayOrder
    .filter((name) => byWeekday.has(name))
    .map((name) => summarizeCampaigns(name, byWeekday.get(name)));
  const summaryRows = [
    summarizeCampaigns("All cached", campaigns),
    summarizeCampaigns("Weekdays", weekday),
    summarizeCampaigns("Weekends", weekend),
  ];

  const ts = swaps.map((swap) => swap.timestamp);
  const output = {
    wallet: WALLET,
    generatedAt: new Date().toISOString(),
    timezone: TIME_ZONE,
    sources,
    coverage: {
      transactions: transactions.length,
      swaps: swaps.length,
      roundTrips: campaigns.length,
      uniqueMints: new Set(campaigns.map((campaign) => campaign.mint)).size,
      oldest: ts.length ? new Date(Math.min(...ts) * 1000).toISOString() : null,
      newest: ts.length ? new Date(Math.max(...ts) * 1000).toISOString() : null,
      note: "Campaigns are reconstructed from cached wallet transactions only; market-cap and GMGN-only labels are not included.",
    },
    summary: summaryRows,
    byDay: dayRows,
    byWeekday: weekdayRows,
  };

  fs.writeFileSync(OUT_JSON, JSON.stringify(output, null, 2));

  const md = [
    "# Omego Day / Weekend Analysis",
    "",
    `Generated: ${output.generatedAt}`,
    `Timezone: ${TIME_ZONE}`,
    "",
    "## Coverage",
    "",
    `- Cached transactions: ${output.coverage.transactions}`,
    `- Extracted swaps: ${output.coverage.swaps}`,
    `- Reconstructed round trips: ${output.coverage.roundTrips}`,
    `- Unique mints touched: ${output.coverage.uniqueMints}`,
    `- Oldest swap: ${output.coverage.oldest ?? "-"}`,
    `- Newest swap: ${output.coverage.newest ?? "-"}`,
    "",
    "Input caches:",
    ...sources.map((source) => `- ${source.file}: ${source.rows} txs`),
    "",
    "## Weekend vs Weekday",
    "",
    table(summaryRows),
    "",
    "## Day By Day",
    "",
    table(dayRows),
    "",
    "## Weekday Buckets",
    "",
    table(weekdayRows),
    "",
    "## Read",
    "",
    "- This is a wallet-ledger realized-PnL view reconstructed from cached transactions, not a full GMGN export.",
    "- Round trips are bucketed by first buy date in Toronto time.",
    "- Realized ROI excludes still-open/unrealized inventory.",
    "- Weekend buckets are currently thinner than weekday buckets; treat this as directional until we collect a continuous multi-week cache.",
  ].join("\n");
  fs.writeFileSync(OUT_MD, md);

  console.log(md);
  console.log(`\nWrote ${OUT_JSON}`);
  console.log(`Wrote ${OUT_MD}`);
}

main();
