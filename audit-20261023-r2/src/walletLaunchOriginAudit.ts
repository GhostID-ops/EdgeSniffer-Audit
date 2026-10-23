import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import type {
  FlowEvent,
  OriginTransaction,
} from "./walletHistoricalPreEntryAudit.js";
import {
  classifySlotEvent,
  fetchBlockSignatures,
  type SlotRelation,
} from "./walletSlotOrderAudit.js";

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
  originTransactions?: OriginTransaction[];
};

type HistoricalReport = {
  wallet: string;
  rows: HistoricalRow[];
};

type OrderedOrigin = OriginTransaction & {
  relation: SlotRelation;
  transactionIndex: number | null;
  indexLead: number | null;
};

type OriginRow = {
  ledger: LedgerRow;
  outcome: HistoricalRow["outcome"];
  entrySlot: number | null;
  entryTransactionIndex: number | null;
  rawPriorTransactions: number;
  rawAfterTransactions: number;
  priorSwapEvents: number;
  entryType: string | null;
  entrySource: string | null;
  entryIsCreation: boolean;
  entryFeePayer: string | null;
  oldestRetainedOriginSecondsLead: number | null;
  creationSignature: string | null;
  creationType: string | null;
  creationSource: string | null;
  creationSlot: number | null;
  creationRelation: SlotRelation | null;
  creationIndexLead: number | null;
  creationSecondsLead: number | null;
  creationSlotsLead: number | null;
  creator: string | null;
  creatorIsTargetWallet: boolean;
  creatorFundedTargetWallet: boolean;
  immediatePredecessorSignature: string | null;
  immediatePredecessorType: string | null;
  immediatePredecessorSource: string | null;
  immediatePredecessorFeePayer: string | null;
  immediatePredecessorIndexLead: number | null;
  orderedOrigins: OrderedOrigin[];
};

const SLOT_CACHE = path.resolve(process.cwd(), "data", "slot-order-cache");

async function readJson<T>(file: string): Promise<T | null> {
  try {
    return JSON.parse((await readFile(file, "utf8")).replace(/^\uFEFF/, "")) as T;
  } catch {
    return null;
  }
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

function fmtPct(value: number | null): string {
  return value == null ? "-" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

export function isCreationTransaction(tx: OriginTransaction): boolean {
  return (
    /CREATE|TOKEN_MINT|CREATE_POOL/i.test(tx.type ?? "") ||
    /\bcreat(?:e|ed|es|ing)\b.*\b(?:coin|mint|pool|token)\b/i.test(
      tx.description ?? ""
    )
  );
}

function isPrior(relation: SlotRelation): boolean {
  return relation === "prior_slot" || relation === "prior_same_slot";
}

function isAfter(relation: SlotRelation): boolean {
  return relation === "after_slot" || relation === "after_same_slot";
}

function compareOrdered(a: OrderedOrigin, b: OrderedOrigin): number {
  const slotA = a.slot ?? Number.MAX_SAFE_INTEGER;
  const slotB = b.slot ?? Number.MAX_SAFE_INTEGER;
  if (slotA !== slotB) return slotA - slotB;
  const indexA = a.transactionIndex ?? Number.MAX_SAFE_INTEGER;
  const indexB = b.transactionIndex ?? Number.MAX_SAFE_INTEGER;
  if (indexA !== indexB) return indexA - indexB;
  return (a.timestamp ?? 0) - (b.timestamp ?? 0);
}

function aggregateIdentities(
  rows: OriginRow[],
  selector: (row: OriginRow) => string | null
) {
  const map = new Map<
    string,
    {
      identity: string;
      appearances: number;
      wins: number;
      losses: number;
      runners: number;
      rois: number[];
      examples: string[];
    }
  >();
  for (const row of rows) {
    const identity = selector(row);
    if (!identity) continue;
    const current = map.get(identity) ?? {
      identity,
      appearances: 0,
      wins: 0,
      losses: 0,
      runners: 0,
      rois: [],
      examples: [],
    };
    current.appearances += 1;
    current.wins += row.ledger.sourceExitRoiPct > 0 ? 1 : 0;
    current.losses += row.ledger.sourceExitRoiPct < 0 ? 1 : 0;
    current.runners += row.ledger.sourceExitRoiPct >= 50 ? 1 : 0;
    current.rois.push(row.ledger.sourceExitRoiPct);
    if (current.examples.length < 5) current.examples.push(row.ledger.mint);
    map.set(identity, current);
  }
  return [...map.values()]
    .map((row) => ({
      ...row,
      winRatePct: percentage(row.wins, row.appearances),
      runnerRatePct: percentage(row.runners, row.appearances),
      medianRoiPct: median(row.rois),
    }))
    .sort(
      (a, b) =>
        b.appearances - a.appearances ||
        b.winRatePct - a.winRatePct ||
        (b.medianRoiPct ?? -Infinity) - (a.medianRoiPct ?? -Infinity)
    );
}

async function run(inputPath: string, outputTag: string): Promise<void> {
  const input = await readJson<HistoricalReport>(inputPath);
  if (!input?.rows?.length) throw new Error("Historical report has no rows");
  if (!input.rows.some((row) => Array.isArray(row.originTransactions))) {
    throw new Error(
      "Historical report lacks origin transactions. Pull latest code and rerun audit:wallet-history."
    );
  }
  const rows: OriginRow[] = [];
  let missingBlockCaches = 0;

  for (const source of input.rows) {
    let signatures: string[] = [];
    if (source.entrySlot != null) {
      const cache = await readJson<{ signatures?: string[] }>(
        path.join(SLOT_CACHE, `${source.entrySlot}.json`)
      );
      signatures = cache?.signatures ?? [];
      if (!signatures.length && (source.originTransactions?.length ?? 0) > 0) {
        try {
          signatures = await fetchBlockSignatures(source.entrySlot);
        } catch {
          missingBlockCaches += 1;
        }
      }
    }
    const orderedOrigins = (source.originTransactions ?? [])
      .map(
        (tx): OrderedOrigin => ({
          ...tx,
          ...classifySlotEvent(
            tx,
            source.entrySlot,
            source.ledger.entrySignature,
            signatures
          ),
        })
      )
      .sort(compareOrdered);
    const priorOrigins = orderedOrigins.filter(
      (tx) =>
        tx.signature !== source.ledger.entrySignature && isPrior(tx.relation)
    );
    const afterOrigins = orderedOrigins.filter(
      (tx) =>
        tx.signature !== source.ledger.entrySignature && isAfter(tx.relation)
    );
    const explicitCreation =
      priorOrigins.find(isCreationTransaction) ?? null;
    const creation = explicitCreation;
    const entryOrigin =
      orderedOrigins.find(
        (tx) => tx.signature === source.ledger.entrySignature
      ) ?? null;
    const retainedTimestamps = orderedOrigins
      .map((tx) => tx.timestamp)
      .filter((timestamp): timestamp is number => timestamp != null);
    const oldestRetainedOriginSecondsLead = retainedTimestamps.length
      ? source.ledger.entryTs - Math.min(...retainedTimestamps)
      : null;
    const immediatePredecessor =
      priorOrigins
        .filter((tx) => tx.relation === "prior_same_slot")
        .at(-1) ?? null;
    const strictSwaps = source.strictFlow ?? [];
    const orderedSameSecondSwaps = (source.sameSecondFlow ?? []).filter((event) =>
      isPrior(
        classifySlotEvent(
          event,
          source.entrySlot,
          source.ledger.entrySignature,
          signatures
        ).relation
      )
    );
    const creator = creation?.feePayer ?? null;
    const entryTransactionIndex =
      source.ledger.entrySignature && signatures.length
        ? signatures.indexOf(source.ledger.entrySignature)
        : -1;
    rows.push({
      ledger: source.ledger,
      outcome: source.outcome,
      entrySlot: source.entrySlot,
      entryTransactionIndex:
        entryTransactionIndex >= 0 ? entryTransactionIndex : null,
      rawPriorTransactions: priorOrigins.length,
      rawAfterTransactions: afterOrigins.length,
      priorSwapEvents: strictSwaps.length + orderedSameSecondSwaps.length,
      entryType: entryOrigin?.type ?? null,
      entrySource: entryOrigin?.source ?? null,
      entryIsCreation: entryOrigin
        ? isCreationTransaction(entryOrigin)
        : false,
      entryFeePayer: entryOrigin?.feePayer ?? null,
      oldestRetainedOriginSecondsLead,
      creationSignature: creation?.signature ?? null,
      creationType: creation?.type ?? null,
      creationSource: creation?.source ?? null,
      creationSlot: creation?.slot ?? null,
      creationRelation: creation?.relation ?? null,
      creationIndexLead: creation?.indexLead ?? null,
      creationSecondsLead:
        creation?.timestamp != null
          ? source.ledger.entryTs - creation.timestamp
          : null,
      creationSlotsLead:
        creation?.slot != null && source.entrySlot != null
          ? source.entrySlot - creation.slot
          : null,
      creator,
      creatorIsTargetWallet: creator === input.wallet,
      creatorFundedTargetWallet: Boolean(
        creation?.nativeTransfers.find(
          (transfer) =>
            transfer.to === input.wallet &&
            transfer.from != null &&
            transfer.from !== input.wallet &&
            transfer.amountLamports > 0
        )
      ),
      immediatePredecessorSignature: immediatePredecessor?.signature ?? null,
      immediatePredecessorType: immediatePredecessor?.type ?? null,
      immediatePredecessorSource: immediatePredecessor?.source ?? null,
      immediatePredecessorFeePayer: immediatePredecessor?.feePayer ?? null,
      immediatePredecessorIndexLead: immediatePredecessor?.indexLead ?? null,
      orderedOrigins,
    });
  }

  const groups = Object.fromEntries(
    (["loss", "small_win", "scalp_win", "runner"] as const).map((outcome) => {
      const group = rows.filter((row) => row.outcome === outcome);
      const withCreation = group.filter((row) => row.creationSignature);
      return [
        outcome,
        {
          n: group.length,
          withCreation: withCreation.length,
          creationSameSlot: withCreation.filter(
            (row) => row.creationRelation === "prior_same_slot"
          ).length,
          creationImmediatelyBefore: withCreation.filter(
            (row) => row.creationIndexLead === 1
          ).length,
          entryIsCreation: group.filter((row) => row.entryIsCreation).length,
          entryCreationPaidByTarget: group.filter(
            (row) =>
              row.entryIsCreation && row.entryFeePayer === input.wallet
          ).length,
          creationUnresolved: group.filter(
            (row) => !row.creationSignature && !row.entryIsCreation
          ).length,
          medianOldestRetainedOriginSecondsLead: median(
            group.map((row) => row.oldestRetainedOriginSecondsLead ?? NaN)
          ),
          medianCreationSecondsLead: median(
            withCreation.map((row) => row.creationSecondsLead ?? NaN)
          ),
          medianCreationSlotsLead: median(
            withCreation.map((row) => row.creationSlotsLead ?? NaN)
          ),
          medianCreationIndexLead: median(
            withCreation.map((row) => row.creationIndexLead ?? NaN)
          ),
          creatorFundedTarget: withCreation.filter(
            (row) => row.creatorFundedTargetWallet
          ).length,
          medianRawPriorTransactions: median(
            group.map((row) => row.rawPriorTransactions)
          ),
        },
      ];
    })
  );
  const creators = aggregateIdentities(rows, (row) => row.creator);
  const predecessorPayers = aggregateIdentities(
    rows,
    (row) => row.immediatePredecessorFeePayer
  );
  const explicitCreations = rows.filter((row) => row.creationSignature);
  const winnerCreations = explicitCreations.filter(
    (row) => row.ledger.sourceExitRoiPct > 0
  ).length;
  const loserCreations = explicitCreations.filter(
    (row) => row.ledger.sourceExitRoiPct < 0
  ).length;
  const warnings: string[] = [];
  if (missingBlockCaches > 0) {
    warnings.push(`${missingBlockCaches} rows are missing slot-order block caches`);
  }
  if (winnerCreations < 10) {
    warnings.push(`only ${winnerCreations} winners have an explicit prior creation transaction`);
  }
  if (loserCreations < 10) {
    warnings.push(`only ${loserCreations} losses have an explicit prior creation transaction`);
  }
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: input.wallet,
    inputPath,
    methodology: {
      note:
        "Creation and predecessor transactions are taken from the mint history and ordered against the target entry with finalized block transaction indexes.",
      creatorRule:
        "Creator is the fee payer of an explicit CREATE, CREATE_POOL, TOKEN_MINT, or creation-description transaction proven to precede entry.",
    },
    coverage: {
      rows: rows.length,
      rowsWithOriginTransactions: rows.filter(
        (row) => row.orderedOrigins.length > 0
      ).length,
      explicitPriorCreations: explicitCreations.length,
      priorSameSlotCreations: explicitCreations.filter(
        (row) => row.creationRelation === "prior_same_slot"
      ).length,
      creationsImmediatelyBeforeEntry: explicitCreations.filter(
        (row) => row.creationIndexLead === 1
      ).length,
      entriesContainingCreation: rows.filter((row) => row.entryIsCreation).length,
      entryCreationsPaidByTarget: rows.filter(
        (row) => row.entryIsCreation && row.entryFeePayer === input.wallet
      ).length,
      creationUnresolved: rows.filter(
        (row) => !row.creationSignature && !row.entryIsCreation
      ).length,
      creatorFundedTarget: explicitCreations.filter(
        (row) => row.creatorFundedTargetWallet
      ).length,
      missingBlockCaches,
    },
    validity: {
      eligible: warnings.length === 0,
      winnerCreations,
      loserCreations,
      warnings,
    },
    groups,
    repeatedCreators: creators.filter((row) => row.appearances >= 2),
    repeatedPredecessorFeePayers: predecessorPayers.filter(
      (row) => row.appearances >= 2
    ),
    rows,
  };
  const dataDir = path.resolve(process.cwd(), "data");
  await mkdir(dataDir, { recursive: true });
  const jsonPath = path.join(dataDir, `${outputTag}.json`);
  const mdPath = path.join(dataDir, `${outputTag}.md`);
  await writeFile(jsonPath, JSON.stringify(output, null, 2), "utf8");
  const groupRows = groups as Record<
    string,
    {
      n: number;
      withCreation: number;
      creationSameSlot: number;
      creationImmediatelyBefore: number;
      entryIsCreation: number;
      entryCreationPaidByTarget: number;
      creationUnresolved: number;
      medianOldestRetainedOriginSecondsLead: number | null;
      medianCreationSecondsLead: number | null;
      medianCreationSlotsLead: number | null;
      medianCreationIndexLead: number | null;
      creatorFundedTarget: number;
      medianRawPriorTransactions: number | null;
    }
  >;
  const identityRows = (
    values: ReturnType<typeof aggregateIdentities>
  ): string[] =>
    values.slice(0, 25).map(
      (row) =>
        `| \`${row.identity}\` | ${row.appearances} | ${row.wins}/${row.losses} | ${row.runners} | ${row.winRatePct.toFixed(1)}% | ${fmtPct(row.medianRoiPct)} | ${row.examples.map((mint) => mint.slice(0, 8)).join(", ")} |`
    );
  const markdown = [
    "# Wallet Launch-Origin Audit",
    "",
    `Generated: ${output.generatedAt}`,
    `Wallet: \`${output.wallet}\``,
    "",
    "## Coverage",
    "",
    `- Campaign rows: ${output.coverage.rows}`,
    `- Rows with origin transactions: ${output.coverage.rowsWithOriginTransactions}`,
    `- Explicit prior creation transactions: ${output.coverage.explicitPriorCreations}`,
    `- Prior creations in the same slot: ${output.coverage.priorSameSlotCreations}`,
    `- Creation immediately before target entry: ${output.coverage.creationsImmediatelyBeforeEntry}`,
    `- Target entries containing creation: ${output.coverage.entriesContainingCreation}`,
    `- Entry creations fee-paid by target wallet: ${output.coverage.entryCreationsPaidByTarget}`,
    `- Creation unresolved inside retained history: ${output.coverage.creationUnresolved}`,
    `- Creation transaction funded target wallet: ${output.coverage.creatorFundedTarget}`,
    `- Missing slot-order block caches: ${output.coverage.missingBlockCaches}`,
    `- Comparison valid: ${output.validity.eligible}`,
    ...output.validity.warnings.map((warning) => `- Validation warning: ${warning}`),
    "",
    "## Launch Timing By Outcome",
    "",
    "| Outcome | n | prior creation | entry creates | target fee payer | unresolved | same-slot creation | immediately before | median seconds lead | median retained age | median slots lead | median tx-index lead | creator funded target | median raw prior txs |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...["loss", "small_win", "scalp_win", "runner"].map((name) => {
      const row = groupRows[name]!;
      return `| ${name} | ${row.n} | ${row.withCreation} | ${row.entryIsCreation} | ${row.entryCreationPaidByTarget} | ${row.creationUnresolved} | ${row.creationSameSlot} | ${row.creationImmediatelyBefore} | ${row.medianCreationSecondsLead?.toFixed(1) ?? "-"} | ${row.medianOldestRetainedOriginSecondsLead?.toFixed(1) ?? "-"} | ${row.medianCreationSlotsLead?.toFixed(1) ?? "-"} | ${row.medianCreationIndexLead?.toFixed(1) ?? "-"} | ${row.creatorFundedTarget} | ${row.medianRawPriorTransactions?.toFixed(1) ?? "-"} |`;
    }),
    "",
    "## Repeated Creators",
    "",
    "| Identity | Seen | W/L | Runners | Win | Median ROI | Examples |",
    "|---|---:|---:|---:|---:|---:|---|",
    ...identityRows(output.repeatedCreators),
    ...(output.repeatedCreators.length ? [] : ["| - | 0 | - | 0 | - | - | - |"]),
    "",
    "## Repeated Immediate-Predecessor Fee Payers",
    "",
    "| Identity | Seen | W/L | Runners | Win | Median ROI | Examples |",
    "|---|---:|---:|---:|---:|---:|---|",
    ...identityRows(output.repeatedPredecessorFeePayers),
    ...(output.repeatedPredecessorFeePayers.length
      ? []
      : ["| - | 0 | - | 0 | - | - | - |"]),
    "",
    "## Interpretation Limits",
    "",
    "- A prior creation transaction is observable by a stream-first detector, but block order does not reveal the millisecond reaction budget.",
    "- A repeated creator or predecessor is a candidate feature, not proof of intent or future profitability.",
    "- One wallet's accepted tokens cannot measure market-wide false positives.",
    "- Entry selection still requires forward validation, market negatives, and executable exit simulation.",
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
        repeatedCreators: output.repeatedCreators.slice(0, 10),
        repeatedPredecessorFeePayers:
          output.repeatedPredecessorFeePayers.slice(0, 10),
      },
      null,
      2
    )
  );
}

async function main(): Promise<void> {
  const inputPath = path.resolve(process.argv[2] ?? "");
  const outputTag = process.argv[3] ?? "wallet-launch-origin";
  if (!process.argv[2]) {
    throw new Error(
      "usage: tsx src/walletLaunchOriginAudit.ts HISTORICAL.json [outputTag]"
    );
  }
  await run(inputPath, outputTag);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  });
}
