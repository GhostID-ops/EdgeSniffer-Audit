import { PublicKey } from "@solana/web3.js";

/** Pump.fun bonding-curve program (mainnet). */
export const PUMP_FUN_PROGRAM_ID = new PublicKey("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");

/** Trading-fee recipient. Read on-chain from the global account; this is the historical default. */
export const PUMP_FUN_FEE_RECIPIENT = new PublicKey("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM");

/** SPL Token program (classic). pump.fun bonding-curve mints use this, not Token-2022. */
export const TOKEN_PROGRAM_ID = new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");

/** Associated Token Account program. */
export const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");

/** SystemProgram, Rent sysvar — re-export for convenience. */
export const SYSTEM_PROGRAM_ID = new PublicKey("11111111111111111111111111111111");
export const RENT_SYSVAR_ID = new PublicKey("SysvarRent111111111111111111111111111111111");

/** Anchor discriminators (sha256("global:<name>")[0..8]). */
export const BUY_DISCRIMINATOR = Buffer.from([102, 6, 61, 18, 1, 218, 235, 234]);
export const SELL_DISCRIMINATOR = Buffer.from([51, 230, 133, 164, 1, 127, 131, 173]);

/** Pump.fun trading fee, in basis points (1% historically). */
export const PUMP_FUN_TRADE_FEE_BPS = 100;

/**
 * Derive the bonding curve PDA for a given mint.
 * Seeds: ["bonding-curve", mint].
 */
export function deriveBondingCurvePda(mint: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("bonding-curve"), mint.toBuffer()],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

/**
 * Derive the global config PDA. Seeds: ["global"]. Holds canonical fee_recipient and other params.
 */
export function deriveGlobalPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("global")],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

/**
 * Derive the event-authority PDA. Seeds: ["__event_authority"]. Required as readonly account on every ix.
 */
export function deriveEventAuthorityPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("__event_authority")],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

/** Derive the ATA address for (owner, mint) under the classic SPL Token program. */
export function deriveAta(owner: PublicKey, mint: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [owner.toBuffer(), TOKEN_PROGRAM_ID.toBuffer(), mint.toBuffer()],
    ASSOCIATED_TOKEN_PROGRAM_ID
  );
  return pda;
}
