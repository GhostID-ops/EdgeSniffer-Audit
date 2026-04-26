import { PublicKey, LAMPORTS_PER_SOL } from "@solana/web3.js";
import { readConnection } from "./config.js";

const TOKEN_PROGRAM_IDS = [
  new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
  new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPu8NQkMZ6H4hWzPvyxC"),
];

/**
 * Fetch SOL balance for a public key (lamports and SOL).
 */
export async function getSolBalance(publicKey: PublicKey): Promise<{
  lamports: number;
  sol: number;
}> {
  const lamports = await readConnection.getBalance(publicKey);
  return {
    lamports,
    sol: lamports / LAMPORTS_PER_SOL,
  };
}

/**
 * Total raw balance of a given mint held by owner (summed across any ATAs).
 */
export async function getTokenBalance(
  owner: PublicKey,
  mint: string
): Promise<number> {
  let total = 0;

  for (const programId of TOKEN_PROGRAM_IDS) {
    try {
      const resp = await readConnection.getParsedTokenAccountsByOwner(owner, {
        programId,
      });
      for (const acct of resp.value) {
        const parsed = acct.account.data as
          | {
              parsed?: {
                info?: {
                  mint?: string;
                  tokenAmount?: { amount?: string };
                };
              };
            }
          | undefined;
        const info = parsed?.parsed?.info;
        if (info?.mint !== mint) continue;
        const amountStr = info.tokenAmount?.amount;
        if (typeof amountStr === "string") {
          total += parseInt(amountStr, 10);
        }
      }
    } catch {
      // Try the next token program.
    }
  }

  return total;
}

/**
 * Format SOL for display (e.g. "1.2345 SOL").
 */
export function formatSol(lamports: number): string {
  const sol = lamports / LAMPORTS_PER_SOL;
  return `${sol.toFixed(4)} SOL`;
}
