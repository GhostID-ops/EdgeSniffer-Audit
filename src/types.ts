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
};
