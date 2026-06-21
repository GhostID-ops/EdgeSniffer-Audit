export type SourceSignalState = {
  side: "buy" | "sell";
  signature: string;
  version: number;
  observedAt: number;
};

export type PostSellRemainderIntent =
  | { kind: "source-full"; sourceSellVersion: number }
  | { kind: "force-full" };

export function shouldCancelPostSellRemainder(
  latest: SourceSignalState | undefined,
  intent: PostSellRemainderIntent
): boolean {
  if (intent.kind === "force-full") return false;
  return latest != null && latest.version > intent.sourceSellVersion && latest.side === "buy";
}
