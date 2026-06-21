import { PublicKey, LAMPORTS_PER_SOL } from "@solana/web3.js";
import { readConnection } from "./config.js";

const TOKEN_PROGRAM_IDS = [
  new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
  new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
];
const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey(
  "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
);

export type WalletTokenBalance = {
  mint: string;
  amountRaw: number;
  tokenProgram: string;
};

export type WalletTokenBalanceSnapshot = {
  balancesByMint: Map<string, number>;
  successfulPrograms: number;
  failedPrograms: number;
};

export type ContextualTokenBalance = {
  amountRaw: number;
  contextSlot: number;
};

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
 * Read token inventory from a bank that has processed at least minContextSlot.
 * The raw account layout stores amount as u64 at offset 64 for both Token and
 * Token-2022 accounts. Mint-filtered owner queries can return multiple accounts,
 * so the result is summed instead of assuming one ATA.
 */
export async function getTokenBalanceAtOrAfterSlot(
  owner: PublicKey,
  mint: string,
  minContextSlot: number
): Promise<ContextualTokenBalance> {
  const response = await readConnection.getTokenAccountsByOwner(
    owner,
    { mint: new PublicKey(mint) },
    {
      commitment: "confirmed",
      minContextSlot: Math.max(0, Math.floor(minContextSlot)),
    }
  );

  let amountRaw = 0;
  for (const account of response.value) {
    const data = account.account.data;
    if (!Buffer.isBuffer(data) || data.length < 72) continue;
    const raw = data.readBigUInt64LE(64);
    if (raw > BigInt(Number.MAX_SAFE_INTEGER)) {
      throw new Error(`Token balance exceeds safe integer range for ${mint}`);
    }
    amountRaw += Number(raw);
  }

  return {
    amountRaw,
    contextSlot: response.context.slot,
  };
}

/**
 * Read every token balance for an owner with one account scan per token program.
 * Source-hold monitoring reuses this snapshot for all open positions instead of
 * issuing several ATA/account reads for every mint.
 */
export async function getWalletTokenBalanceSnapshot(
  owner: PublicKey
): Promise<WalletTokenBalanceSnapshot> {
  const balancesByMint = new Map<string, number>();
  let successfulPrograms = 0;
  let failedPrograms = 0;

  for (const programId of TOKEN_PROGRAM_IDS) {
    try {
      const resp = await readConnection.getParsedTokenAccountsByOwner(owner, {
        programId,
      });
      successfulPrograms++;
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
        const mint = info?.mint;
        const amountStr = info?.tokenAmount?.amount;
        if (!mint || typeof amountStr !== "string") continue;
        const amountRaw = parseInt(amountStr, 10);
        if (!Number.isFinite(amountRaw) || amountRaw <= 0) continue;
        balancesByMint.set(mint, (balancesByMint.get(mint) ?? 0) + amountRaw);
      }
    } catch {
      failedPrograms++;
    }
  }

  return { balancesByMint, successfulPrograms, failedPrograms };
}

/**
 * List all nonzero token mint balances held by owner, summed across token programs/accounts.
 */
export async function getWalletTokenBalances(owner: PublicKey): Promise<WalletTokenBalance[]> {
  const byMint = new Map<string, WalletTokenBalance>();

  for (const programId of TOKEN_PROGRAM_IDS) {
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
      const mint = info?.mint;
      const amountStr = info?.tokenAmount?.amount;
      if (!mint || typeof amountStr !== "string") continue;
      const amountRaw = parseInt(amountStr, 10);
      if (!Number.isFinite(amountRaw) || amountRaw <= 0) continue;
      const existing = byMint.get(mint);
      if (existing) {
        existing.amountRaw += amountRaw;
      } else {
        byMint.set(mint, {
          mint,
          amountRaw,
          tokenProgram: programId.toBase58(),
        });
      }
    }
  }

  return [...byMint.values()].sort((a, b) => b.amountRaw - a.amountRaw);
}

/**
 * Format SOL for display (e.g. "1.2345 SOL").
 */
export function formatSol(lamports: number): string {
  const sol = lamports / LAMPORTS_PER_SOL;
  return `${sol.toFixed(4)} SOL`;
}
