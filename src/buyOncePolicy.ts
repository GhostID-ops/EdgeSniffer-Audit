export function isPostBroadcastBuyUncertainty(reason: string): boolean {
  return /after (?:Rust )?broadcast|confirmation failed|confirmation-failed|signature=|Transaction failed/i.test(reason);
}

export function shouldHoldBuyOnceAfterFailure(args: {
  buyOncePerTokenEnabled: boolean;
  reason: string;
}): boolean {
  return args.buyOncePerTokenEnabled && isPostBroadcastBuyUncertainty(args.reason);
}
