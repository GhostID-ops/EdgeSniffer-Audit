/** A Jupiter swap detected from a tracked wallet's transaction. */
export type DetectedSwap = {
  signature: string;
  wallet: string;
  /** Input token mint (e.g. wrapped SOL). */
  inputMint: string;
  /** Output token mint. */
  outputMint: string;
  /** Amount in raw units (lamports for SOL). */
  amountRaw: number;
  /** True if input is native SOL (we clamp by min/max SOL). */
  isNativeInput: boolean;
  /**
   * Counterparty side amount in raw units. For a buy (SOL→token), this is the token amount the
   * source received. For a sell, the SOL the source received. Used to compute the source's effective
   * fill price for drift checks. Optional because not all extractors populate it.
   */
  counterpartyAmountRaw?: number;
  /** Decimals of the counterparty token. Needed alongside counterpartyAmountRaw for ratio math. */
  counterpartyDecimals?: number;
  /**
   * Base58 pubkeys of the source's pump.fun buy ix accounts, in order. When present, the bot
   * uses these directly (replacing only user-specific slots 5, 6, 13) instead of deriving and
   * fetching them. This is critical for Token-2022 pump.fun launches because slots fix1 (protocol
   * fee recipient) and REM0/REM1 (creator share + buyback recipient) rotate per-coin and aren't
   * derivable — they must come from the source tx, which isn't yet confirmable on hot launches.
   * Populated by the gRPC watcher when the source's tx contains a pump.fun buy ix. Undefined
   * for swaps from other detectors (WS) or non-pump.fun sources.
   */
  sourceIxAccounts?: string[];
  /**
   * Base58 pubkeys of the source's PumpSwap AMM buy ix accounts, in order. When present, the
   * graduated-token direct path can reuse the source's exact pool/global/fee accounts without
   * waiting for getTransaction to become available.
   */
  sourcePumpSwapAccounts?: string[];
  /** Original quote mint used by the source wallet when it differs from the mirrored input. */
  sourceQuoteMint?: string;
  /** Original quote amount used by the source wallet, in raw units. */
  sourceQuoteAmountRaw?: number;
};
