export type Outcome = "loss" | "small_win" | "scalp_win" | "runner";

export type CausalFeatureRow = {
  key: string;
  mint: string;
  entryTs: number;
  outcome: Outcome;
  roiPct: number;
  priorBuys: number;
  priorWallets: number;
  priorBuyVolumeSol: number;
  medianIndexLead: number;
  priorSameSlotEvents: number;
  creationKnown: boolean;
  creationSameSlot: boolean;
  creator: string | null;
  predecessor: string | null;
  creatorPriorAppearances: number;
  creatorPriorWinRatePct: number | null;
  predecessorPriorAppearances: number;
  predecessorPriorWinRatePct: number | null;
};

export type CausalProfile = {
  id: string;
  minBuys: number;
  minWallets: number;
  minBuyVolumeSol: number;
  minIndexLead: number;
  creation: "any" | "known" | "same_slot";
  identity: "none" | "creator_positive" | "predecessor_positive" | "either_positive";
};

export type WeightedMetrics = {
  rows: number;
  weightedRows: number;
  winners: number;
  winRatePct: number;
  medianRoiPct: number | null;
  avgRoiPct: number | null;
  profitFactor: number | null;
  equalBetPnlSol: number;
  scalpCapturePct: number;
  runnerCapturePct: number;
  loserRejectionPct: number;
};

type IdentityState = {
  appearances: number;
  wins: number;
};

export function campaignKey(mint: string, entryTs: number): string {
  return `${mint}:${entryTs}`;
}

export function outcomeFromRoi(roiPct: number): Outcome {
  if (roiPct < 0) return "loss";
  if (roiPct >= 50) return "runner";
  if (roiPct >= 10) return "scalp_win";
  return "small_win";
}

export function median(values: number[]): number | null {
  const rows = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!rows.length) return null;
  const middle = Math.floor(rows.length / 2);
  return rows.length % 2
    ? rows[middle]!
    : (rows[middle - 1]! + rows[middle]!) / 2;
}

export function profileId(profile: Omit<CausalProfile, "id">): string {
  const creation =
    profile.creation === "same_slot"
      ? "cs"
      : profile.creation === "known"
        ? "ck"
        : "ca";
  const identity =
    profile.identity === "creator_positive"
      ? "ic"
      : profile.identity === "predecessor_positive"
        ? "ip"
        : profile.identity === "either_positive"
          ? "ie"
          : "in";
  return [
    `b${profile.minBuys}`,
    `w${profile.minWallets}`,
    `v${profile.minBuyVolumeSol}`,
    `l${profile.minIndexLead}`,
    creation,
    identity,
  ].join("_");
}

function positiveIdentity(appearances: number, winRatePct: number | null): boolean {
  return appearances >= 2 && winRatePct != null && winRatePct >= 60;
}

export function profilePasses(
  row: CausalFeatureRow,
  profile: CausalProfile
): boolean {
  if (row.priorBuys < profile.minBuys) return false;
  if (row.priorWallets < profile.minWallets) return false;
  if (row.priorBuyVolumeSol < profile.minBuyVolumeSol) return false;
  if (row.medianIndexLead < profile.minIndexLead) return false;
  if (profile.creation === "known" && !row.creationKnown) return false;
  if (profile.creation === "same_slot" && !row.creationSameSlot) return false;
  const creatorPositive = positiveIdentity(
    row.creatorPriorAppearances,
    row.creatorPriorWinRatePct
  );
  const predecessorPositive = positiveIdentity(
    row.predecessorPriorAppearances,
    row.predecessorPriorWinRatePct
  );
  if (profile.identity === "creator_positive" && !creatorPositive) return false;
  if (
    profile.identity === "predecessor_positive" &&
    !predecessorPositive
  ) {
    return false;
  }
  if (
    profile.identity === "either_positive" &&
    !creatorPositive &&
    !predecessorPositive
  ) {
    return false;
  }
  return true;
}

export function buildProfiles(): CausalProfile[] {
  const output: CausalProfile[] = [];
  for (const minBuys of [1, 3, 4, 5, 6]) {
    for (const minWallets of [1, 3, 4, 5]) {
      for (const minBuyVolumeSol of [0, 5, 7.5, 10, 12.5]) {
        for (const minIndexLead of [0, 50, 100, 150, 200]) {
          for (const creation of ["any", "known", "same_slot"] as const) {
            for (const identity of [
              "none",
              "creator_positive",
              "predecessor_positive",
              "either_positive",
            ] as const) {
              const profile = {
                minBuys,
                minWallets,
                minBuyVolumeSol,
                minIndexLead,
                creation,
                identity,
              };
              output.push({ ...profile, id: profileId(profile) });
            }
          }
        }
      }
    }
  }
  return output;
}

export function attachWalkForwardIdentity(
  rows: Array<
    Omit<
      CausalFeatureRow,
      | "creatorPriorAppearances"
      | "creatorPriorWinRatePct"
      | "predecessorPriorAppearances"
      | "predecessorPriorWinRatePct"
    >
  >
): CausalFeatureRow[] {
  const creatorState = new Map<string, IdentityState>();
  const predecessorState = new Map<string, IdentityState>();
  const sorted = [...rows].sort(
    (a, b) => a.entryTs - b.entryTs || a.key.localeCompare(b.key)
  );
  return sorted.map((row) => {
    const creator = row.creator ? creatorState.get(row.creator) : undefined;
    const predecessor = row.predecessor
      ? predecessorState.get(row.predecessor)
      : undefined;
    const output: CausalFeatureRow = {
      ...row,
      creatorPriorAppearances: creator?.appearances ?? 0,
      creatorPriorWinRatePct: creator?.appearances
        ? (creator.wins / creator.appearances) * 100
        : null,
      predecessorPriorAppearances: predecessor?.appearances ?? 0,
      predecessorPriorWinRatePct: predecessor?.appearances
        ? (predecessor.wins / predecessor.appearances) * 100
        : null,
    };
    const won = row.roiPct > 0 ? 1 : 0;
    if (row.creator) {
      const current = creatorState.get(row.creator) ?? {
        appearances: 0,
        wins: 0,
      };
      creatorState.set(row.creator, {
        appearances: current.appearances + 1,
        wins: current.wins + won,
      });
    }
    if (row.predecessor) {
      const current = predecessorState.get(row.predecessor) ?? {
        appearances: 0,
        wins: 0,
      };
      predecessorState.set(row.predecessor, {
        appearances: current.appearances + 1,
        wins: current.wins + won,
      });
    }
    return output;
  });
}

function percentage(value: number, total: number): number {
  return (value / Math.max(1, total)) * 100;
}

export function summarizeWeighted(
  universe: CausalFeatureRow[],
  selected: CausalFeatureRow[],
  populationByOutcome: Record<Outcome, number>,
  fixedBetSol = 0.5
): WeightedMetrics {
  const sampleByOutcome = Object.fromEntries(
    (["loss", "small_win", "scalp_win", "runner"] as const).map((outcome) => [
      outcome,
      universe.filter((row) => row.outcome === outcome).length,
    ])
  ) as Record<Outcome, number>;
  const weight = (row: CausalFeatureRow): number =>
    populationByOutcome[row.outcome] /
    Math.max(1, sampleByOutcome[row.outcome]);
  const weightedRows = selected.reduce((sum, row) => sum + weight(row), 0);
  const weightedWinners = selected
    .filter((row) => row.roiPct > 0)
    .reduce((sum, row) => sum + weight(row), 0);
  const weightedRoi = selected.reduce(
    (sum, row) => sum + row.roiPct * weight(row),
    0
  );
  const grossWin = selected
    .filter((row) => row.roiPct > 0)
    .reduce((sum, row) => sum + row.roiPct * weight(row), 0);
  const grossLoss = Math.abs(
    selected
      .filter((row) => row.roiPct < 0)
      .reduce((sum, row) => sum + row.roiPct * weight(row), 0)
  );
  const scalpUniverse = universe
    .filter((row) => row.roiPct >= 10)
    .reduce((sum, row) => sum + weight(row), 0);
  const runnerUniverse = universe
    .filter((row) => row.roiPct >= 50)
    .reduce((sum, row) => sum + weight(row), 0);
  const lossUniverse = universe
    .filter((row) => row.roiPct < 0)
    .reduce((sum, row) => sum + weight(row), 0);
  const selectedScalps = selected
    .filter((row) => row.roiPct >= 10)
    .reduce((sum, row) => sum + weight(row), 0);
  const selectedRunners = selected
    .filter((row) => row.roiPct >= 50)
    .reduce((sum, row) => sum + weight(row), 0);
  const selectedLosses = selected
    .filter((row) => row.roiPct < 0)
    .reduce((sum, row) => sum + weight(row), 0);
  return {
    rows: selected.length,
    weightedRows,
    winners: selected.filter((row) => row.roiPct > 0).length,
    winRatePct: percentage(weightedWinners, weightedRows),
    medianRoiPct: median(selected.map((row) => row.roiPct)),
    avgRoiPct: weightedRows ? weightedRoi / weightedRows : null,
    profitFactor:
      grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? null : 0,
    equalBetPnlSol: (weightedRoi / 100) * fixedBetSol,
    scalpCapturePct: percentage(selectedScalps, scalpUniverse),
    runnerCapturePct: percentage(selectedRunners, runnerUniverse),
    loserRejectionPct: percentage(lossUniverse - selectedLosses, lossUniverse),
  };
}

export type PathPoint = {
  timestamp: number;
  marketCapUsd: number;
  signature?: string;
  transactionIndex?: number | null;
};

export type ExitRule = {
  id: string;
  takeProfitPct: number;
  stopLossPct: number;
  maxHoldSeconds: number;
};

export type ExitResult = {
  roiPct: number;
  grossRoiPct: number;
  reason: "take_profit" | "stop_loss" | "max_hold" | "no_path";
  holdSeconds: number;
};

export type TimedExitResult = ExitResult & {
  mint: string;
  entryMs: number;
  exitMs: number;
  quality: number;
  effectiveDelayMs?: number;
};

export type PortfolioLimits = {
  maxOpen: number;
  maxPerHour: number;
  maxSession: number;
};

export function applyPortfolioLimits(
  candidates: TimedExitResult[],
  limits: PortfolioLimits
): TimedExitResult[] {
  const ordered = [...candidates].sort(
    (a, b) =>
      a.entryMs - b.entryMs ||
      b.quality - a.quality ||
      a.mint.localeCompare(b.mint)
  );
  const accepted: TimedExitResult[] = [];
  const openExitTimes: number[] = [];
  const rollingOpenTimes: number[] = [];
  for (const candidate of ordered) {
    while (openExitTimes.length && openExitTimes[0]! <= candidate.entryMs) {
      openExitTimes.shift();
    }
    while (
      rollingOpenTimes.length &&
      rollingOpenTimes[0]! <= candidate.entryMs - 3_600_000
    ) {
      rollingOpenTimes.shift();
    }
    if (limits.maxSession > 0 && accepted.length >= limits.maxSession) break;
    if (limits.maxOpen > 0 && openExitTimes.length >= limits.maxOpen) continue;
    if (
      limits.maxPerHour > 0 &&
      rollingOpenTimes.length >= limits.maxPerHour
    ) {
      continue;
    }
    accepted.push(candidate);
    rollingOpenTimes.push(candidate.entryMs);
    const insertAt = openExitTimes.findIndex(
      (exitMs) => exitMs > candidate.exitMs
    );
    if (insertAt < 0) openExitTimes.push(candidate.exitMs);
    else openExitTimes.splice(insertAt, 0, candidate.exitMs);
  }
  return accepted;
}

export function simulateExit(
  entry: PathPoint,
  path: PathPoint[],
  rule: ExitRule,
  roundTripCostBps: number
): ExitResult {
  const ordered = path
    .filter(
      (point) =>
        point.marketCapUsd > 0 &&
        point.timestamp >= entry.timestamp &&
        point.timestamp <= entry.timestamp + rule.maxHoldSeconds
    )
    .sort(
      (a, b) =>
        a.timestamp - b.timestamp ||
        (a.transactionIndex ?? Number.MAX_SAFE_INTEGER) -
          (b.transactionIndex ?? Number.MAX_SAFE_INTEGER)
    );
  let last = entry;
  let reason: ExitResult["reason"] = ordered.length ? "max_hold" : "no_path";
  for (const point of ordered) {
    last = point;
    const roi = (point.marketCapUsd / entry.marketCapUsd - 1) * 100;
    if (roi >= rule.takeProfitPct) {
      reason = "take_profit";
      break;
    }
    if (roi <= rule.stopLossPct) {
      reason = "stop_loss";
      break;
    }
  }
  const observedRoiPct = (last.marketCapUsd / entry.marketCapUsd - 1) * 100;
  const grossRoiPct =
    reason === "take_profit"
      ? rule.takeProfitPct
      : reason === "stop_loss"
        ? rule.stopLossPct
        : observedRoiPct;
  const netMultiple =
    (1 + grossRoiPct / 100) * (1 - roundTripCostBps / 10_000);
  return {
    roiPct: (netMultiple - 1) * 100,
    grossRoiPct,
    reason,
    holdSeconds: Math.max(0, last.timestamp - entry.timestamp),
  };
}
