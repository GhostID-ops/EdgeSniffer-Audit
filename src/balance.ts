import { PublicKey, LAMPORTS_PER_SOL } from "@solana/web3.js";
import { readConnection } from "./config.js";

const TOKEN_PROGRAM_IDS = [
  new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
  new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
];
const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey(
  "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
);

/** Derive the ATA for (owner, mint, tokenProgram). Returns null on derivation failure. */
function deriveAta(owner: PublicKey, mint: string, tokenProgramId: PublicKey): PublicKey | null {
  try {
    const [ata] = PublicKey.findProgramAddressSync(
      [owner.toBuffer(), tokenProgramId.toBuffer(), new PublicKey(mint).toBuffer()],
      ASSOCIATED_TOKEN_PROGRAM_ID
    );
    return ata;
  } catch {
    return null;
  }
}

/**
 * Fast path: derive the ATA and read its balance directly. One account read instead of scanning
 * every token account the wallet owns. Tries original Token program first, then Token-2022.
 * Returns null if neither ATA exists or both reads fail — caller should fall back to getTokenBalance.
 */
export async function getTokenBalanceFast(
  owner: PublicKey,
  mint: string
): Promise<number | null> {
  for (const programId of TOKEN_PROGRAM_IDS) {
    const ata = deriveAta(owner, mint, programId);
    if (!ata) continue;
    try {
      const resp = await readConnection.getTokenAccountBalance(ata);
      const amount = resp?.value?.amount;
      if (typeof amount === "string") {
        const parsed = parseInt(amount, 10);
        if (Number.isFinite(parsed) && parsed >= 0) return parsed;
      }
    } catch {
      // ATA may not exist for this token program — try the next one.
    }
  }
  return null;
}

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
  let hadRpcSuccess = false;

  try {
    const resp = await readConnection.getParsedTokenAccountsByOwner(owner, {
      mint: new PublicKey(mint),
    });
    hadRpcSuccess = true;
    for (const acct of resp.value) {
      const parsed = acct.account.data as
        | {
            parsed?: {
              info?: {
                tokenAmount?: { amount?: string };
              };
            };
          }
        | undefined;
      const amountStr = parsed?.parsed?.info?.tokenAmount?.amount;
      if (typeof amountStr === "string") {
        total += parseInt(amountStr, 10);
      }
    }
    return total;
  } catch {
    // Fall back to program scans below for RPCs that do not support mint filters.
  }

  for (const programId of TOKEN_PROGRAM_IDS) {
    try {
      const resp = await readConnection.getParsedTokenAccountsByOwner(owner, {
        programId,
      });
      hadRpcSuccess = true;
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

  if (!hadRpcSuccess) {
    throw new Error(`Could not fetch token balance for ${mint}`);
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
