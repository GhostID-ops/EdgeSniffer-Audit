import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { HeliusKeyPool, heliusKeysFromEnv } from "./heliusKeyPool.js";
import {
  loadProfiles,
  parseEnv,
  profileGates,
  scoreEvent,
  windowFeatures,
  type FlowEvent,
  type Profile,
} from "./walletHistoricalPreEntryAudit.js";

type LedgerRow = {
  mint: string;
  route: "pump" | "bags" | "other";
  entrySignature?: string;
  entryTs: number;
  sourceExitRoiPct: number;
};

type HistoricalRow = {
  ledger: LedgerRow;
  outcome: "loss" | "small_win" | "scalp_win" | "runner";
  fetchStatus: string;
  entrySlot: number | null;
  strictFlow?: FlowEvent[];
  sameSecondFlow?: FlowEvent[];
};

type HistoricalReport = {
  wallet: string;
  edgeRoot?: string;
  rows: HistoricalRow[];
};

type BlockResult = {
  signatures?: string[];
  transactions?: Array<
    | string
    | {
        transaction?: { signatures?: string[] };
        signatures?: string[];
      }
  >;
};

type RpcResponse = {
  result?: BlockResult | null;
  error?: { code?: number; message?: string };
};

export type SlotRelation =
  | "prior_slot"
  | "prior_same_slot"
  | "entry"
  | "after_same_slot"
  | "after_slot"
  | "unknown_slot"
  | "signature_not_in_block"
  | "entry_not_in_block";

type OrderedFlow = FlowEvent & {
  relation: SlotRelation;
  transactionIndex: number | null;
  indexLead: number | null;
};

type SlotAuditRow = {
  ledger: LedgerRow;
  outcome: HistoricalRow["outcome"];
  entrySlot: number | null;
  entryTransactionIndex: number | null;
  blockSignatureCount: number;
  strictEvents: number;
  sameSecondEvents: number;
  provenPriorEvents: number;
  provenPriorBuys: number;
  provenPriorSells: number;
  provenPriorWallets: number;
  provenPriorBuyVolumeSol: number;
  priorSameSlotEvents: number;
  afterSameSlotEvents: number;
  unknownOrderEvents: number;
  medianSameSlotIndexLead: number | null;
  profilePasses: Record<string, boolean>;
  bestProfileScores: Record<string, number | null>;
  missedGates: Record<string, string[]>;
  orderedSameSecondFlow: OrderedFlow[];
};

const KEYS = heliusKeysFromEnv();
const POOL = new HeliusKeyPool(KEYS, {
  cooldownMs: Number(process.env.HELIUS_KEY_COOLDOWN_MS ?? "60000"),
  maxAttempts: Number(
    process.env.HELIUS_KEY_MAX_ATTEMPTS ?? String(Math.max(12, KEYS.length * 5))
  ),
  onEvent: (event) => {
    if (event.event === "key_selected") return;
    console.warn(
      `[helius] ${event.event} keySlot=${event.keySlot}` +
        (event.status ? ` status=${event.status}` : "")
    );
  },
});
const CACHE_DIR = path.resolve(process.cwd(), "data", "slot-order-cache");
const REQUEST_SLEEP_MS = Number(process.env.WALLET_SLOT_ORDER_SLEEP_MS ?? "150");

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readJson<T>(file: string): Promise<T | null> {
  try {
    return JSON.parse((await readFile(file, "utf8")).replace(/^\uFEFF/, "")) as T;
  } catch {
    return null;
  }
}

async function writeJson(file: string, value: unknown): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value), "utf8");
}

export function extractBlockSignatures(result: BlockResult): string[] {
  if (Array.isArray(result.signatures)) {
    return result.signatures.filter((value): value is string => typeof value === "string");
  }
  return (result.transactions ?? [])
    .map((row) => {
      if (typeof row === "string") return row;
      return row.transaction?.signatures?.[0] ?? row.signatures?.[0];
    })
    .filter((value): value is string => typeof value === "string");
}

export async function fetchBlockSignatures(slot: number): Promise<string[]> {
  const cachePath = path.join(CACHE_DIR, `${slot}.json`);
  const cached = await readJson<{ signatures: string[] }>(cachePath);
  if (cached?.signatures?.length) return cached.signatures;
  const response = await POOL.request(
    (apiKey) => `https://mainnet.helius-rpc.com/?api-key=${apiKey}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: `slot-${slot}`,
        method: "getBlock",
        params: [
          slot,
          {
            commitment: "finalized",
            transactionDetails: "signatures",
            rewards: false,
            maxSupportedTransactionVersion: 0,
          },
        ],
      }),
      signal: AbortSignal.timeout(30_000),
    }
  );
  if (!response.ok) {
    throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 180)}`);
  }
  const payload = (await response.json()) as RpcResponse;
  if (payload.error) {
    throw new Error(
      `getBlock ${slot} RPC ${payload.error.code ?? ""}: ${payload.error.message ?? "unknown error"}`
    );
  }
  if (!payload.result) throw new Error(`getBlock ${slot} returned no block`);
  const signatures = extractBlockSignatures(payload.result);
  if (!signatures.length) {
    throw new Error(`getBlock ${slot} returned no transaction signatures`);
  }
  await writeJson(cachePath, { slot, signatures });
  await sleep(REQUEST_SLEEP_MS);
  return signatures;
}

export function classifySlotEvent(
  event: { signature: string; slot?: number | null },
  entrySlot: number | null,
  entrySignature: string | undefined,
  signatures: string[]
): Pick<OrderedFlow, "relation" | "transactionIndex" | "indexLead"> {
  if (event.signature === entrySignature) {
    const index = signatures.indexOf(event.signature);
    return { relation: "entry", transactionIndex: index >= 0 ? index : null, indexLead: 0 };
  }
  if (event.slot == null || entrySlot == null) {
    return { relation: "unknown_slot", transactionIndex: null, indexLead: null };
  }
  if (event.slot < entrySlot) {
    return { relation: "prior_slot", transactionIndex: null, indexLead: null };
  }
  if (event.slot > entrySlot) {
    return { relation: "after_slot", transactionIndex: null, indexLead: null };
  }
  const entryIndex = entrySignature ? signatures.indexOf(entrySignature) : -1;
  if (entryIndex < 0) {
    return { relation: "entry_not_in_block", transactionIndex: null, indexLead: null };
  }
  const eventIndex = signatures.indexOf(event.signature);
  if (eventIndex < 0) {
    return { relation: "signature_not_in_block", transactionIndex: null, indexLead: null };
  }
  if (eventIndex < entryIndex) {
    return {
      relation: "prior_same_slot",
      transactionIndex: eventIndex,
      indexLead: entryIndex - eventIndex,
    };
  }
  return {
    relation: "after_same_slot",
    transactionIndex: eventIndex,
    indexLead: entryIndex - eventIndex,
  };
}

function median(values: number[]): number | null {
  const rows = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!rows.length) return null;
  const mid = Math.floor(rows.length / 2);
  return rows.length % 2 ? rows[mid]! : (rows[mid - 1]! + rows[mid]!) / 2;
}

function percentage(value: number, total: number): number {
  return (value / Math.max(1, total)) * 100;
}

function profitFactor(values: number[]): number | null {
  const wins = values.filter((value) => value > 0).reduce((sum, value) => sum + value, 0);
  const losses = Math.abs(
    values.filter((value) => value < 0).reduce((sum, value) => sum + value, 0)
  );
  return losses > 0 ? wins / losses : wins > 0 ? null : 0;
}

function fmtPct(value: number | null): string {
  return value == null ? "-" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function sortCausal(events: OrderedFlow[]): OrderedFlow[] {
  return [...events].sort((a, b) => {
    const slotA = a.slot ?? Number.MAX_SAFE_INTEGER;
    const slotB = b.slot ?? Number.MAX_SAFE_INTEGER;
    if (slotA !== slotB) return slotA - slotB;
    const indexA = a.transactionIndex ?? Number.MAX_SAFE_INTEGER;
    const indexB = b.transactionIndex ?? Number.MAX_SAFE_INTEGER;
    if (indexA !== indexB) return indexA - indexB;
    return a.timestamp - b.timestamp;
  });
}

function evaluateProfiles(
  flow: OrderedFlow[],
  row: HistoricalRow,
  profiles: Profile[]
): {
  passes: Record<string, boolean>;
  scores: Record<string, number | null>;
  missedGates: Record<string, string[]>;
} {
  const buys = flow.filter((event) => event.side === "buy");
  const evaluations = buys.map((event) => {
    const prefix = flow.filter((candidate) => {
      if ((candidate.slot ?? -1) < (event.slot ?? -1)) return true;
      if (candidate.slot !== event.slot) return false;
      if (candidate.transactionIndex == null || event.transactionIndex == null) {
        return candidate.timestamp < event.timestamp || candidate.signature === event.signature;
      }
      return candidate.transactionIndex <= event.transactionIndex;
    });
    const context = windowFeatures(prefix, event.timestamp + 0.001, 60);
    const scores = Object.fromEntries(
      profiles.map((profile) => [
        profile.name,
        scoreEvent(
          event,
          context,
          false,
          row.ledger.route === "pump",
          profile.maxBuys,
          profile.maxSells,
          profile.maxBuyVolume
        ),
      ])
    );
    const gates = Object.fromEntries(
      profiles.map((profile) => [
        profile.name,
        profileGates(
          event,
          scores[profile.name] ?? 0,
          context,
          profile,
          row.ledger.route === "pump"
        ),
      ])
    );
    return { event, scores, gates };
  });
  const passes: Record<string, boolean> = {};
  const scores: Record<string, number | null> = {};
  const missedGates: Record<string, string[]> = {};
  for (const profile of profiles) {
    const ranked = [...evaluations].sort(
      (a, b) =>
        (a.gates[profile.name]?.length ?? Infinity) -
          (b.gates[profile.name]?.length ?? Infinity) ||
        (b.scores[profile.name] ?? 0) - (a.scores[profile.name] ?? 0)
    );
    const best = ranked[0];
    passes[profile.name] = Boolean(
      evaluations.find((evaluation) => evaluation.gates[profile.name]?.length === 0)
    );
    scores[profile.name] = best?.scores[profile.name] ?? null;
    missedGates[profile.name] = best?.gates[profile.name] ?? ["no_prior_buy"];
  }
  return { passes, scores, missedGates };
}

async function run(
  inputPath: string,
  edgeRoot: string,
  outputTag: string
): Promise<void> {
  if (!KEYS.length) throw new Error("No Helius API keys configured");
  const input = await readJson<HistoricalReport>(inputPath);
  if (!input?.rows?.length) throw new Error("Historical report has no rows");
  if (!input.rows.some((row) => Array.isArray(row.sameSecondFlow))) {
    throw new Error(
      "Historical report lacks flow arrays. Pull latest code and rerun audit:wallet-history first."
    );
  }
  let edgeEnv: Record<string, string> = {};
  try {
    edgeEnv = parseEnv(await readFile(path.join(edgeRoot, ".env"), "utf8"));
  } catch {}
  const profiles = loadProfiles(edgeEnv);
  const blockCache = new Map<number, string[]>();
  const rows: SlotAuditRow[] = [];

  for (let index = 0; index < input.rows.length; index++) {
    const source = input.rows[index]!;
    const sameSecond = source.sameSecondFlow ?? [];
    const strict = (source.strictFlow ?? []).map(
      (event): OrderedFlow => ({
        ...event,
        relation: "prior_slot",
        transactionIndex: null,
        indexLead: null,
      })
    );
    let signatures: string[] = [];
    if (
      source.entrySlot != null &&
      sameSecond.some((event) => event.slot === source.entrySlot)
    ) {
      signatures = blockCache.get(source.entrySlot) ?? [];
      if (!signatures.length) {
        signatures = await fetchBlockSignatures(source.entrySlot);
        blockCache.set(source.entrySlot, signatures);
      }
    }
    const orderedSameSecondFlow = sameSecond.map((event): OrderedFlow => ({
      ...event,
      ...classifySlotEvent(
        event,
        source.entrySlot,
        source.ledger.entrySignature,
        signatures
      ),
    }));
    const provenSameSecond = orderedSameSecondFlow.filter(
      (event) =>
        event.relation === "prior_slot" || event.relation === "prior_same_slot"
    );
    const provenPrior = sortCausal([...strict, ...provenSameSecond]);
    const profileEvaluation = evaluateProfiles(provenPrior, source, profiles);
    const buys = provenPrior.filter((event) => event.side === "buy");
    const sells = provenPrior.filter((event) => event.side === "sell");
    const sameSlotLeads = provenSameSecond
      .map((event) => event.indexLead ?? NaN)
      .filter(Number.isFinite);
    const entryIndex = source.ledger.entrySignature
      ? signatures.indexOf(source.ledger.entrySignature)
      : -1;
    rows.push({
      ledger: source.ledger,
      outcome: source.outcome,
      entrySlot: source.entrySlot,
      entryTransactionIndex: entryIndex >= 0 ? entryIndex : null,
      blockSignatureCount: signatures.length,
      strictEvents: strict.length,
      sameSecondEvents: sameSecond.length,
      provenPriorEvents: provenPrior.length,
      provenPriorBuys: buys.length,
      provenPriorSells: sells.length,
      provenPriorWallets: new Set(provenPrior.map((event) => event.wallet)).size,
      provenPriorBuyVolumeSol: buys.reduce((sum, event) => sum + event.sol, 0),
      priorSameSlotEvents: orderedSameSecondFlow.filter(
        (event) => event.relation === "prior_same_slot"
      ).length,
      afterSameSlotEvents: orderedSameSecondFlow.filter(
        (event) => event.relation === "after_same_slot"
      ).length,
      unknownOrderEvents: orderedSameSecondFlow.filter((event) =>
        [
          "unknown_slot",
          "signature_not_in_block",
          "entry_not_in_block",
        ].includes(event.relation)
      ).length,
      medianSameSlotIndexLead: median(sameSlotLeads),
      profilePasses: profileEvaluation.passes,
      bestProfileScores: profileEvaluation.scores,
      missedGates: profileEvaluation.missedGates,
      orderedSameSecondFlow,
    });
    console.log(
      `[${index + 1}/${input.rows.length}] ${source.ledger.mint.slice(0, 8)} ` +
        `outcome=${source.outcome} prior=${provenPrior.length} ` +
        `sameSlotBefore=${rows.at(-1)?.priorSameSlotEvents} ` +
        `sameSlotAfter=${rows.at(-1)?.afterSameSlotEvents} unknown=${rows.at(-1)?.unknownOrderEvents}`
    );
  }

  const outcomeGroups = Object.fromEntries(
    (["loss", "small_win", "scalp_win", "runner"] as const).map((outcome) => {
      const group = rows.filter((row) => row.outcome === outcome);
      const withPrior = group.filter((row) => row.provenPriorEvents > 0);
      return [
        outcome,
        {
          n: group.length,
          withProvenPrior: withPrior.length,
          medianPriorEvents: median(withPrior.map((row) => row.provenPriorEvents)),
          medianPriorBuys: median(withPrior.map((row) => row.provenPriorBuys)),
          medianPriorWallets: median(withPrior.map((row) => row.provenPriorWallets)),
          medianPriorBuyVolumeSol: median(
            withPrior.map((row) => row.provenPriorBuyVolumeSol)
          ),
          medianSameSlotIndexLead: median(
            withPrior.map((row) => row.medianSameSlotIndexLead ?? NaN)
          ),
        },
      ];
    })
  );
  const profileResults = profiles.map((profile) => {
    const comparable = rows.filter((row) => row.provenPriorBuys > 0);
    const passed = comparable.filter((row) => row.profilePasses[profile.name]);
    const rois = passed.map((row) => row.ledger.sourceExitRoiPct);
    const winners = comparable.filter((row) => row.ledger.sourceExitRoiPct > 0);
    const losers = comparable.filter((row) => row.ledger.sourceExitRoiPct < 0);
    const gateCounts = new Map<string, number>();
    for (const row of winners.filter((item) => !item.profilePasses[profile.name])) {
      for (const gate of row.missedGates[profile.name] ?? ["no_prior_buy"]) {
        gateCounts.set(gate, (gateCounts.get(gate) ?? 0) + 1);
      }
    }
    return {
      profile: profile.name,
      comparable: comparable.length,
      passed: passed.length,
      sourceWinRatePct: percentage(
        passed.filter((row) => row.ledger.sourceExitRoiPct > 0).length,
        passed.length
      ),
      sourceMedianRoiPct: median(rois),
      sourceExitProfitFactor: profitFactor(rois),
      winnerCapturePct: percentage(
        passed.filter((row) => row.ledger.sourceExitRoiPct > 0).length,
        winners.length
      ),
      runnerCapturePct: percentage(
        passed.filter((row) => row.ledger.sourceExitRoiPct >= 50).length,
        comparable.filter((row) => row.ledger.sourceExitRoiPct >= 50).length
      ),
      loserRejectionPct: percentage(
        losers.filter((row) => !row.profilePasses[profile.name]).length,
        losers.length
      ),
      missedWinnerGates: [...gateCounts.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([gate, count]) => ({ gate, count })),
    };
  });
  const orderedRows = rows.filter((row) => row.entryTransactionIndex != null);
  const priorWinnerRows = rows.filter(
    (row) => row.provenPriorEvents > 0 && row.ledger.sourceExitRoiPct > 0
  ).length;
  const priorLoserRows = rows.filter(
    (row) => row.provenPriorEvents > 0 && row.ledger.sourceExitRoiPct < 0
  ).length;
  const validityWarnings: string[] = [];
  if (orderedRows.length < 80) {
    validityWarnings.push(`only ${orderedRows.length} entry blocks were ordered`);
  }
  if (priorWinnerRows < 10) {
    validityWarnings.push(`only ${priorWinnerRows} winners have proven-prior flow`);
  }
  if (priorLoserRows < 10) {
    validityWarnings.push(`only ${priorLoserRows} losses have proven-prior flow`);
  }
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: input.wallet,
    inputPath,
    methodology: {
      note:
        "Same-second transactions are classified by slot and by their transaction index in the finalized block. Only lower slots or lower same-slot transaction indexes are treated as causal pre-entry evidence.",
      precursorScoring: false,
      outcomeWarning:
        "Profile outcomes use the source wallet's realized ROI and do not simulate EdgeSniffer exits.",
    },
    coverage: {
      rows: rows.length,
      distinctBlocksFetched: blockCache.size,
      entryBlocksOrdered: orderedRows.length,
      rowsWithProvenPriorFlow: rows.filter((row) => row.provenPriorEvents > 0).length,
      rowsWithProvenPriorBuys: rows.filter((row) => row.provenPriorBuys > 0).length,
      priorSameSlotEvents: rows.reduce((sum, row) => sum + row.priorSameSlotEvents, 0),
      afterSameSlotEvents: rows.reduce((sum, row) => sum + row.afterSameSlotEvents, 0),
      unknownOrderEvents: rows.reduce((sum, row) => sum + row.unknownOrderEvents, 0),
    },
    validity: {
      eligible: validityWarnings.length === 0,
      priorWinnerRows,
      priorLoserRows,
      warnings: validityWarnings,
    },
    outcomeGroups,
    profileResults,
    rows,
  };
  const dataDir = path.resolve(process.cwd(), "data");
  await mkdir(dataDir, { recursive: true });
  const jsonPath = path.join(dataDir, `${outputTag}.json`);
  const mdPath = path.join(dataDir, `${outputTag}.md`);
  await writeFile(jsonPath, JSON.stringify(output, null, 2), "utf8");
  const groups = outcomeGroups as Record<
    string,
    {
      n: number;
      withProvenPrior: number;
      medianPriorEvents: number | null;
      medianPriorBuys: number | null;
      medianPriorWallets: number | null;
      medianPriorBuyVolumeSol: number | null;
      medianSameSlotIndexLead: number | null;
    }
  >;
  const markdown = [
    "# Wallet Same-Slot Order Audit",
    "",
    `Generated: ${output.generatedAt}`,
    `Wallet: \`${output.wallet}\``,
    "",
    "## Coverage",
    "",
    `- Campaign rows: ${output.coverage.rows}`,
    `- Distinct entry blocks fetched: ${output.coverage.distinctBlocksFetched}`,
    `- Entry blocks ordered: ${output.coverage.entryBlocksOrdered}`,
    `- Rows with proven-prior flow: ${output.coverage.rowsWithProvenPriorFlow}`,
    `- Rows with proven-prior buys: ${output.coverage.rowsWithProvenPriorBuys}`,
    `- Same-slot events before wallet entry: ${output.coverage.priorSameSlotEvents}`,
    `- Same-slot events after wallet entry: ${output.coverage.afterSameSlotEvents}`,
    `- Same-second events with unknown order: ${output.coverage.unknownOrderEvents}`,
    `- Profile comparison valid: ${output.validity.eligible}`,
    ...output.validity.warnings.map((warning) => `- Validation warning: ${warning}`),
    "",
    "## Proven Pre-Entry Flow By Outcome",
    "",
    "| Outcome | n | with prior flow | prior events | prior buys | prior wallets | prior buy SOL | same-slot tx-index lead |",
    "|---|---:|---:|---:|---:|---:|---:|---:|",
    ...["loss", "small_win", "scalp_win", "runner"].map((name) => {
      const row = groups[name]!;
      return `| ${name} | ${row.n} | ${row.withProvenPrior} | ${row.medianPriorEvents?.toFixed(1) ?? "-"} | ${row.medianPriorBuys?.toFixed(1) ?? "-"} | ${row.medianPriorWallets?.toFixed(1) ?? "-"} | ${row.medianPriorBuyVolumeSol?.toFixed(2) ?? "-"} | ${row.medianSameSlotIndexLead?.toFixed(1) ?? "-"} |`;
    }),
    "",
    "## EdgeSniffer Profile Selection Replay",
    "",
    `Validity: **${output.validity.eligible ? "eligible for diagnostic comparison" : "invalid due to coverage bias"}**`,
    "",
    "Only transactions proven to precede the wallet entry are used. Source PF is not an EdgeSniffer exit simulation.",
    "",
    "| Profile | comparable | passed | source win | source median ROI | source PF | winner capture | runner capture | loser rejection |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...profileResults.map(
      (row) =>
        `| ${row.profile} | ${row.comparable} | ${row.passed} | ${row.sourceWinRatePct.toFixed(1)}% | ${fmtPct(row.sourceMedianRoiPct)} | ${row.sourceExitProfitFactor?.toFixed(2) ?? "-"} | ${row.winnerCapturePct.toFixed(1)}% | ${row.runnerCapturePct.toFixed(1)}% | ${row.loserRejectionPct.toFixed(1)}% |`
    ),
    "",
    "## Missed Winner Gates",
    "",
    ...profileResults.flatMap((row) => [
      `### ${row.profile}`,
      "",
      ...(row.missedWinnerGates.length
        ? row.missedWinnerGates.map((item) => `- ${item.gate}: ${item.count}`)
        : ["- None in comparable sample"]),
      "",
    ]),
    "## Limits",
    "",
    "- Block order establishes causal transaction order, not the millisecond lead available to a live detector.",
    "- Current precursor membership is excluded to avoid historical look-ahead.",
    "- This accepted-wallet cohort still needs contemporaneous market negatives to measure false positives.",
    "- Detection delay, freshness, confirmation, exposure, exits, fees, impact, and failed transactions require a separate executable replay.",
    "",
  ].join("\n");
  await writeFile(mdPath, `${markdown}\n`, "utf8");
  console.log(
    JSON.stringify(
      {
        jsonPath,
        mdPath,
        coverage: output.coverage,
        validity: output.validity,
        profileSummary: profileResults,
      },
      null,
      2
    )
  );
}

async function main(): Promise<void> {
  const inputPath = path.resolve(process.argv[2] ?? "");
  const edgeRoot = path.resolve(process.argv[3] ?? "");
  const outputTag = process.argv[4] ?? "wallet-slot-order";
  if (!process.argv[2] || !process.argv[3]) {
    throw new Error(
      "usage: tsx src/walletSlotOrderAudit.ts HISTORICAL.json EDGE_ROOT [outputTag]"
    );
  }
  await run(inputPath, edgeRoot, outputTag);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  });
}
