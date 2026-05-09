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
};
