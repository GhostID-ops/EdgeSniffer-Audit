export type SourceSignalState = {
  side: "buy" | "sell";
  signature: string;
  version: number;
  observedAt: number;
};

export type PostSellRemainderIntent =
  | { kind: "source-full"; sourceSellVersion: number; confirmedPostBalanceRaw?: number }
  | { kind: "force-full"; confirmedPostBalanceRaw?: number };

export function shouldCancelPostSellRemainder(
  latest: SourceSignalState | undefined,
  intent: PostSellRemainderIntent
): boolean {
  if (intent.kind === "force-full") return false;
  return latest != null && latest.version > intent.sourceSellVersion && latest.side === "buy";
}

export function confirmedPostSellRemainderRaw(
  postBalanceRaw: number | null | undefined
): number {
  if (postBalanceRaw == null || !Number.isFinite(postBalanceRaw) || postBalanceRaw <= 0) {
    return 0;
  }
  return Math.floor(postBalanceRaw);
}
