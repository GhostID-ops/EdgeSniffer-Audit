import "dotenv/config";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

type SourceSummaryFile = {
  summary: {
    wallet: string;
    totalSpent: number;
    totalReceived: number;
    net: number;
    wins: number;
    losses: number;
    winRate: number;
  };
  groups: CampaignGroup[];
  rows: SourceRow[];
};

type CampaignGroup = {
  mint: string;
  buys: number;
  sells: number;
  spent: number;
  received: number;
  net: number;
  openToken?: number;
};

type SourceRow = {
  time: string;
  ts: number;
  sig: string;
  source?: string;
  type?: string;
  side: string;
  mint?: string;
  tokenNet?: number;
  nativeSol?: number;
  feeSol?: number;
  err?: string;
};

type InvestigationOutput = {
  wallet: string;
  campaigns: InvestigationCampaign[];
  deep?: {
    mintSignals?: DeepMintSignal[];
    leadActors?: LeadActorStats[];
    completedMints?: number;
    requestedMints?: number;
    stoppedEarly?: boolean;
    stopReason?: string;
  };
};

type InvestigationCampaign = {
  mint: string;
  net: number;
  spent: number;
  buys: number;
  sells: number;
  firstBuySize: number;
  maxBuySize: number;
  buySizes: number[];
  buyIntervalsSec: number[];
  sellIntervalsFromFirstBuySec: number[];
  sizeProfile: string;
  ladderProfile: string;
  roi: number;
};

type DeepMintSignal = {
  mint: string;
  net: number;
  isWinner: boolean;
  firstBuyTs: number;
  sourceEntryAgeSec?: number;
  sourceBuyerRank?: number;
  preSourceActors: string[];
  historyQuality?: string;
};

type LeadActorStats = {
  actor: string;
  campaigns: number;
  wins: number;
  losses: number;
  net: number;
  avgLeadSec: number;
  examples: string[];
};

type CampaignModel = {
  group: CampaignGroup;
  investigation?: InvestigationCampaign;
  deep?: DeepMintSignal;
  rows: SourceRow[];
  buyRows: SourceRow[];
  sellRows: SourceRow[];
  firstBuyTs: number;
  secondBuyDelaySec?: number;
  thirdBuyDelaySec?: number;
  hasPreBuySell: boolean;
  preBuySellSol: number;
};

type BacktestAssumptions = {
  buyTxCostSol: number;
  sellTxCostSol: number;
  entryPenaltyBps: number;
  exitPenaltyBps: number;
  sourceSizeMultiplier: number;
  maxBuySol?: number;
};

type Policy = {
  name: string;
  description: string;
  liveFeasible: boolean;
  copyBuySol: (ctx: BuyContext) => number;
};

type BuyContext = {
  campaign: CampaignModel;
  row: SourceRow;
  buyIndex: number;
  sourceBuySol: number;
  assumptions: BacktestAssumptions;
  positiveLeadActors: Set<string>;
  negativeLeadActors: Set<string>;
};

type CampaignBacktest = {
  mint: string;
  touched: boolean;
  buysCopied: number;
  sellsCopied: number;
  spent: number;
  proceeds: number;
  costs: number;
  net: number;
  roi: number;
  leftoverTokens: number;
  sourceNet: number;
  sourceSpent: number;
  sourceRoi: number;
  rank?: number;
  entryAgeSec?: number;
  ladderProfile?: string;
};

type PolicyBacktest = {
  policy: string;
  description: string;
  liveFeasible: boolean;
  campaignsTouched: number;
  copiedBuys: number;
  copiedSells: number;
  spent: number;
  proceeds: number;
  costs: number;
  net: number;
  roi: number;
  winRate: number;
  avgWin: number;
  avgLoss: number;
  maxWin: number;
  maxLoss: number;
  leftoverCampaigns: number;
  campaignResults: CampaignBacktest[];
};

const ROOT = process.cwd();
const SOURCE_PATH = path.join(ROOT, "source-wallet-analysis-summary.json");
const INVESTIGATION_PATH = path.join(ROOT, "signal-investigation-output.json");
const REPORT_PATH = path.join(ROOT, "strategy-backtest-report.md");
const OUTPUT_PATH = path.join(ROOT, "strategy-backtest-output.json");

function numberArg(name: string, fallback: number): number {
  const prefix = `${name}=`;
  const raw = process.argv.find((arg) => arg.startsWith(prefix))?.slice(prefix.length);
  if (!raw) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function optionalNumberArg(name: string): number | undefined {
  const prefix = `${name}=`;
  const raw = process.argv.find((arg) => arg.startsWith(prefix))?.slice(prefix.length);
  if (!raw || raw.toLowerCase() === "none") return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

async function loadJson<T>(filePath: string): Promise<T> {
  return JSON.parse(await readFile(filePath, "utf8")) as T;
}

function rowBuySol(row: SourceRow): number {
  const native = Number(row.nativeSol ?? 0);
  return row.side === "buy" && native < 0 ? Math.abs(native) : 0;
}

function rowSellSol(row: SourceRow): number {
  const native = Number(row.nativeSol ?? 0);
  return row.side === "sell" && native > 0 ? native : 0;
}

function tokenNet(row: SourceRow): number {
  return Number(row.tokenNet ?? 0);
}

function sortByTs(rows: SourceRow[]): SourceRow[] {
  return [...rows].sort((a, b) => a.ts - b.ts || a.sig.localeCompare(b.sig));
}

function buildCampaigns(source: SourceSummaryFile, investigation: InvestigationOutput): CampaignModel[] {
  const rowsByMint = new Map<string, SourceRow[]>();
  for (const row of source.rows) {
    if (!row.mint) continue;
    const rows = rowsByMint.get(row.mint) ?? [];
    rows.push(row);
    rowsByMint.set(row.mint, rows);
  }

  const investigationByMint = new Map(investigation.campaigns.map((campaign) => [campaign.mint, campaign]));
  const deepByMint = new Map((investigation.deep?.mintSignals ?? []).map((signal) => [signal.mint, signal]));

  return source.groups.map((group) => {
    const rows = sortByTs(rowsByMint.get(group.mint) ?? []);
    const buyRows = rows.filter((row) => row.side === "buy" && rowBuySol(row) > 0 && tokenNet(row) > 0);
    const sellRows = rows.filter((row) => row.side === "sell" && rowSellSol(row) > 0 && tokenNet(row) < 0);
    const firstBuyTs = buyRows[0]?.ts ?? 0;
    const preBuySells = firstBuyTs > 0 ? sellRows.filter((row) => row.ts < firstBuyTs) : sellRows;
    const secondBuyDelaySec = buyRows.length >= 2 ? buyRows[1]!.ts - firstBuyTs : undefined;
    const thirdBuyDelaySec = buyRows.length >= 3 ? buyRows[2]!.ts - firstBuyTs : undefined;
    return {
      group,
      investigation: investigationByMint.get(group.mint),
      deep: deepByMint.get(group.mint),
      rows,
      buyRows,
      sellRows,
      firstBuyTs,
      secondBuyDelaySec,
      thirdBuyDelaySec,
      hasPreBuySell: preBuySells.length > 0,
      preBuySellSol: preBuySells.reduce((sum, row) => sum + rowSellSol(row), 0),
    };
  });
}

function copySize(sourceBuySol: number, assumptions: BacktestAssumptions, multiplier = assumptions.sourceSizeMultiplier): number {
  const scaled = sourceBuySol * multiplier;
  return assumptions.maxBuySol === undefined ? scaled : Math.min(scaled, assumptions.maxBuySol);
}

function actorSets(investigation: InvestigationOutput): {
  positiveLeadActors: Set<string>;
  negativeLeadActors: Set<string>;
} {
  const positiveLeadActors = new Set<string>();
  const negativeLeadActors = new Set<string>();
  for (const actor of investigation.deep?.leadActors ?? []) {
    const winRate = actor.campaigns > 0 ? actor.wins / actor.campaigns : 0;
    if (actor.campaigns >= 5 && actor.net > 5 && winRate >= 0.6) positiveLeadActors.add(actor.actor);
    if (actor.campaigns >= 5 && actor.net < -2 && winRate <= 0.5) negativeLeadActors.add(actor.actor);
  }
  return { positiveLeadActors, negativeLeadActors };
}

function makePolicies(): Policy[] {
  return [
    {
      name: "copy_all",
      description: "Copy every source buy and mirror source sell fractions.",
      liveFeasible: true,
      copyBuySol: ({ sourceBuySol, assumptions }) => copySize(sourceBuySol, assumptions),
    },
    {
      name: "fresh_campaigns_only",
      description: "Copy all buys only in campaigns with no source sell before the first observed buy.",
      liveFeasible: true,
      copyBuySol: ({ campaign, sourceBuySol, assumptions }) =>
        campaign.hasPreBuySell ? 0 : copySize(sourceBuySol, assumptions),
    },
    {
      name: "first_wave_only",
      description: "Copy only campaigns where deep attribution sees him as buyer rank 1-2.",
      liveFeasible: true,
      copyBuySol: ({ campaign, sourceBuySol, assumptions }) =>
        (campaign.deep?.sourceBuyerRank ?? 9999) <= 2 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "entry_age_lte_10s",
      description: "Copy only campaigns where detected entry age is 10 seconds or less.",
      liveFeasible: true,
      copyBuySol: ({ campaign, sourceBuySol, assumptions }) =>
        (campaign.deep?.sourceEntryAgeSec ?? 999999) <= 10 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "skip_crowded_rank_3_25",
      description: "Copy all except campaigns where he entered after a small early crowd, rank 3-25.",
      liveFeasible: true,
      copyBuySol: ({ campaign, sourceBuySol, assumptions }) => {
        const rank = campaign.deep?.sourceBuyerRank;
        if (rank !== undefined && rank >= 3 && rank <= 25) return 0;
        return copySize(sourceBuySol, assumptions);
      },
    },
    {
      name: "conviction_buys_only",
      description: "Copy only source buy legs of at least 3 SOL.",
      liveFeasible: true,
      copyBuySol: ({ sourceBuySol, assumptions }) =>
        sourceBuySol >= 3 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "first_wave_conviction",
      description: "Copy only 3+ SOL buy legs when he is first-wave rank 1-2.",
      liveFeasible: true,
      copyBuySol: ({ campaign, sourceBuySol, assumptions }) =>
        (campaign.deep?.sourceBuyerRank ?? 9999) <= 2 && sourceBuySol >= 3 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "fresh_first_wave_conviction",
      description: "Copy 3+ SOL buy legs only when the campaign is fresh and first-wave rank 1-2.",
      liveFeasible: true,
      copyBuySol: ({ campaign, sourceBuySol, assumptions }) =>
        !campaign.hasPreBuySell && (campaign.deep?.sourceBuyerRank ?? 9999) <= 2 && sourceBuySol >= 3
          ? copySize(sourceBuySol, assumptions)
          : 0,
    },
    {
      name: "fast_second_buy_confirm",
      description: "Wait for a second buy within 10 seconds, then copy from the second buy onward.",
      liveFeasible: true,
      copyBuySol: ({ campaign, buyIndex, sourceBuySol, assumptions }) =>
        buyIndex >= 1 && (campaign.secondBuyDelaySec ?? 999999) <= 10 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "fast_third_buy_confirm",
      description: "Wait for three buys within 10 seconds, then copy from the third buy onward.",
      liveFeasible: true,
      copyBuySol: ({ campaign, buyIndex, sourceBuySol, assumptions }) =>
        buyIndex >= 2 && (campaign.thirdBuyDelaySec ?? 999999) <= 10 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "positive_lead_actor_only",
      description: "Copy only when pre-source actors include recurring positive lead actors from attribution.",
      liveFeasible: true,
      copyBuySol: ({ campaign, sourceBuySol, assumptions, positiveLeadActors }) => {
        const actors = campaign.deep?.preSourceActors ?? [];
        return actors.some((actor) => positiveLeadActors.has(actor)) ? copySize(sourceBuySol, assumptions) : 0;
      },
    },
    {
      name: "avoid_negative_lead_actor",
      description: "Copy all except campaigns with recurring negative lead actors in the pre-source set.",
      liveFeasible: true,
      copyBuySol: ({ campaign, sourceBuySol, assumptions, negativeLeadActors }) => {
        const actors = campaign.deep?.preSourceActors ?? [];
        return actors.some((actor) => negativeLeadActors.has(actor)) ? 0 : copySize(sourceBuySol, assumptions);
      },
    },
    {
      name: "historical_rapid_multiladder",
      description: "Benchmark only: copy campaigns that eventually became rapid multi-ladders.",
      liveFeasible: false,
      copyBuySol: ({ campaign, sourceBuySol, assumptions }) =>
        campaign.investigation?.ladderProfile === "rapid multi-ladder" ? copySize(sourceBuySol, assumptions) : 0,
    },
  ];
}

function simulateCampaign(
  campaign: CampaignModel,
  policy: Policy,
  assumptions: BacktestAssumptions,
  positiveLeadActors: Set<string>,
  negativeLeadActors: Set<string>
): CampaignBacktest {
  let sourceHeld = 0;
  let copiedHeld = 0;
  let spent = 0;
  let proceeds = 0;
  let costs = 0;
  let buysCopied = 0;
  let sellsCopied = 0;
  let buyIndex = 0;

  for (const row of campaign.rows) {
    if (row.side === "buy") {
      const sourceBuySol = rowBuySol(row);
      const tokens = tokenNet(row);
      if (sourceBuySol > 0 && tokens > 0) {
        const copySol = policy.copyBuySol({
          campaign,
          row,
          buyIndex,
          sourceBuySol,
          assumptions,
          positiveLeadActors,
          negativeLeadActors,
        });
        if (copySol > 0) {
          const entryAdjustedSol = copySol * (1 + assumptions.entryPenaltyBps / 10_000);
          const tokenScale = copySol / sourceBuySol;
          copiedHeld += tokens * tokenScale;
          spent += entryAdjustedSol;
          costs += assumptions.buyTxCostSol;
          buysCopied += 1;
        }
        sourceHeld += tokens;
        buyIndex += 1;
      }
      continue;
    }

    if (row.side === "sell") {
      const soldTokens = Math.abs(tokenNet(row));
      const sourceSellSol = rowSellSol(row);
      if (soldTokens > 0 && sourceSellSol > 0 && sourceHeld > 0 && copiedHeld > 0) {
        const sellFraction = Math.min(1, soldTokens / sourceHeld);
        const copiedSellTokens = copiedHeld * sellFraction;
        const sourcePrice = sourceSellSol / soldTokens;
        const copiedReceive = copiedSellTokens * sourcePrice * (1 - assumptions.exitPenaltyBps / 10_000);
        copiedHeld -= copiedSellTokens;
        proceeds += copiedReceive;
        costs += assumptions.sellTxCostSol;
        sellsCopied += 1;
      }
      sourceHeld = Math.max(0, sourceHeld - soldTokens);
    }
  }

  const net = proceeds - spent - costs;
  return {
    mint: campaign.group.mint,
    touched: buysCopied > 0,
    buysCopied,
    sellsCopied,
    spent,
    proceeds,
    costs,
    net,
    roi: spent > 0 ? net / spent : 0,
    leftoverTokens: copiedHeld,
    sourceNet: campaign.group.net,
    sourceSpent: campaign.group.spent,
    sourceRoi: campaign.group.spent > 0 ? campaign.group.net / campaign.group.spent : 0,
    rank: campaign.deep?.sourceBuyerRank,
    entryAgeSec: campaign.deep?.sourceEntryAgeSec,
    ladderProfile: campaign.investigation?.ladderProfile,
  };
}

function simulatePolicy(
  campaigns: CampaignModel[],
  policy: Policy,
  assumptions: BacktestAssumptions,
  positiveLeadActors: Set<string>,
  negativeLeadActors: Set<string>
): PolicyBacktest {
  const campaignResults = campaigns.map((campaign) =>
    simulateCampaign(campaign, policy, assumptions, positiveLeadActors, negativeLeadActors)
  );
  const touched = campaignResults.filter((result) => result.touched);
  const wins = touched.filter((result) => result.net > 0);
  const losses = touched.filter((result) => result.net < 0);
  const spent = touched.reduce((sum, result) => sum + result.spent, 0);
  const proceeds = touched.reduce((sum, result) => sum + result.proceeds, 0);
  const costs = touched.reduce((sum, result) => sum + result.costs, 0);
  const net = touched.reduce((sum, result) => sum + result.net, 0);

  return {
    policy: policy.name,
    description: policy.description,
    liveFeasible: policy.liveFeasible,
    campaignsTouched: touched.length,
    copiedBuys: touched.reduce((sum, result) => sum + result.buysCopied, 0),
    copiedSells: touched.reduce((sum, result) => sum + result.sellsCopied, 0),
    spent,
    proceeds,
    costs,
    net,
    roi: spent > 0 ? net / spent : 0,
    winRate: touched.length > 0 ? wins.length / touched.length : 0,
    avgWin: wins.length > 0 ? wins.reduce((sum, result) => sum + result.net, 0) / wins.length : 0,
    avgLoss: losses.length > 0 ? losses.reduce((sum, result) => sum + result.net, 0) / losses.length : 0,
    maxWin: wins.length > 0 ? Math.max(...wins.map((result) => result.net)) : 0,
    maxLoss: losses.length > 0 ? Math.min(...losses.map((result) => result.net)) : 0,
    leftoverCampaigns: touched.filter((result) => Math.abs(result.leftoverTokens) > 0.001).length,
    campaignResults,
  };
}

function sol(value: number): string {
  return value.toFixed(3);
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function short(value: string, head = 6, tail = 4): string {
  return value.length <= head + tail ? value : `${value.slice(0, head)}...${value.slice(-tail)}`;
}

function markdownTable(rows: Array<Record<string, string | number>>, columns: string[]): string {
  if (rows.length === 0) return "_No rows._";
  return [
    `| ${columns.join(" | ")} |`,
    `| ${columns.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${columns.map((column) => String(row[column] ?? "")).join(" | ")} |`),
  ].join("\n");
}

function summaryRows(results: PolicyBacktest[]): Array<Record<string, string | number>> {
  return [...results]
    .sort((a, b) => b.net - a.net)
    .map((result) => ({
      policy: result.policy,
      live: result.liveFeasible ? "yes" : "benchmark",
      campaigns: result.campaignsTouched,
      buys: result.copiedBuys,
      sells: result.copiedSells,
      spent: sol(result.spent),
      costs: sol(result.costs),
      net: sol(result.net),
      roi: pct(result.roi),
      winRate: pct(result.winRate),
      maxLoss: sol(result.maxLoss),
    }));
}

function topCampaignRows(result: PolicyBacktest, direction: "wins" | "losses"): Array<Record<string, string | number>> {
  const touched = result.campaignResults.filter((campaign) => campaign.touched);
  const sorted = touched.sort((a, b) => (direction === "wins" ? b.net - a.net : a.net - b.net));
  return sorted.slice(0, 10).map((campaign) => ({
    mint: short(campaign.mint),
    net: sol(campaign.net),
    roi: pct(campaign.roi),
    spent: sol(campaign.spent),
    sourceNet: sol(campaign.sourceNet),
    rank: campaign.rank ?? "",
    age: campaign.entryAgeSec ?? "",
    ladder: campaign.ladderProfile ?? "",
  }));
}

function buildReport(
  source: SourceSummaryFile,
  investigation: InvestigationOutput,
  campaigns: CampaignModel[],
  assumptions: BacktestAssumptions,
  results: PolicyBacktest[],
  positiveLeadActors: Set<string>,
  negativeLeadActors: Set<string>
): string {
  const liveResults = results.filter((result) => result.liveFeasible && result.campaignsTouched > 0);
  const bestLive = [...liveResults].sort((a, b) => b.net - a.net)[0];
  const bestRoi = [...liveResults].filter((result) => result.spent >= 5).sort((a, b) => b.roi - a.roi)[0];
  const preBuySellCampaigns = campaigns.filter((campaign) => campaign.hasPreBuySell);
  const preBuySellSourceNet = preBuySellCampaigns.reduce((sum, campaign) => sum + campaign.group.net, 0);
  const preBuySellSol = preBuySellCampaigns.reduce((sum, campaign) => sum + campaign.preBuySellSol, 0);

  const lines = [
    "# Strategy Backtest Report",
    "",
    `Wallet: \`${source.summary.wallet}\``,
    `Generated: ${new Date().toISOString()}`,
    "",
    "## Assumptions",
    "",
    `- Backtest uses the source wallet's observed fills from \`source-wallet-analysis-summary.json\`.`,
    `- Copied buys receive the same token/SOL rate, then apply ${assumptions.entryPenaltyBps} bps entry penalty.`,
    `- Copied sells mirror the source wallet's sell fraction, then apply ${assumptions.exitPenaltyBps} bps exit penalty.`,
    `- Transaction costs are ${sol(assumptions.buyTxCostSol)} SOL per copied buy and ${sol(assumptions.sellTxCostSol)} SOL per copied sell.`,
    `- Leftover tokens are marked at zero. This is intentionally conservative for unresolved bags.`,
    `- Source-size multiplier is ${assumptions.sourceSizeMultiplier}x${assumptions.maxBuySol === undefined ? "" : ` with max buy cap ${sol(assumptions.maxBuySol)} SOL`}.`,
    `- Deep attribution coverage: ${investigation.deep?.completedMints ?? 0}/${investigation.deep?.requestedMints ?? 0} requested mints.`,
    "",
    "## Executive Read",
    "",
    bestLive
      ? `Best live-feasible policy by net was \`${bestLive.policy}\`: ${sol(bestLive.net)} SOL net on ${sol(bestLive.spent)} SOL spent, ${pct(bestLive.roi)} ROI.`
      : "No live-feasible policy produced copied trades.",
    bestRoi
      ? `Best live-feasible policy by ROI with at least 5 SOL deployed was \`${bestRoi.policy}\`: ${pct(bestRoi.roi)} ROI, ${sol(bestRoi.net)} SOL net.`
      : "",
    "The first-pass result should be treated as a policy ranking tool, not a final execution guarantee. Latency, liquidity impact, and hidden off-chain signals still need live shadow testing.",
    "",
    "## Data Quality Flags",
    "",
    `${preBuySellCampaigns.length} campaigns include a source sell before the first observed buy in this sample. Those campaigns contain hidden inventory from before the captured window.`,
    `Those flagged campaigns account for ${sol(preBuySellSourceNet)} SOL of source net and ${sol(preBuySellSol)} SOL of pre-buy sells. Fresh-only policies are included to avoid treating that as copyable edge.`,
    "",
    "## Policy Results",
    "",
    markdownTable(summaryRows(results), [
      "policy",
      "live",
      "campaigns",
      "buys",
      "sells",
      "spent",
      "costs",
      "net",
      "roi",
      "winRate",
      "maxLoss",
    ]),
    "",
    "## Lead Actor Sets",
    "",
    `Positive lead actors selected: ${positiveLeadActors.size}`,
    `Negative lead actors selected: ${negativeLeadActors.size}`,
    "",
    "## Best Live Policy: Top Wins",
    "",
    bestLive
      ? markdownTable(topCampaignRows(bestLive, "wins"), ["mint", "net", "roi", "spent", "sourceNet", "rank", "age", "ladder"])
      : "_No rows._",
    "",
    "## Best Live Policy: Worst Losses",
    "",
    bestLive
      ? markdownTable(topCampaignRows(bestLive, "losses"), ["mint", "net", "roi", "spent", "sourceNet", "rank", "age", "ladder"])
      : "_No rows._",
    "",
    "## Interpretation Notes",
    "",
    "1. If a policy wins by copying almost every trade, it is probably relying on the wallet's full edge and needs a latency/slippage stress test.",
    "2. If a policy wins by filtering hard, it may be overfit; the next test is live shadow collection.",
    "3. First-wave and entry-age filters are the current strongest signal candidates from attribution.",
    "4. Fast second/third buy confirmation is important because it is live-detectable without needing private token discovery.",
    "5. Historical rapid multi-ladder is a benchmark only; you cannot know that label at first entry.",
    "",
    "## Next Investigation Step",
    "",
    "Build a live shadow recorder that watches this wallet without trading and records what each policy would do in real time. The recorder should log detection latency, entry age, buyer rank when available, copied size, simulated fill penalty, and outcome after each sell.",
    "",
  ].filter((line) => line !== "");

  return lines.join("\n");
}

async function main(): Promise<void> {
  const source = await loadJson<SourceSummaryFile>(SOURCE_PATH);
  const investigation = await loadJson<InvestigationOutput>(INVESTIGATION_PATH);
  const assumptions: BacktestAssumptions = {
    buyTxCostSol: numberArg("--buy-tx-cost-sol", 0.006),
    sellTxCostSol: numberArg("--sell-tx-cost-sol", 0.0035),
    entryPenaltyBps: numberArg("--entry-penalty-bps", 150),
    exitPenaltyBps: numberArg("--exit-penalty-bps", 150),
    sourceSizeMultiplier: numberArg("--source-size-multiplier", 1),
    maxBuySol: optionalNumberArg("--max-buy-sol"),
  };

  const campaigns = buildCampaigns(source, investigation);
  const { positiveLeadActors, negativeLeadActors } = actorSets(investigation);
  const policies = makePolicies();
  const results = policies.map((policy) =>
    simulatePolicy(campaigns, policy, assumptions, positiveLeadActors, negativeLeadActors)
  );
  const report = buildReport(source, investigation, campaigns, assumptions, results, positiveLeadActors, negativeLeadActors);
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: source.summary.wallet,
    assumptions,
    positiveLeadActors: [...positiveLeadActors],
    negativeLeadActors: [...negativeLeadActors],
    dataQuality: {
      preBuySellCampaigns: campaigns.filter((campaign) => campaign.hasPreBuySell).map((campaign) => ({
        mint: campaign.group.mint,
        sourceNet: campaign.group.net,
        preBuySellSol: campaign.preBuySellSol,
      })),
    },
    results,
  };

  await writeFile(REPORT_PATH, report);
  await writeFile(OUTPUT_PATH, JSON.stringify(output, null, 2));
  console.log(`Wrote ${REPORT_PATH}`);
  console.log(`Wrote ${OUTPUT_PATH}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exitCode = 1;
});
