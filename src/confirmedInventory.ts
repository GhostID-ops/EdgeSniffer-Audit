export type ConfirmedInventoryState = {
  slot: number;
  balanceRaw: number;
  signature: string;
  source: "transaction" | "snapshot";
};

type InventoryObservation = Omit<ConfirmedInventoryState, "source">;

export type ConfirmedInventoryLedger = {
  state: ConfirmedInventoryState | null;
  refreshFloorSlot: number;
};

export const emptyConfirmedInventoryLedger = (): ConfirmedInventoryLedger => ({
  state: null,
  refreshFloorSlot: 0,
});

function normalizedState(
  next: InventoryObservation,
  source: ConfirmedInventoryState["source"]
): ConfirmedInventoryState {
  return {
    slot: Math.max(0, Math.floor(next.slot)),
    balanceRaw: Math.max(0, Math.floor(next.balanceRaw)),
    signature: next.signature,
    source,
  };
}

export function applyConfirmedTransactionState(
  ledger: ConfirmedInventoryLedger,
  nextInput: InventoryObservation
): ConfirmedInventoryLedger {
  const next = normalizedState(nextInput, "transaction");
  const current = ledger.state;
  if (!current || next.slot > current.slot) {
    return { state: next, refreshFloorSlot: Math.max(ledger.refreshFloorSlot, next.slot) };
  }
  if (next.slot < current.slot) return ledger;
  if (current.source === "snapshot") return ledger;
  if (next.signature === current.signature) {
    return { state: next, refreshFloorSlot: Math.max(ledger.refreshFloorSlot, next.slot) };
  }

  // getTransaction does not expose transaction order within a slot. Require a
  // contextual account snapshot before this inventory can be spent safely.
  return {
    state: null,
    refreshFloorSlot: Math.max(ledger.refreshFloorSlot, next.slot),
  };
}

export function applyContextualInventorySnapshot(
  ledger: ConfirmedInventoryLedger,
  snapshot: InventoryObservation
): ConfirmedInventoryLedger {
  const next = normalizedState(snapshot, "snapshot");
  if (next.slot < ledger.refreshFloorSlot) return ledger;
  if (ledger.state && next.slot < ledger.state.slot) return ledger;
  return {
    state: next,
    refreshFloorSlot: next.slot,
  };
}

export function authoritativeBalanceRaw(ledger: ConfirmedInventoryLedger): number | null {
  return ledger.state?.balanceRaw ?? null;
}

export function cappedQueuedSellRaw(confirmedBalanceRaw: number, targetRaw: number): number {
  return Math.max(
    0,
    Math.min(
      Math.floor(Math.max(0, confirmedBalanceRaw)),
      Math.floor(Math.max(0, targetRaw))
    )
  );
}

export function shouldExpandQueuedSellAfterFailure(
  queuedSourceVersion: number,
  latest: { version: number; side: "buy" | "sell" } | undefined
): boolean {
  return !latest || latest.version <= queuedSourceVersion || latest.side === "sell";
}
