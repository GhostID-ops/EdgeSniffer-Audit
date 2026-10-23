export type SourceHoldZeroState = {
  misses: number;
  lastConfirmedSlot: number;
};

export type SourceHoldZeroObservation =
  | { counted: true; state: SourceHoldZeroState }
  | { counted: false; state: SourceHoldZeroState };

/**
 * Count a source-hold zero only when it came from a newer confirmed bank.
 * Polling the same stale RPC slot repeatedly must never trigger an exit.
 */
export function observeSourceHoldZero(
  previous: SourceHoldZeroState | undefined,
  confirmedSlot: number
): SourceHoldZeroObservation {
  const normalizedSlot = Math.max(0, Math.floor(confirmedSlot));
  const current = previous ?? { misses: 0, lastConfirmedSlot: -1 };
  if (normalizedSlot <= current.lastConfirmedSlot) {
    return { counted: false, state: current };
  }
  return {
    counted: true,
    state: {
      misses: current.misses + 1,
      lastConfirmedSlot: normalizedSlot,
    },
  };
}
