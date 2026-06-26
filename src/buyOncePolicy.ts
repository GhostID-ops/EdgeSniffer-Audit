import { decideRetryAction } from "./executionRetry.js";

export function isPostBroadcastBuyUncertainty(reason: string): boolean {
  return /after (?:Rust )?broadcast|confirmation failed|confirmation-failed|signature=|Transaction failed/i.test(reason);
}

export function shouldHoldBuyOnceAfterFailure(args: {
  buyOncePerTokenEnabled: boolean;
  reason: string;
}): boolean {
  if (!args.buyOncePerTokenEnabled || !isPostBroadcastBuyUncertainty(args.reason)) return false;
  return decideRetryAction(args.reason).shouldQuarantine;
}
export function shouldBlockBuyOnceReentry(args: {
  buyOncePerTokenEnabled: boolean;
  allowRebuyAfterFlat: boolean;
  attemptedThisSession: boolean;
  boughtThisSession: boolean;
  heldRaw: number;
}): boolean {
  if (!args.buyOncePerTokenEnabled) return false;
  if (!args.attemptedThisSession && !args.boughtThisSession) return false;
  if (args.allowRebuyAfterFlat && args.heldRaw <= 0) return false;
  return true;
}
