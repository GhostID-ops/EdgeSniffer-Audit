export function isPostBroadcastBuyUncertainty(reason: string): boolean {
  return /after (?:Rust )?broadcast|confirmation failed|confirmation-failed|signature=|Transaction failed/i.test(reason);
}

/**
 * A CONFIRMED on-chain revert: the transaction was included in a block and failed
 * deterministically (InstructionError / Anchor custom error), so it moved no tokens
 * and spent no SOL beyond the base fee. Re-buying this mint is safe — the original
 * buy provably did not land. Distinct from delivery uncertainty (timeout / dropped /
 * sender error), where the tx may still confirm and a re-buy would double-buy.
 */
export function isConfirmedOnChainBuyFailure(reason: string): boolean {
  return /InstructionError|custom program error|Custom\(|Custom":|AnchorError|Error Code|Error Number/i.test(reason);
}

export function shouldHoldBuyOnceAfterFailure(args: {
  buyOncePerTokenEnabled: boolean;
  reason: string;
}): boolean {
  if (!args.buyOncePerTokenEnabled || !isPostBroadcastBuyUncertainty(args.reason)) return false;
  // Hold the buy-once gate for every post-broadcast outcome whose result is genuinely
  // UNKNOWN (timeout / dropped / sender error / bare "confirmation failed"): the tx may
  // still confirm, and releasing the gate here is exactly what let a second buy land when
  // the tracked wallet fires the same mint repeatedly. Only a CONFIRMED on-chain revert is
  // safe to release + retry, because that tx provably did not take effect. A genuinely
  // dropped tx becomes a cheap miss instead of a costly double buy.
  return !isConfirmedOnChainBuyFailure(args.reason);
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
