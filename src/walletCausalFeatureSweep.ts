import { createReadStream } from "node:fs";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createInterface } from "node:readline";
import {
  attachWalkForwardIdentity,
  buildProfiles,
  campaignKey,
  outcomeFromRoi,
  profilePasses,
  summarizeWeighted,
  type CausalFeatureRow,
  type CausalProfile,
  type Outcome,
} from "./walletCausalResearch.js";

type LedgerRow = {
  mint: string;
  entryTs: number;
  sourceExitRoiPct: number;
};

type SlotRow = {
  ledger: LedgerRow;
  provenPriorBuys: number;
  provenPriorWallets: number;
  provenPriorBuyVolumeSol: number;
  priorSameSlotEvents: number;
  medianSameSlotIndexLead: number | null;
};

type OriginRow = {
  ledger: LedgerRow;
  creationSignature: string | null;
  creationRelation: string | null;
  creator: string | null;
  immediatePredecessorFeePayer: string | null;
};

type LedgerReport = {
  wallet: string;
  rows: LedgerRow[];
};

type SlotReport = {
  wallet: string;
  rows: SlotRow[];
};

type OriginReport = {
  wallet: string;
  rows: OriginRow[];
};

type EdgeObservation = {
  mint: string;
  timestampMs: number;
  marketCapUsd: number | null;
  buyCount: number | null;
  wallets: number | null;
  buyVolumeSol: number | null;
  mfe120Pct: number | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function numberAt(
  value: unknown,
  names: string[],
  depth = 0
): number | null {
  if (!isRecord(value) || depth > 4) return null;
  for (const name of names) {
    const raw = value[name];
    const parsed = typeof raw === "number" ? raw : Number(raw);
    if (Number.isFinite(parsed)) return parsed;
  }
  for (const child of ["signal", "event", "position", "pending", "context"]) {
    const parsed = numberAt(value[child], names, depth + 1);
    if (parsed != null) return parsed;
  }
  return null;
}

function stringAt(
  value: unknown,
  names: string[],
  depth = 0
): string | null {
  if (!isRecord(value) || depth > 4) return null;
  for (const name of names) {
    const raw = value[name];
    if (typeof raw === "string" && raw) return raw;
  }
  for (const child of ["signal", "event", "position", "pending", "context"]) {
    const parsed = stringAt(value[child], names, depth + 1);
    if (parsed) return parsed;
  }
  return null;
}

function timestampMs(value: unknown): number | null {
  const raw = numberAt(value, ["timestamp", "ts"]);
  if (raw != null) return raw < 10_000_000_000 ? raw * 1000 : raw;
  const text = stringAt(value, ["createdAt", "at", "isoTs"]);
  const parsed = text ? Date.parse(text) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeEdgeObservation(value: unknown): EdgeObservation | null {
  const mint = stringAt(value, ["mint", "tokenMint", "outputMint"]);
  const time = timestampMs(value);
  if (!mint || time == null) return null;
  return {
    mint,
    timestampMs: time,
    marketCapUsd: numberAt(value, [
      "marketCapUsd",
      "inferredMarketCapUsd",
      "entryMarketCapUsd",
    ]),
    buyCount: numberAt(value, ["buyCount60", "buys60"]),
    wallets: numberAt(value, ["uniqueWallets60", "wallets60"]),
    buyVolumeSol: numberAt(value, [
      "volumeBuySol60",
      "buyVolumeSol60",
      "buyVolume60",
    ]),
    mfe120Pct: null,
  };
}

async function readJson<T>(file: string): Promise<T> {
  return JSON.parse((await readFile(file, "utf8")).replace(/^\uFEFF/, "")) as T;
}

async function findSignalFiles(root: string): Promise<string[]> {
  const files: string[] = [];
  async function walk(directory: string): Promise<void> {
    let entries;
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(target);
      else if (
        entry.isFile() &&
        ["launch-paper-signals.jsonl", "launch-paper-stream-events.jsonl"].includes(
          entry.name
        )
      ) {
        files.push(target);
      }
    }
  }
  await walk(path.join(root, "data"));
  return files.sort();
}

async function loadMarketControls(
  edgeRoot: string,
  startMs: number,
  endMs: number,
  targetMints: Set<string>
): Promise<{
  files: string[];
  lines: number;
  observations: EdgeObservation[];
  overlapStartMs: number | null;
  overlapEndMs: number | null;
}> {
  const files = await findSignalFiles(edgeRoot);
  const byMint = new Map<string, EdgeObservation>();
  let lines = 0;
  let overlapStartMs: number | null = null;
  let overlapEndMs: number | null = null;
  for (const file of files) {
    const reader = createInterface({
      input: createReadStream(file, { encoding: "utf8" }),
      crlfDelay: Infinity,
    });
    for await (const line of reader) {
      lines += 1;
      if (!line.trim()) continue;
      try {
        const observation = normalizeEdgeObservation(
          JSON.parse(line.replace(/^\uFEFF/, ""))
        );
        if (!observation) continue;
        overlapStartMs =
          overlapStartMs == null
            ? observation.timestampMs
            : Math.min(overlapStartMs, observation.timestampMs);
        overlapEndMs =
          overlapEndMs == null
            ? observation.timestampMs
            : Math.max(overlapEndMs, observation.timestampMs);
        if (
          observation.timestampMs < startMs ||
          observation.timestampMs > endMs ||
          targetMints.has(observation.mint)
        ) {
          continue;
        }
        const current = byMint.get(observation.mint);
        if (!current || observation.timestampMs < current.timestampMs) {
          byMint.set(observation.mint, observation);
        }
      } catch {}
    }
  }
  const maxMcByMint = new Map<string, number>();
  for (const file of files) {
    const reader = createInterface({
      input: createReadStream(file, { encoding: "utf8" }),
      crlfDelay: Infinity,
    });
    for await (const line of reader) {
      if (!line.trim()) continue;
      try {
        const observation = normalizeEdgeObservation(
          JSON.parse(line.replace(/^\uFEFF/, ""))
        );
        if (!observation?.marketCapUsd) continue;
        const first = byMint.get(observation.mint);
        if (
          !first ||
          observation.timestampMs < first.timestampMs ||
          observation.timestampMs > first.timestampMs + 120_000
        ) {
          continue;
        }
        maxMcByMint.set(
          observation.mint,
          Math.max(
            maxMcByMint.get(observation.mint) ?? observation.marketCapUsd,
            observation.marketCapUsd
          )
        );
      } catch {}
    }
  }
  for (const observation of byMint.values()) {
    const maxMc = maxMcByMint.get(observation.mint);
    observation.mfe120Pct =
      maxMc != null && observation.marketCapUsd != null && observation.marketCapUsd > 0
        ? (maxMc / observation.marketCapUsd - 1) * 100
        : null;
  }
  return {
    files,
    lines,
    observations: [...byMint.values()],
    overlapStartMs,
    overlapEndMs,
  };
}

function populationCounts(rows: LedgerRow[]): Record<Outcome, number> {
  const output: Record<Outcome, number> = {
    loss: 0,
    small_win: 0,
    scalp_win: 0,
    runner: 0,
  };
  for (const row of rows) output[outcomeFromRoi(row.sourceExitRoiPct)] += 1;
  return output;
}

function joinRows(
  slot: SlotReport,
  origin: OriginReport
): CausalFeatureRow[] {
  const origins = new Map(
    origin.rows.map((row) => [
      campaignKey(row.ledger.mint, row.ledger.entryTs),
      row,
    ])
  );
  const base = slot.rows.map((row) => {
    const key = campaignKey(row.ledger.mint, row.ledger.entryTs);
    const originRow = origins.get(key);
    return {
      key,
      mint: row.ledger.mint,
      entryTs: row.ledger.entryTs,
      outcome: outcomeFromRoi(row.ledger.sourceExitRoiPct),
      roiPct: row.ledger.sourceExitRoiPct,
      priorBuys: row.provenPriorBuys,
      priorWallets: row.provenPriorWallets,
      priorBuyVolumeSol: row.provenPriorBuyVolumeSol,
      medianIndexLead: Math.max(0, row.medianSameSlotIndexLead ?? 0),
      priorSameSlotEvents: row.priorSameSlotEvents,
      creationKnown: Boolean(originRow?.creationSignature),
      creationSameSlot: originRow?.creationRelation === "prior_same_slot",
      creator: originRow?.creator ?? null,
      predecessor: originRow?.immediatePredecessorFeePayer ?? null,
    };
  });
  return attachWalkForwardIdentity(base);
}

function controlPasses(
  row: EdgeObservation,
  profile: CausalProfile
): boolean {
  if (
    profile.minIndexLead > 0 ||
    profile.creation !== "any" ||
    profile.identity !== "none"
  ) {
    return false;
  }
  return (
    (row.buyCount ?? -Infinity) >= profile.minBuys &&
    (row.wallets ?? -Infinity) >= profile.minWallets &&
    (row.buyVolumeSol ?? -Infinity) >= profile.minBuyVolumeSol
  );
}

function fmt(value: number | null, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "-" : value.toFixed(digits);
}

export async function runCausalSweep(
  ledgerPath: string,
  slotPath: string,
  originPath: string,
  edgeRoot: string,
  outputTag: string
): Promise<void> {
  const [ledger, slot, origin] = await Promise.all([
    readJson<LedgerReport>(ledgerPath),
    readJson<SlotReport>(slotPath),
    readJson<OriginReport>(originPath),
  ]);
  if (ledger.wallet !== slot.wallet || ledger.wallet !== origin.wallet) {
    throw new Error("Wallet mismatch across ledger, slot-order, and origin reports");
  }
  const rows = joinRows(slot, origin).filter((row) => row.priorBuys > 0);
  if (rows.length < 20) throw new Error("Too few proven-prior rows for a causal sweep");
  const splitIndex = Math.max(1, Math.floor(rows.length * 0.7));
  const train = rows.slice(0, splitIndex);
  const test = rows.slice(splitIndex);
  const populationByOutcome = populationCounts(ledger.rows);
  const profiles = buildProfiles();
  const evaluated = profiles
    .map((profile) => {
      const trainSelected = train.filter((row) => profilePasses(row, profile));
      const testSelected = test.filter((row) => profilePasses(row, profile));
      return {
        profile,
        train: summarizeWeighted(
          train,
          trainSelected,
          populationByOutcome
        ),
        test: summarizeWeighted(test, testSelected, populationByOutcome),
        selectedKeys: rows
          .filter((row) => profilePasses(row, profile))
          .map((row) => row.key),
      };
    })
    .filter(
      (row) =>
        row.train.rows >= 8 &&
        row.test.rows >= 3 &&
        row.train.equalBetPnlSol > 0 &&
        (row.train.profitFactor ?? Infinity) > 1.1
    )
    .sort(
      (a, b) =>
        b.train.equalBetPnlSol - a.train.equalBetPnlSol ||
        (b.train.profitFactor ?? Infinity) -
          (a.train.profitFactor ?? Infinity)
    );
  const top = evaluated.slice(0, 25);
  const startMs = Math.min(...ledger.rows.map((row) => row.entryTs)) * 1000;
  const endMs = Math.max(...ledger.rows.map((row) => row.entryTs)) * 1000;
  const controls = await loadMarketControls(
    edgeRoot,
    startMs,
    endMs,
    new Set(ledger.rows.map((row) => row.mint))
  );
  const projectionIds = new Set<string>();
  const controlComparableProfiles = top
    .map((row) => {
      const profile: CausalProfile = {
        ...row.profile,
        id: `${row.profile.id}__flow_projection`,
        minIndexLead: 0,
        creation: "any",
        identity: "none",
      };
      const projectionKey = [
        profile.minBuys,
        profile.minWallets,
        profile.minBuyVolumeSol,
      ].join(":");
      if (projectionIds.has(projectionKey)) return null;
      projectionIds.add(projectionKey);
      const trainSelected = train.filter((item) =>
        profilePasses(item, profile)
      );
      const testSelected = test.filter((item) =>
        profilePasses(item, profile)
      );
      return {
        profile,
        sourceProfileId: row.profile.id,
        droppedGates: [
          ...(row.profile.minIndexLead > 0 ? ["transaction_index_lead"] : []),
          ...(row.profile.creation !== "any" ? ["creation"] : []),
          ...(row.profile.identity !== "none" ? ["identity_reputation"] : []),
        ],
        train: summarizeWeighted(train, trainSelected, populationByOutcome),
        test: summarizeWeighted(test, testSelected, populationByOutcome),
      };
    })
    .filter((row): row is NonNullable<typeof row> => Boolean(row))
    .slice(0, 10);
  const controlRows = controlComparableProfiles
    .map((row) => ({
      profile: row.profile.id,
      sourceProfile: row.sourceProfileId,
      droppedGates: row.droppedGates,
      controlsEvaluated: controls.observations.length,
      controlsWithPath: controls.observations.filter(
        (control) => control.mfe120Pct != null
      ).length,
      controlsTriggered: controls.observations.filter((control) =>
        controlPasses(control, row.profile)
      ).length,
      negativeControls: controls.observations.filter(
        (control) => control.mfe120Pct != null && control.mfe120Pct < 10
      ).length,
      triggeredNegatives: controls.observations.filter(
        (control) =>
          control.mfe120Pct != null &&
          control.mfe120Pct < 10 &&
          controlPasses(control, row.profile)
      ).length,
      triggeredPathWins: controls.observations.filter(
        (control) =>
          control.mfe120Pct != null &&
          control.mfe120Pct >= 10 &&
          controlPasses(control, row.profile)
      ).length,
      triggeredRunners: controls.observations.filter(
        (control) =>
          control.mfe120Pct != null &&
          control.mfe120Pct >= 50 &&
          controlPasses(control, row.profile)
      ).length,
    }));
  const controlsWithPath = controls.observations.filter(
    (control) => control.mfe120Pct != null
  ).length;
  const controlValidity = {
    eligible:
      controls.observations.length >= 100 &&
      controlsWithPath >= 100 &&
      (controls.overlapStartMs ?? Infinity) <= startMs &&
      (controls.overlapEndMs ?? -Infinity) >= endMs,
    reason:
      controls.observations.length < 100
        ? "fewer than 100 non-wallet Edge candidates overlap the wallet window"
        : controlsWithPath < 100
          ? "fewer than 100 overlapping market controls have a 120-second path"
        : (controls.overlapStartMs ?? Infinity) > startMs ||
            (controls.overlapEndMs ?? -Infinity) < endMs
          ? "retained Edge data does not cover the full wallet window"
          : null,
    schema:
      "Edge 60-second context is a market-control proxy; transaction-index, creation, and identity gates are not applied to controls.",
  };
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: ledger.wallet,
    inputs: { ledgerPath, slotPath, originPath, edgeRoot },
    methodology: {
      split: "chronological 70/30 train/test",
      populationReweighting: populationByOutcome,
      identity:
        "Creator and predecessor reputation uses only earlier sampled campaigns.",
      projectedPnl:
        "Equal-bet PnL is population-projected after stratified-sample reweighting; it is not literal PnL from the displayed row count.",
      warning:
        "Source-wallet ROI is a selection label, not an executable EdgeSniffer exit.",
    },
    coverage: {
      provenPriorRows: rows.length,
      trainRows: train.length,
      testRows: test.length,
      profiles: profiles.length,
      viableProfiles: evaluated.length,
      marketControlFiles: controls.files,
      marketControlLines: controls.lines,
      marketControls: controls.observations.length,
      marketControlsWithPath: controlsWithPath,
      edgeStartIso: controls.overlapStartMs
        ? new Date(controls.overlapStartMs).toISOString()
        : null,
      edgeEndIso: controls.overlapEndMs
        ? new Date(controls.overlapEndMs).toISOString()
        : null,
      walletStartIso: new Date(startMs).toISOString(),
      walletEndIso: new Date(endMs).toISOString(),
    },
    controlValidity,
    topProfiles: top,
    controlComparableProfiles,
    marketControlSummary: controlRows,
    rows,
  };
  const dataDir = path.resolve(process.cwd(), "data");
  await mkdir(dataDir, { recursive: true });
  const jsonPath = path.join(dataDir, `${outputTag}.json`);
  const mdPath = path.join(dataDir, `${outputTag}.md`);
  await writeFile(jsonPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  const markdown = [
    "# Wallet Causal Feature Sweep",
    "",
    `Generated: ${output.generatedAt}`,
    `Wallet: \`${output.wallet}\``,
    "",
    "## Validity",
    "",
    `- Proven-prior rows: ${rows.length}`,
    `- Chronological train/test: ${train.length}/${test.length}`,
    `- Profiles evaluated: ${profiles.length}`,
    `- Viable train profiles: ${evaluated.length}`,
    `- Market controls: ${controls.observations.length}`,
    `- Market controls with 120-second paths: ${controlsWithPath}`,
    `- Market-control comparison valid: ${controlValidity.eligible}`,
    ...(controlValidity.reason ? [`- Control warning: ${controlValidity.reason}`] : []),
    `- Control schema: ${controlValidity.schema}`,
    "",
    "## Top Chronological Profiles",
    "",
    "| Profile | Train n | Train PF | Train projected PnL | Test n | Test PF | Test projected PnL | Test win | Test scalp capture | Test runner capture | Test loser rejection |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...top.map(
      (row) =>
        `| ${row.profile.id} | ${row.train.rows} | ${fmt(row.train.profitFactor)} | ${row.train.equalBetPnlSol.toFixed(3)} | ${row.test.rows} | ${fmt(row.test.profitFactor)} | ${row.test.equalBetPnlSol.toFixed(3)} | ${row.test.winRatePct.toFixed(1)}% | ${row.test.scalpCapturePct.toFixed(1)}% | ${row.test.runnerCapturePct.toFixed(1)}% | ${row.test.loserRejectionPct.toFixed(1)}% |`
    ),
    "",
    "## Market-Control Trigger Counts",
    "",
    "| Flow projection | Source profile | Dropped gates | Controls | With path | Triggered | Negatives | Triggered negatives | Triggered >=10% | Triggered runners |",
    "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ...controlRows.map(
      (row) =>
        `| ${row.profile} | ${row.sourceProfile} | ${row.droppedGates.join(", ") || "none"} | ${row.controlsEvaluated} | ${row.controlsWithPath} | ${row.controlsTriggered} | ${row.negativeControls} | ${row.triggeredNegatives} | ${row.triggeredPathWins} | ${row.triggeredRunners} |`
    ),
    ...(controlRows.length
      ? []
      : [
          "| - | - | - | 0 | 0 | 0 | 0 | 0 | 0 | 0 |",
          "",
          "No top profile used only fields shared with retained Edge controls.",
        ]),
    "",
    "## Interpretation",
    "",
    "- Profiles are selected on the chronological train partition and displayed on untouched later rows.",
    "- Population reweighting corrects the 30/30/30/30 stratified audit toward the full wallet outcome distribution.",
    "- Projected PnL estimates a full-population equal-bet result; it is not literal PnL from the displayed sample rows.",
    "- Identity history is walk-forward and does not use future campaign outcomes.",
    "- Market controls use an explicit flow-only projection and list every unsupported gate that was dropped.",
    "- Market controls must be valid before treating a profile as a detector.",
    "- A positive source-exit profile still requires transaction-lag, cost, and independent exit replay.",
    "",
  ].join("\n");
  await writeFile(mdPath, `${markdown}\n`, "utf8");
  console.log(
    JSON.stringify(
      {
        jsonPath,
        mdPath,
        coverage: output.coverage,
        controlValidity,
        top: top.slice(0, 5).map((row) => ({
          profile: row.profile.id,
          train: row.train,
          test: row.test,
        })),
      },
      null,
      2
    )
  );
}

async function main(): Promise<void> {
  const ledgerPath = path.resolve(process.argv[2] ?? "");
  const slotPath = path.resolve(process.argv[3] ?? "");
  const originPath = path.resolve(process.argv[4] ?? "");
  const edgeRoot = path.resolve(process.argv[5] ?? "");
  const outputTag = process.argv[6] ?? "wallet-causal-sweep";
  if (!process.argv[2] || !process.argv[3] || !process.argv[4] || !process.argv[5]) {
    throw new Error(
      "usage: tsx src/walletCausalFeatureSweep.ts LEDGER.json SLOT.json ORIGIN.json EDGE_ROOT [outputTag]"
    );
  }
  await runCausalSweep(ledgerPath, slotPath, originPath, edgeRoot, outputTag);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  });
}
