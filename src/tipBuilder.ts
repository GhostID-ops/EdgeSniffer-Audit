import {
  PublicKey,
  SystemProgram,
  type TransactionInstruction,
} from "@solana/web3.js";
import {
  JITO_TIP_ACCOUNTS,
  HELIUS_TIP_ACCOUNTS,
  HELIUS_TIP_MIN_LAMPORTS,
  tipLamports,
  useJitoDirect,
  useJitoFallback,
} from "./config.js";

/**
 * Build the tip transfer instruction(s) for a transaction.
 *
 * Jito and Helius Sender each enforce their own tip-account whitelist (zero overlap). So a
 * tx that needs to be acceptable to whichever sender path runs must include BOTH:
 *   - A Jito tip when USE_JITO_DIRECT=true   → buys MEV priority via Jito's auction.
 *   - A Helius tip when the Helius path may  → satisfies Helius's "min 200k lamports to one
 *     run, i.e. !USE_JITO_DIRECT or            of our wallets" check; pure infra fee, not MEV.
 *     USE_JITO_FALLBACK=true.
 *
 * The Helius tip stays at HELIUS_TIP_MIN_LAMPORTS (200k) when Jito is primary — paying full
 * tipLamports to both wallets would double our fee bleed for no extra priority. When Helius
 * is the only path, the full tipLamports goes there since that's the only validator paying
 * attention.
 */
export function buildTipInstructions(user: PublicKey): TransactionInstruction[] {
  const ixs: TransactionInstruction[] = [];

  if (useJitoDirect) {
    const jitoAccount = JITO_TIP_ACCOUNTS[Math.floor(Math.random() * JITO_TIP_ACCOUNTS.length)]!;
    ixs.push(
      SystemProgram.transfer({
        fromPubkey: user,
        toPubkey: new PublicKey(jitoAccount),
        lamports: tipLamports,
      })
    );
  }

  const heliusPathPossible = !useJitoDirect || useJitoFallback;
  if (heliusPathPossible) {
    const heliusAccount =
      HELIUS_TIP_ACCOUNTS[Math.floor(Math.random() * HELIUS_TIP_ACCOUNTS.length)]!;
    // When Jito is primary, only meet Helius's MIN — don't double-pay for priority Helius
    // wouldn't give anyway. When Helius is the only path, send the full configured tip.
    const heliusLamports = useJitoDirect
      ? HELIUS_TIP_MIN_LAMPORTS
      : Math.max(tipLamports, HELIUS_TIP_MIN_LAMPORTS);
    ixs.push(
      SystemProgram.transfer({
        fromPubkey: user,
        toPubkey: new PublicKey(heliusAccount),
        lamports: heliusLamports,
      })
    );
  }

  return ixs;
}
